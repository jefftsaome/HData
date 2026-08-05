"""hdata/time.py 时间校对模块特征测试。

背景（2026-08-05 streak9 事故）：服务器比本地慢 100s+，104 帧
serverTime/countdownEndTime 换算失真。本模块统一校对逻辑。
"""
import time

from hdata.time import (
    HARD_LIMIT_MS,
    SOFT_LIMIT_MS,
    check_offset,
    compute_offset,
    format_diff,
    to_local,
)


def test_compute_offset_positive_when_server_ahead():
    """服务器比本地快 → offset 为正。"""
    now = int(time.time() * 1000)
    off = compute_offset(now + 5000)
    assert off == 5000  # 同一时刻内,time.time 最多差几 ms,容差放宽
    assert abs(off - 5000) < 100


def test_compute_offset_negative_when_server_behind():
    now = int(time.time() * 1000)
    off = compute_offset(now - 5000)
    assert abs(off - (-5000)) < 100


def test_to_local_converts_server_clock_to_local():
    """服务器 countdownEndTime → 本地时钟终点。"""
    now = int(time.time() * 1000)
    offset = -120_000                 # 服务器比本地慢 120s
    server_now = now + offset         # 同一时刻的服务器时钟戳
    server_cd = server_now + 15_000   # 服务器视角还有 15s
    local_cd = to_local(server_cd, offset)
    # 本地视角应也是约 15s 后(换算消掉时钟差)
    assert abs((local_cd - now) - 15_000) < 100


def test_check_offset_severity():
    assert check_offset(int(time.time() * 1000))[0] == "ok"
    assert check_offset(int(time.time() * 1000) + 60_000)[0] == "warn"
    assert check_offset(int(time.time() * 1000) + 600_000)[0] == "error"


def test_check_offset_returns_diff():
    sev, diff = check_offset(int(time.time() * 1000) + 60_000)
    assert sev == "warn"
    assert diff >= SOFT_LIMIT_MS and diff < HARD_LIMIT_MS


def test_format_diff():
    assert format_diff(0) == "一致"
    assert format_diff(500) == "一致"          # <1s
    assert "快" in format_diff(123_400)
    assert "123.4s" in format_diff(123_400)
    assert "慢" in format_diff(-123_400)
    assert "123.4s" in format_diff(-123_400)
