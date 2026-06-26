"""乐鱼原始数据 → MarketTick 适配器（语义隔离边界）"""

import time
from htools.types import MarketTick, TickSide

# 投注区域原始名称 → 语义化 key
BET_AREA_MAP = {
    "庄": "long",
    "闲": "short",
    "和": "flat",
    "庄对": "long_pair",
    "闲对": "short_pair",
}


class LeyuAdapter:
    """将百家乐牌局结果翻译为通用 MarketTick（量化行情术语）"""

    RESULT_MAP = {
        "banker": TickSide.LONG,
        "player": TickSide.SHORT,
        "tie": TickSide.FLAT,
        "B": TickSide.LONG,
        "P": TickSide.SHORT,
        "T": TickSide.FLAT,
    }

    ROAD_MAP = {
        "B": "L",
        "P": "S",
        "T": "F",
    }

    def create_tick(
        self,
        result: str,
        table_id: int,
        counter_id: str = "",
        trade_seq: str = "",
        status: str = "",
        countdown: int | None = None,
        long_score: int = 0,
        short_score: int = 0,
        round_id: int = 0,
        table_type_id: int = 0,
        boot_no: int = 0,
        road_sequence: list[str] | None = None,
        confidence: float = 1.0,
        bets: dict | None = None,
        extra_metadata: dict | None = None,
    ) -> MarketTick:
        """将结果转为 MarketTick。"""
        side = self.RESULT_MAP.get(result, TickSide.FLAT)

        # 方向历史序列
        side_seq: list[str] = []
        if road_sequence:
            side_seq = [self.ROAD_MAP.get(r, "F") for r in road_sequence]

        # metadata 构建
        metadata: dict = {}
        metadata["table_no"] = table_id
        if table_type_id:
            metadata["table_type_id"] = table_type_id
        if round_id:
            metadata["round_id"] = round_id
        if boot_no:
            metadata["boot_no"] = boot_no
        if extra_metadata:
            metadata.update(extra_metadata)

        # 投注字段映射
        bet_kwargs = self._extract_bet_kwargs(bets or {})

        return MarketTick(
            counter_id=counter_id,
            trade_seq=trade_seq,
            side_sequence=side_seq,
            status=status,
            countdown=countdown,
            side=side,
            long_score=long_score,
            short_score=short_score,
            confidence=confidence,
            timestamp=int(time.time() * 1000),
            metadata=metadata,
            **bet_kwargs,
        )

    @staticmethod
    def _extract_bet_kwargs(bets: dict) -> dict:
        """从 bets dict 提取成交量关键字参数。"""
        kw = {}
        total = bets.get("total", {})
        if total.get("amount"):
            kw["total_amt"] = total["amount"]
            kw["total_cnt"] = total.get("count", 0)
        for raw_name, data in bets.get("areas", {}).items():
            semantic = BET_AREA_MAP.get(raw_name, raw_name)
            if data.get("amount"):
                kw[f"{semantic}_amt"] = data["amount"]
                kw[f"{semantic}_cnt"] = data.get("count", 0)
        return kw
