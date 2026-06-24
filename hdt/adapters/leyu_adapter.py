"""乐鱼原始数据 → MarketTick 适配器"""

from htools.types import MarketTick, TickSide, TickType
import time


class LeyuAdapter:
    """将乐鱼百家乐牌局结果翻译为通用 MarketTick"""

    RESULT_MAP = {
        "banker": TickSide.UP,
        "player": TickSide.DOWN,
        "tie": TickSide.FLAT,
    }

    def create_tick(
        self,
        side: int | str,
        price: float,
        table_id: str = "default",
        confidence: float = 1.0,
        **metadata,
    ) -> MarketTick:
        if isinstance(side, str):
            side = self.RESULT_MAP.get(side, TickSide.FLAT)
        return MarketTick(
            instrument_id=table_id,
            tick_type=TickType.TRADE,
            side=TickSide(side),
            price=price,
            volume=1,
            confidence=confidence,
            timestamp=int(time.time() * 1000),
            metadata=metadata,
        )
