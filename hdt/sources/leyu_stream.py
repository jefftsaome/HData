"""StreamSource — 通过 CDP DOM 轮询采集实时行情"""

from typing import AsyncIterator
from htools.interfaces import DataSource
from htools.types import MarketTick
from htools.utils.logger import get_logger, setup_logging
from hdt.adapters.leyu_adapter import LeyuAdapter

logger = get_logger(__name__)


class StreamSource(DataSource):
    """CDP DOM 轮询数据源。"""

    def __init__(self, cdp_url: str = ""):
        self._cdp_url = cdp_url
        self._adapter = LeyuAdapter()
        self._running = False

    @property
    def id(self) -> str:
        return "stream_source"

    @property
    def name(self) -> str:
        return "Stream Source"

    @property
    def status(self) -> str:
        return "running" if self._running else "stopped"

    async def start(self) -> AsyncIterator[MarketTick]:
        setup_logging()
        self._running = True
        logger.info("StreamSource started (CDP mode)")

        tick = self._adapter.create_tick(
            result="B",
            score=8.0,
            table_id=2718,
            counter_id="U01",
            trade_seq="GB000000000",
            road_sequence=["B"],
        )
        yield tick

        while self._running:
            await self._poll_once()
            import asyncio
            await asyncio.sleep(0.05)

    async def _poll_once(self):
        """单次 DOM 提取（从 harvester/core/live_source.py 后续迁移）"""
        pass

    async def stop(self):
        self._running = False
        logger.info("StreamSource stopped")
