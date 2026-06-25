"""CDPSource — 通过 CDP DOM 轮询采集实时行情"""

from typing import AsyncIterator, Callable

from htools.interfaces import DataSource, SourceStatus
from htools.types import MarketTick, SourceStatusEvent
from htools.utils.logger import get_logger, setup_logging
from hdt.adapters.leyu_adapter import LeyuAdapter

logger = get_logger(__name__)


class CDPSource(DataSource):
    """CDP DOM 轮询数据源。通过 Chrome CDP 读取游戏页面 DOM。"""

    def __init__(self, cdp_url: str = ""):
        self._cdp_url = cdp_url
        self._adapter = LeyuAdapter()
        self._status: SourceStatus = "idle"
        self._on_status_change: Callable[[SourceStatusEvent], None] | None = None

    @property
    def id(self) -> str:
        return "cdp_source"

    @property
    def name(self) -> str:
        return "CDP Source"

    @property
    def status(self) -> SourceStatus:
        return self._status

    def set_on_status_change(self, callback: Callable[[SourceStatusEvent], None]):
        self._on_status_change = callback

    def _set_status(self, status: SourceStatus):
        self._status = status
        if self._on_status_change:
            self._on_status_change({"source_id": self.id, "status": status})

    async def start(self) -> AsyncIterator[MarketTick]:
        setup_logging()
        self._set_status("running")
        logger.info("CDPSource started (CDP mode)")

        tick = self._adapter.create_tick(
            result="B",
            score=8.0,
            table_id=2718,
            counter_id="U01",
            trade_seq="GB000000000",
            road_sequence=["B"],
        )
        yield tick

        while self._status == "running":
            try:
                await self._poll_once()
            except Exception as e:
                logger.error("CDP poll error: {}", e)
                self._set_status("error")
                break
            import asyncio
            await asyncio.sleep(0.05)

    async def _poll_once(self):
        """单次 DOM 提取（从 harvester/core/live_source.py 后续迁移）"""
        pass

    async def stop(self):
        self._set_status("stopped")
        logger.info("CDPSource stopped")
