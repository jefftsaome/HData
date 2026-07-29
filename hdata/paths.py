"""hdata 用户数据目录定位（缓存/会话/设备指纹/浏览器 profile）。

固定落在用户 home 目录（``~/.hdata/cache``），Windows / Linux / macOS
同一约定，与包的安装位置彻底解耦——venv 重建、包升级、多仓库克隆
都不影响已持久化的会话与设备指纹。

可用环境变量 ``HDATA_HOME`` 覆盖根目录（测试或多实例隔离用）。
"""
from __future__ import annotations

import os
from pathlib import Path


def hdata_home() -> Path:
    """hdata 用户数据根目录（默认 ``~/.hdata``）。"""
    override = os.environ.get("HDATA_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hdata"


def cache_dir() -> Path:
    """缓存目录（默认 ``~/.hdata/cache``），不保证已存在。"""
    return hdata_home() / "cache"
