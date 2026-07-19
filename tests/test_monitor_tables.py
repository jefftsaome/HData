"""monitor_tables 分片策略单元测试（不触网）。

覆盖行为：
  - 空 tables + 多账号：每个账号（含当前登录账号）各建一个分片，
    保证后续 add_table() 能均衡分配到全部账号；
  - 常规用法（单账号多桌）：仅一个分片，桌台全在该分片；
  - add_table() 动态加桌按负载最小分片轮询分配。
"""
from __future__ import annotations

import pytest

import hdata.client as hc
from hdata.client import GameClient


class _FakeConn:
    """替代 _WSConnection：只记录 send，不建立真实连接。"""

    def __init__(self, session, on_before_connect=None):
        self._session = session
        self._player_id = session.get("game_player_id", 1)
        self.device_id = "fake-device"
        self.sent: list[dict] = []

    async def send(self, msg):
        self.sent.append(msg)


def _make_client(monkeypatch, extra_accounts: int = 0) -> GameClient:
    client = GameClient(entry_url="https://example.com")
    client._session = {"game_token": "t0", "game_player_id": 1}
    client._account = "acc0"
    client._password = "p0"

    async def fake_session_login(account, password="", **kw):
        return {"game_token": f"t-{account}",
                "game_player_id": abs(hash(account)) % 10000}

    monkeypatch.setattr(hc, "_session_login", fake_session_login)
    monkeypatch.setattr(hc, "_WSConnection", _FakeConn)
    return client


async def test_empty_tables_creates_shard_per_account(monkeypatch):
    """空 tables + 3 个额外账号 → 4 个分片（含当前账号）。"""
    client = _make_client(monkeypatch)
    accounts = [{"account": f"a{i}", "password": "p"} for i in range(3)]
    mon = await client.monitor_tables([], accounts=accounts)
    assert len(mon._shards) == 4
    assert all(len(s._tables) == 0 for s in mon._shards)


async def test_add_table_balances_across_shards(monkeypatch):
    """add_table 把桌台轮询分配到负载最小的分片。"""
    client = _make_client(monkeypatch)
    accounts = [{"account": f"a{i}", "password": "p"} for i in range(3)]
    mon = await client.monitor_tables([], accounts=accounts)
    for i in range(3):
        await mon.add_table({"table_id": 100 + i, "game_type_id": 2001})
    sizes = sorted(len(s._tables) for s in mon._shards)
    assert sizes == [0, 1, 1, 1]
    # 每个分到桌的分片都通过连接发出了进桌指令
    entered = sum(1 for s in mon._shards if s._conn.sent)
    assert entered == 3
    assert sorted(mon.table_ids) == [100, 101, 102]


async def test_single_account_all_tables_one_shard(monkeypatch):
    """不传 accounts：全部桌台压当前账号一条连接（兼容旧行为）。"""
    client = _make_client(monkeypatch)
    tables = [{"table_id": 200 + i, "game_type_id": 2001} for i in range(3)]
    mon = await client.monitor_tables(tables)
    assert len(mon._shards) == 1
    assert len(mon._shards[0]._tables) == 3


async def test_tables_round_robin_over_accounts(monkeypatch):
    """初始 tables 轮询分组；空组账号仍有分片可接动态桌。"""
    client = _make_client(monkeypatch)
    accounts = [{"account": "b1", "password": "p"}]
    tables = [{"table_id": 300 + i, "game_type_id": 2001} for i in range(2)]
    mon = await client.monitor_tables(tables, accounts=accounts)
    assert len(mon._shards) == 2
    assert [len(s._tables) for s in mon._shards] == [1, 1]
    # 再加两桌 → 每分片各 2 桌
    await mon.add_table({"table_id": 400, "game_type_id": 2001})
    await mon.add_table({"table_id": 401, "game_type_id": 2001})
    assert [len(s._tables) for s in mon._shards] == [2, 2]
