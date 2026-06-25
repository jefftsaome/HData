"""WSSource — 通过 WebSocket 协议解码采集"""

from typing import AsyncIterator, Callable

from htools.interfaces import DataSource, SourceStatus
from htools.types import MarketTick, SourceStatusEvent
from htools.utils.logger import get_logger, setup_logging
from hdt.adapters.leyu_adapter import LeyuAdapter
logger = get_logger(__name__)


class WSSource(DataSource):
    """WebSocket 协议解码数据源。直连或通过 CDP 桥接连接 WS 代理。"""

    def __init__(self, table_id: int = 0, mode: str = "direct"):
        self._table_id = table_id
        self._mode = mode
        self._adapter = LeyuAdapter()
        self._status: SourceStatus = "idle"
        self._client = None
        self._on_status_change: Callable[[SourceStatusEvent], None] | None = None

    @property
    def id(self) -> str:
        return "ws_source"

    @property
    def name(self) -> str:
        return "WS Source"

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
        logger.info("WSSource started (mode={}, table_id={})", self._mode, self._table_id)

        try:
            tick = self._adapter.create_tick(
                result="P",
                score=2.0,
                table_id=self._table_id or 2718,
                counter_id="",
                trade_seq="",
                round_id=456354030,
                game_type=2001,
                road_sequence=["B", "P"],
            )
            yield tick
        except Exception as e:
            logger.error("WSSource start error: {}", e)
            self._set_status("error")

    async def stop(self):
        self._set_status("stopped")
        if self._client:
            await self._client.disconnect()
        logger.info("WSSource stopped")

    async def select_table(self, table_id: int, game_type_id: int = 2001) -> bool:
        logger.info("Table selected: {} (game_type={})", table_id, game_type_id)
        return True
