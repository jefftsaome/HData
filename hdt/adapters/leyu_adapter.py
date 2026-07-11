"""乐鱼原始数据 → MarketTick 适配器（语义隔离边界）"""

import time
from htools.types import MarketTick, TickSide
from htools.utils.time import now_ms

# 投注区域原始名称 → 语义化 key
BET_AREA_MAP = {
    "庄": "long",
    "闲": "short",
    "和": "flat",
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

    # 状态文本映射：原始 → 量化行情术语
    STATUS_MAP = {
        "结算中": "CLOSED",
        "开牌中": "MATCHING",
        "洗牌中": "SHUFFLING",
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
        session_id: str = "",
        bets: dict | None = None,
        extra_metadata: dict | None = None,
    ) -> MarketTick:
        """将结果转为 MarketTick。"""
        side = self.RESULT_MAP.get(result, TickSide.FLAT)

        # 状态文本语义化
        # countdown 存在时说明处于下注期，DOM status 可能延迟，强制 OPEN
        if isinstance(countdown, int):
            # 这些数据都会有上局残留的可能性
            mapped_status = "OPEN"
            side, long_score, short_score = None, None, None
            if extra_metadata is None:
                extra_metadata = {}
            extra_metadata.update(
                {
                    "player_cards": None,
                    "banker_cards": None,
                }
            )
        else:
            mapped_status = self.STATUS_MAP.get(status, status)

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
            counter_id=counter_id,      # 桌台信息
            trade_seq=trade_seq,        # 牌局信息
            side_sequence=side_seq,     # 路纸数据
            status=mapped_status,       # 状态文本
            countdown=countdown,        # 倒计时
            side=side,                  # 牌局结果
            long_score=long_score,      # 庄点数
            short_score=short_score,    # 闲点数
            session_id=session_id,      # 靴盘局数
            confidence=confidence,      # 数据置信度
            timestamp=now_ms(),         # 时间戳 ms
            metadata=metadata,          # 不希望给到下游的数据
            # bet_kwargs include：
            # long_amt/short_amt/flat_amt/total_amt
            # long_cnt/short_cnt/flat_cnt/total_cnt
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

        needed_areas = BET_AREA_MAP.keys()
        areas = bets.get("areas", {})
        for raw_name in needed_areas:
            data = areas.get(raw_name, {})
            semantic = BET_AREA_MAP[raw_name]
            kw[f"{semantic}_amt"] = data.get("amount", 0)
            kw[f"{semantic}_cnt"] = data.get("count", 0)
        return kw
