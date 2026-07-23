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


# ── kick_policy="rotate"（被踢换账号重进） ──

import asyncio
import json


class _KickConn(_FakeConn):
    """带服务器推送帧队列的假连接（不建真实连接）。"""

    def __init__(self, session, on_before_connect=None):
        super().__init__(session, on_before_connect)
        self.frames: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def recv(self):
        try:
            return await asyncio.wait_for(self.frames.get(), timeout=0.5)
        except asyncio.TimeoutError:
            return None


def _kick_frame(table_id: int) -> dict:
    """模拟服务器 102 踢出推送（leaveTableType=2 长时间未下注）。"""
    return {"protocolId": 102,
            "jsonData": json.dumps({"param": json.dumps(
                {"tableId": table_id, "leaveTableType": 2})})}


async def test_rotate_shard_drops_and_reports_kick(monkeypatch):
    """rotate：分片收到踢出推送 → 摘除该桌并上报（含账号/桌信息），
    空分片保活不终止迭代。"""
    client = _make_client(monkeypatch)
    monkeypatch.setattr(hc, "_WSConnection", _KickConn)
    sess = await client.enter_tables(
        [{"table_id": 100, "game_type_id": 2001}], kick_policy="rotate")
    await sess.__aenter__()
    assert 100 in sess._entered

    sess._conn.frames.put_nowait(_kick_frame(100))
    agen = sess.events()
    ev = await agen.__anext__()
    assert ev["type"] == "kick" and ev["table_id"] == 100
    assert ev["data"]["dropped"] is True
    assert ev["data"]["table"]["table_id"] == 100
    assert "account" in ev["data"]
    assert sess._tables == []                     # 已摘除
    # 空分片保活：迭代不结束（超时而非 StopAsyncIteration）
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(agen.__anext__(), timeout=1.2)
    await agen.aclose()


async def test_follow_system_still_terminates_when_empty(monkeypatch):
    """follow_system 回归：全桌被踢后迭代正常结束。"""
    client = _make_client(monkeypatch)
    monkeypatch.setattr(hc, "_WSConnection", _KickConn)
    sess = await client.enter_tables(
        [{"table_id": 100, "game_type_id": 2001}],
        kick_policy="follow_system")
    await sess.__aenter__()

    sess._conn.frames.put_nowait(_kick_frame(100))
    agen = sess.events()
    ev = await agen.__anext__()
    assert ev["data"]["dropped"] is True
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


async def test_table_monitor_rotates_kicked_table_to_other_shard(monkeypatch):
    """TableMonitor 轮转：A 分片被踢的桌换到 B 分片重进，事件标注账号。"""
    client = _make_client(monkeypatch)
    client._session["account"] = "acc0"
    monkeypatch.setattr(hc, "_WSConnection", _KickConn)
    mon = await client.monitor_tables(
        [], accounts=[{"account": "a0", "password": "p"}],
        kick_policy="rotate")
    src, other = mon._shards[0], mon._shards[1]
    await mon.add_table({"table_id": 100, "game_type_id": 2001})
    assert 100 in [t["table_id"] for t in src._tables]   # 负载均衡到首分片

    src._conn.frames.put_nowait(_kick_frame(100))
    agen = mon.events()
    ev = await asyncio.wait_for(agen.__anext__(), timeout=2)
    assert ev["type"] == "kick" and ev["table_id"] == 100
    assert ev["data"]["action"] == "rotated"
    assert ev["data"]["dropped"] is False
    assert ev["data"]["from_account"] == "acc0"
    assert ev["data"]["to_account"] == "a0"
    assert src._tables == []                             # 源分片已摘除
    assert [t["table_id"] for t in other._tables] == [100]  # 换到 B 分片
    assert any("tableId" in str(m) or m for m in other._conn.sent)  # B 发出进桌
    await agen.aclose()


async def test_rotate_fallback_single_shard_reenter_same_account(monkeypatch):
    """仅一个存活分片：退回同账号重进（保监控连续性），事件带 note。"""
    client = _make_client(monkeypatch)
    client._session["account"] = "acc0"
    monkeypatch.setattr(hc, "_WSConnection", _KickConn)
    mon = await client.monitor_tables(
        [{"table_id": 100, "game_type_id": 2001}],
        kick_policy="rotate")
    await mon.__aenter__()          # 建连并进桌（填充 _entered）
    src = mon._shards[0]

    src._conn.frames.put_nowait(_kick_frame(100))
    agen = mon.events()
    ev = await asyncio.wait_for(agen.__anext__(), timeout=2)
    assert ev["data"]["action"] == "auto_reenter"
    assert ev["data"]["to_account"] == "acc0"
    assert "note" in ev["data"]
    assert [t["table_id"] for t in src._tables] == [100]  # 桌回到原分片
    await agen.aclose()
