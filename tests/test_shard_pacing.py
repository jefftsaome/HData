"""TableMonitor 分片建连的按出口分组限速测试（不触网）。

2026-07-27 起：同一代理出口的分片串行、间隔 connect_interval_s
（默认 18s）；不同出口并行建连；无代理（直连）全部视为同一组。
测试用 RFC 5737 文档保留假代理地址。
"""
from __future__ import annotations

import asyncio

import hdata.client as hc
from hdata.client import TableMonitor

PX1 = "http://u:p@203.0.113.11:8011"      # 出口组 1（假地址）
PX2 = "http://u:p@203.0.113.22:8011"      # 出口组 2（假地址）

ORDER: list[str] = []


class _PaceConn:
    """替代 _WSConnection：记录建连顺序，不建真实连接。"""

    def __init__(self, proxy: str, name: str):
        self._session = {"proxy": proxy, "account": name}
        self._player_id = 1
        self.device_id = "d"

    async def __aenter__(self):
        ORDER.append(self._session["account"])
        return self

    async def __aexit__(self, *exc):
        return None

    async def send(self, msg):
        pass


def _shard(proxy: str, name: str) -> hc.MultiTableSession:
    return hc.MultiTableSession(_PaceConn(proxy, name), [],
                                kick_policy="stay")


async def test_per_exit_grouped_pacing(monkeypatch):
    """2+2+1 分片分属 2 出口 + 直连：组内串行限速、组间并行。"""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleeps.append(d)
        await real_sleep(0)      # 让出循环，模拟真实等待的调度效果

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    ORDER.clear()
    shards = [_shard(PX1, "a1"), _shard(PX1, "a2"),   # 出口1 组
              _shard(PX2, "b1"), _shard(PX2, "b2"),   # 出口2 组
              _shard("", "c1")]                        # 直连组
    mon = TableMonitor(shards, lambda: None, connect_interval_s=18)
    await mon.__aenter__()

    # 组内各睡 (n-1) 次：1+1+0 = 2 次，间隔 18s
    assert sleeps == [18, 18]
    # 并行证据：三个组的"首片"都在任何"次片"之前建连
    assert set(ORDER[:3]) == {"a1", "b1", "c1"}
    assert set(ORDER[3:]) == {"a2", "b2"}
    assert len(mon._shards) == 5


async def test_single_group_serial(monkeypatch):
    """全部直连（无代理）= 同一组：逐片串行，n-1 次间隔。"""
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    ORDER.clear()
    shards = [_shard("", f"a{i}") for i in range(3)]
    mon = TableMonitor(shards, lambda: None, connect_interval_s=7)
    await mon.__aenter__()
    assert sleeps == [7, 7]
    assert ORDER == ["a0", "a1", "a2"]


def test_default_interval_is_18s():
    mon = TableMonitor([], lambda: None, connect_interval_s=0)
    assert mon._connect_interval_s == 18.0 == hc._SHARD_CONNECT_INTERVAL_S
    mon2 = TableMonitor([], lambda: None, connect_interval_s=5)
    assert mon2._connect_interval_s == 5
