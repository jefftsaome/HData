"""优雅退出 + 限流修复的冒烟驱动（一次性脚本）。

以 min_streak=99 跑 run_strategy（无候选触发，纯启动+空转），75s 后
取消主任务模拟 Ctrl+C 的 CancelledError 路径，观察：
  1. 监控账号全部缓存登录、刷新节流生效（无 linbing1 打码死循环）；
  2. TableMonitor 10 分片一次就绪；
  3. _graceful_shutdown 完整执行（离桌断连、关库、汇总行）。
"""
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "hsys" / "crawl-bot"))

from loguru import logger
from strategies.streak import run_strategy

_cfg = json.loads((_ROOT / "hsys" / "crawl-bot" / "config.json")
                  .read_text(encoding="utf-8"))
_cfg.update({"db_path": str(_ROOT / "data" / "shutdown_smoke.db"),
             "min_streak": 99, "purge_raw_days": 0,
             "proxies": ""})


async def wrapper():
    task = asyncio.create_task(run_strategy(_cfg))
    await asyncio.sleep(75)
    logger.info("[冒烟] 75s 到，取消主任务（模拟 Ctrl+C）…")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | {message}")
    asyncio.run(wrapper())
