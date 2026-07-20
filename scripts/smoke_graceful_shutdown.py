"""优雅退出 + 限流修复的冒烟驱动（一次性脚本）。

以 --min 99 跑 amain（无候选触发，纯启动+空转），75s 后取消主任务
模拟 Ctrl+C 的 CancelledError 路径，观察：
  1. 监控账号全部缓存登录、刷新节流生效（无 linbing1 打码死循环）；
  2. TableMonitor 10 分片一次就绪；
  3. _graceful_shutdown 完整执行（离桌断连、关库、汇总行）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from scripts.streak_hunter import amain


async def wrapper():
    task = asyncio.create_task(amain(99, "data/shutdown_smoke.db", 0, "", 10))
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
