"""每账号进桌节奏器（_EnterPacer）单元测试（不触网）。

覆盖行为：
  - 分片首条进桌指令立即发送；
  - 之后逐条串行，间隔落在 interval ± jitter 区间（含计时器容差）；
  - urgent 插队队头，但仍受最小间隔约束；
  - 两个分片（账号）的节奏器互不阻塞、天然并行；
  - 多路并发 request_enter 收敛为单 worker 串行发送；
  - 节奏器未 start（未进 __aenter__）时降级同步直发（旧语义兼容）；
  - stop 撤销未发队列。

小尺度真 sleep：interval 0.06~0.1s 量级，含 Windows 计时器容差。
"""
from __future__ import annotations

import asyncio
import random
import re
import time

import pytest

import hdata.client as hc


class _PacerConn:
    """替代 _WSConnection：记录 (时刻, 消息)，不建立真实连接。"""

    def __init__(self, account: str = "acc"):
        self._session = {"account": account, "game_player_id": 1}
        self._player_id = 1
        self.device_id = "fake-device"
        self.sent: list[tuple[float, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send(self, msg):
        self.sent.append((time.monotonic(), msg))


def _tid(msg) -> int:
    m = re.search(r"['\"]tableId['\"]: (\d+)", str(msg))
    return int(m.group(1)) if m else -1


def _make_session(interval: float = 0.1, jitter: float = 0.02,
                  seed: int = 0, account: str = "acc"):
    conn = _PacerConn(account)
    sess = hc.MultiTableSession(conn, [], kick_policy="rotate",
                                readd_interval_s=interval,
                                readd_jitter_s=jitter,
                                rng=random.Random(seed))
    return sess, conn


def _table(tid: int) -> dict:
    return {"table_id": tid, "game_type_id": 2001}


async def test_first_table_sent_immediately():
    """分片首条指令不等间隔，立即发送。"""
    sess, conn = _make_session()
    await sess.__aenter__()
    try:
        await sess.request_enter(_table(1))
        await asyncio.sleep(0.03)          # 远小于首个间隔 0.1s
        assert [_tid(m) for _, m in conn.sent] == [1]
    finally:
        await sess.__aexit__(None, None, None)


async def test_serial_interval_within_bounds():
    """首条之后逐条串行，间隔落在 interval ± jitter（含计时器容差）。"""
    interval, jitter = 0.1, 0.02
    sess, conn = _make_session(interval, jitter)
    await sess.__aenter__()
    try:
        for i in range(3):
            await sess.request_enter(_table(i + 1))
        t0 = time.monotonic()
        while len(conn.sent) < 3 and time.monotonic() - t0 < 3:
            await asyncio.sleep(0.01)
        assert len(conn.sent) == 3
        gaps = [conn.sent[i + 1][0] - conn.sent[i][0] for i in range(2)]
        for g in gaps:
            assert g >= interval - jitter - 0.02   # 下界（容计时器早醒）
            assert g <= interval + jitter + 0.15   # 上界（容 Windows 睡眠粒度）
    finally:
        await sess.__aexit__(None, None, None)


async def test_urgent_jumps_queue_but_respects_min_gap():
    """urgent 插队队头先发，但距上一条仍守最小间隔。"""
    interval, jitter = 0.1, 0.02
    sess, conn = _make_session(interval, jitter)
    await sess.__aenter__()
    try:
        await sess.request_enter(_table(1))
        while not conn.sent:                       # 等首条发出
            await asyncio.sleep(0.005)
        await sess.request_enter(_table(2))                    # normal
        await sess.request_enter(_table(3))                    # normal
        await sess.request_enter(_table(9), urgent=True)       # 插队
        t0 = time.monotonic()
        while len(conn.sent) < 4 and time.monotonic() - t0 < 3:
            await asyncio.sleep(0.01)
        order = [_tid(m) for _, m in conn.sent]
        assert order == [1, 9, 2, 3]               # 9 插队到队头
        gap = conn.sent[1][0] - conn.sent[0][0]
        assert gap >= interval - jitter - 0.02     # urgent 不提前
    finally:
        await sess.__aexit__(None, None, None)


async def test_pacers_independent_parallel():
    """两个账号的节奏器互不阻塞：各 3 桌的耗时 ≈ 单账号 3 桌，
    而不是全局串行 6 桌。"""
    interval, jitter = 0.1, 0.02
    sa, ca = _make_session(interval, jitter, account="a")
    sb, cb = _make_session(interval, jitter, account="b")
    await sa.__aenter__()
    await sb.__aenter__()
    try:
        t0 = time.monotonic()
        for i in range(3):
            await sa.request_enter(_table(i + 1))
            await sb.request_enter(_table(101 + i))
        while (len(ca.sent) < 3 or len(cb.sent) < 3) \
                and time.monotonic() - t0 < 3:
            await asyncio.sleep(0.01)
        wall = time.monotonic() - t0
        assert len(ca.sent) == 3 and len(cb.sent) == 3
        # 并行 ≈ 2 个间隔；全局串行 6 桌 ≈ 5 个间隔。取中间值判定。
        assert wall < 3.5 * interval
    finally:
        await sa.__aexit__(None, None, None)
        await sb.__aexit__(None, None, None)


async def test_concurrent_push_converges_serial():
    """多路并发 request_enter（模拟铺桌/补桌/rotate/接管同时到达）：
    同一账号只由一个 worker 串行发出，间隔全部达标。"""
    interval, jitter = 0.06, 0.01
    sess, conn = _make_session(interval, jitter)
    await sess.__aenter__()
    try:
        await asyncio.gather(*(
            sess.request_enter(_table(i + 1), urgent=(i % 2 == 0))
            for i in range(5)))
        t0 = time.monotonic()
        while len(conn.sent) < 5 and time.monotonic() - t0 < 3:
            await asyncio.sleep(0.01)
        assert len(conn.sent) == 5
        assert sorted(_tid(m) for _, m in conn.sent) == [1, 2, 3, 4, 5]
        gaps = [conn.sent[i + 1][0] - conn.sent[i][0] for i in range(4)]
        for g in gaps:
            assert g >= interval - jitter - 0.02
    finally:
        await sess.__aexit__(None, None, None)


async def test_direct_fallback_when_not_started():
    """未进 __aenter__（节奏器未 start）的直驱用法：同步直发，
    保持旧语义（单元测试/简单脚本兼容）。"""
    sess, conn = _make_session()
    await sess.request_enter(_table(7))            # 入队前就发出
    assert [_tid(m) for _, m in conn.sent] == [7]
    assert not sess._pacer.started


async def test_stop_discards_pending():
    """__aexit__ 停节奏器并丢弃未发队列。"""
    sess, conn = _make_session()
    await sess.__aenter__()
    for i in range(4):
        await sess.request_enter(_table(i + 1))
    await asyncio.sleep(0.03)                      # 首条发出即停
    await sess.__aexit__(None, None, None)
    sent_at_stop = len(conn.sent)
    assert 1 <= sent_at_stop < 4
    await asyncio.sleep(0.3)                       # 之后不再有发送
    assert len(conn.sent) == sent_at_stop
    assert sess._pacer.pending == 0
