"""StreakHunter 入场复核与启动即登录行为单测（不触网）。

覆盖行为：
  - 入场复核：连胜已断 → 跳过不进桌、不开 episode；
  - 入场复核：连胜仍在但长度变化 → 以最新 side/length 开 episode；
  - 入场复核：已反转成反向连胜 → 按新方向入场并重算 via；
  - 入场复核通过且与候选一致 → 正常进桌；
  - run() 启动即建监控（不等首个候选到达）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts.streak_hunter import StreakMonitor


def _make_monitor(min_streak: int = 4) -> StreakMonitor:
    creds = [{"account": "m1", "password": "x"},
             {"account": "m2", "password": "y"}]
    store = Mock()
    store.open_episode.return_value = 42
    watcher = SimpleNamespace(_flat={}, good_roads={},
                              active=set(), cooldown={})
    sm = StreakMonitor(creds, store, watcher, min_streak)
    sm.mon = SimpleNamespace(add_table=AsyncMock(),
                             leave_table=AsyncMock())
    return sm


def _cand(tid: int = 2001, side: str = "B", length: int = 5) -> dict:
    return {"table_id": tid, "game_type_id": 2001,
            "table_name": "经典百家乐H08",
            "side": side, "length": length, "via": "local_streak"}


async def test_open_episode_skip_when_streak_broken():
    sm = _make_monitor()
    sm._watcher._flat[2001] = "BBBBBPP"          # 已反，末尾 P×2 < 4
    await sm._open_episode(_cand(side="B", length=5))
    sm.mon.add_table.assert_not_awaited()
    sm._store.open_episode.assert_not_called()
    assert 2001 not in sm.episodes
    assert 2001 not in sm._watcher.active


async def test_open_episode_realigns_length():
    sm = _make_monitor()
    sm._watcher._flat[2001] = "PBBBBBBB"         # 最新 B×7（候选时 B×5）
    await sm._open_episode(_cand(side="B", length=5))
    sm.mon.add_table.assert_awaited_once()
    kw = sm._store.open_episode.call_args[0][0]
    assert kw["side"] == "B" and kw["start_length"] == 7
    ep = sm.episodes[2001]
    assert ep.side == "B" and ep.length == 7


async def test_open_episode_follows_reversal():
    sm = _make_monitor()
    sm._watcher._flat[2001] = "BBBBBPPPP"        # 已反转成 P×4
    sm._watcher.good_roads[2001] = ["长闲"]
    await sm._open_episode(_cand(side="B", length=5))
    kw = sm._store.open_episode.call_args[0][0]
    assert kw["side"] == "P" and kw["start_length"] == 4
    assert kw["detected_via"] == "good_roads"    # via 按新方向重算
    assert sm.episodes[2001].side == "P"


async def test_open_episode_normal_when_consistent():
    sm = _make_monitor()
    sm._watcher._flat[2001] = "PTBBBBB"          # B×5（夹 T 不计），一致
    await sm._open_episode(_cand(side="B", length=5))
    sm.mon.add_table.assert_awaited_once_with(
        {"table_id": 2001, "game_type_id": 2001})
    kw = sm._store.open_episode.call_args[0][0]
    assert kw["side"] == "B" and kw["start_length"] == 5
    assert 2001 in sm._watcher.active


async def test_run_ensures_monitor_eagerly():
    """无候选时 run() 也应立即建监控并进入服务循环。"""
    sm = _make_monitor()
    sm.mon = None                                # 模拟未初始化

    async def fake_ensure():
        sm.mon = SimpleNamespace()

    sm.ensure_monitor = fake_ensure
    serve_entered = asyncio.Event()

    async def fake_serve(candidates):
        serve_entered.set()
        await asyncio.Event().wait()             # 永不返回

    sm._serve = fake_serve
    task = asyncio.create_task(sm.run(asyncio.Queue()))
    await asyncio.wait_for(serve_entered.wait(), 1.0)
    assert sm.mon is not None                    # 未等候选即完成建监控
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_graceful_shutdown_cleans_up():
    """取消任务被等待收尾；监控 shutdown 与关库按序执行。"""
    from scripts.streak_hunter import _graceful_shutdown

    monitor = Mock()
    monitor.stats = {"rounds": 1, "broke": 0, "censored": 2}
    monitor.shutdown = AsyncMock()
    store = Mock()

    async def sleeper():
        await asyncio.sleep(999)

    tasks = [asyncio.create_task(sleeper()) for _ in range(2)]
    await _graceful_shutdown(tasks, monitor, store,
                             asyncio.get_running_loop().time())
    assert all(t.cancelled() for t in tasks)
    monitor.shutdown.assert_awaited_once()
    store.commit.assert_called_once()
    store.close.assert_called_once()


async def test_graceful_shutdown_store_closed_on_monitor_error():
    """监控清理抛异常时，关库仍然执行（数据完整性优先）。"""
    from scripts.streak_hunter import _graceful_shutdown

    monitor = Mock()
    monitor.stats = {"rounds": 0, "broke": 0, "censored": 0}
    monitor.shutdown = AsyncMock(side_effect=RuntimeError("boom"))
    store = Mock()
    await _graceful_shutdown([], monitor, store,
                             asyncio.get_running_loop().time())
    store.commit.assert_called_once()
    store.close.assert_called_once()
