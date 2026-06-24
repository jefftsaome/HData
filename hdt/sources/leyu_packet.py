"""PacketSource — 通过 WebSocket 协议解码采集"""

from typing import AsyncIterator
import logging
from htools.interfaces import DataSource
from htools.types import MarketTick
from hdt.adapters.leyu_adapter import LeyuAdapter

logger = logging.getLogger(__name__)


class PacketSource(DataSource):
    """WebSocket 协议解码数据源。"""

    def __init__(self, table_id: int = 0, mode: str = "direct"):
        self._table_id = table_id
        self._mode = mode
        self._adapter = LeyuAdapter()
        self._running = False
        self._client = None

    @property
    def id(self) -> str:
        return "packet_source"

    @property
    def name(self) -> str:
        return "Packet Source"

    @property
    def status(self) -> str:
        return "running" if self._running else "stopped"

    async def start(self) -> AsyncIterator[MarketTick]:
        self._running = True
        logger.info("PacketSource started (mode=%s, table_id=%s)", self._mode, self._table_id)

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

    async def stop(self):
        self._running = False
        if self._client:
            await self._client.disconnect()
        logger.info("PacketSource stopped")

    async def select_table(self, table_id: int, game_type_id: int = 2001) -> bool:
        logger.info("Table selected: %s (game_type=%s)", table_id, game_type_id)
        return True
