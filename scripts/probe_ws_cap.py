"""WS 并发连接上限探针（诊断工具）。

场景：同一出口 IP 下，平台允许同时保持多少条游戏 WS 连接？
按"1 条大厅连接 + N 条监控分片"的真实结构逐条建连（3s 间隔），
记录第几条开始被 403 拒绝，最后全部干净关闭。

用法:
    uv run python scripts/probe_ws_cap.py --config <config.json路径> [监控账号数，默认 6]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

import hdata.client as hc
from hdata.client import GameClient

_ap = argparse.ArgumentParser(description="WS 并发连接上限探针（账号配置来自 crawl-bot config.json）")
_ap.add_argument("--config", required=True,
                 help="账号配置 JSON 路径（含 accounts/entry_url/geepass_token/jfbym_token）")
_args, _ = _ap.parse_known_args()
_cfg = json.loads(Path(_args.config).read_text(encoding="utf-8"))
ACCOUNTS = _cfg["accounts"]
ENTRY_URL = _cfg["entry_url"]
GEEPASS = _cfg["geepass_token"]
JFBYM = _cfg["jfbym_token"]


async def main(n_monitor: int = 6, interval: float = 3.0):
    conns: list[hc._WSConnection] = []
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)

    async def open_one(acc: dict, tag: str) -> bool:
        sess = await hc._session_login(
            acc["account"], acc["password"], entry_url=ENTRY_URL,
            geepass_token=GEEPASS, jfbym_token=JFBYM)
        conn = hc._WSConnection(sess,              # 与生产一致的刷新回调
                                on_before_connect=client._make_refresh_cb())
        t0 = time.time()
        try:
            await conn.__aenter__()
        except Exception as e:
            logger.error(f"[{tag}] {acc['account']} 连接失败: {e}")
            return False
        conns.append(conn)
        logger.info(f"[{tag}] {acc['account']} 连接成功 "
                    f"({time.time() - t0:.1f}s)，当前存活 {len(conns)} 条")
        return True

    # 1. 大厅连接（发现层账号）
    if not await open_one(ACCOUNTS[0], "大厅"):
        logger.error("大厅连接即失败，终止探测")
        return
    await asyncio.sleep(interval)

    # 2. 逐条叠加监控分片连接
    fails = 0
    for i, acc in enumerate(ACCOUNTS[1:1 + n_monitor], 1):
        ok = await open_one(acc, f"分片{i}")
        if not ok:
            fails += 1
            if fails >= 2:
                logger.warning("连续 2 次失败，判定已达上限")
                break
        await asyncio.sleep(interval)

    logger.info(f"探测结束：存活 {len(conns)} 条连接"
                f"（1 大厅 + {len(conns) - 1} 监控分片），失败 {fails} 次")

    # 3. 干净关闭
    for c in conns:
        try:
            await c.__aexit__(None, None, None)
        except Exception:
            pass
    logger.info("全部连接已关闭")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")
    asyncio.run(main(n))
