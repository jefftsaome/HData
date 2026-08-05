"""服务器时间校对：统一各采集端与平台服务器的时钟对齐。

背景（2026-08-05 streak9 事故）：平台 104 帧带 `serverTime`（服务器当前
毫秒时间戳）与 `countdownEndTime`（下注倒计时终点，同样服务器时钟）。
若客户机本地时钟与服务器偏差大（实测慢 100s+），倒计时换算会失真，
UI 永久显示"开牌中"；且对依赖服务器时间的签名/超时判断有影响。

本模块提供：
- `compute_offset(server_ms)`：服务器-本地时钟差（毫秒）
- `to_local(server_ms, offset)`：服务器时间戳 → 本地时钟时间戳
- `check_offset(server_ms, hard_limit, soft_limit)`：偏差检测，返回
  (severity, diff_ms)，由调用方决定警告/报错，本模块不擅自打日志
- `format_diff(diff_ms)`：人类可读差值（如 "慢 123.4s"）

不依赖网络/平台，纯换算；供 WS 采集层（104 帧）与业务层复用。
"""

from __future__ import annotations

import time

# 偏差阈值（毫秒）
SOFT_LIMIT_MS = 30_000       # 30s：仅警告，倒计时仍能换算
HARD_LIMIT_MS = 300_000      # 5min：可能致倒计时严重失真，报错级


def compute_offset(server_ms: int) -> int:
    """服务器时间 - 本地时间（毫秒）。正值=服务器比本地快。"""
    return int(server_ms) - int(time.time() * 1000)


def to_local(server_ms: int, offset: int) -> int:
    """服务器时钟时间戳 → 本地时钟时间戳（毫秒）。

    服务器 104 帧的 countdownEndTime 是服务器时钟；要拿到本地时钟的
    倒计时终点，用 `to_local(cd_end, offset)`。
    """
    return int(server_ms) - int(offset)


def check_offset(
    server_ms: int,
    hard_limit: int = HARD_LIMIT_MS,
    soft_limit: int = SOFT_LIMIT_MS,
) -> tuple[str, int]:
    """检查服务器时间偏差，返回 (severity, diff_ms)。

    Args:
        server_ms: 服务器时间戳（104 帧 serverTime）
        hard_limit: 报错阈值（默认 5min）
        soft_limit: 警告阈值（默认 30s）

    Returns:
        (severity, diff_ms)：severity ∈ {"ok", "warn", "error"}；
        diff_ms 为服务器-本地偏差绝对值。
    """
    diff = abs(compute_offset(server_ms))
    if diff >= hard_limit:
        return "error", diff
    if diff >= soft_limit:
        return "warn", diff
    return "ok", diff


def format_diff(diff_ms: int) -> str:
    """人类可读差值：如 "慢 123.4s" / "快 5.2s" / "一致"。

    diff_ms 为服务器-本地偏差（compute_offset 原值，可正可负）。
    """
    if abs(diff_ms) < 1000:
        return "一致"
    direction = "快" if diff_ms > 0 else "慢"
    return f"{direction} {abs(diff_ms) / 1000:.1f}s"
