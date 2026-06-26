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

    # 原始结果 → 语义化方向
    RESULT_MAP = {
        "banker": TickSide.LONG,    # 庄 → 做多
        "player": TickSide.SHORT,   # 闲 → 做空
        "tie": TickSide.FLAT,       # 和 → 平盘
        "B": TickSide.LONG,
        "P": TickSide.SHORT,
        "T": TickSide.FLAT,
    }

    # 路纸序列映射：原始 → 语义化
    ROAD_MAP = {
        "B": "L",   # banker → LONG
        "P": "S",   # player → SHORT
        "T": "F",   # tie → FLAT
    }

    def create_tick(
        self,
        result: str,
        score: int,
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
        extra_metadata: dict | None = None,
    ) -> MarketTick:
        """将百家乐牌局结果转为 MarketTick。

        Args:
            result: 原始结果 "banker"/"player"/"tie" 或 "B"/"P"/"T"
            score: 点数 0-9
            table_id: 数字桌台 ID（存入 metadata.table_no）
            counter_id: 柜台编号（CDP tableName 后缀如 "U11"）
            trade_seq: 交易序号（CDP roundNo 如 "GB..."）
            status: 牌局状态文本
            countdown: 倒计时秒数
            long_score: 多头评分（原庄点数）
            short_score: 空头评分（原闲点数）
            round_id: WS 协议 roundId（数字）
            table_type_id: 合约类型编号
            boot_no: 靴次
            road_sequence: 路纸序列
            confidence: 置信度
            extra_metadata: 额外的 metadata 字段（如 bet 数据、server_time），合并到 metadata
        """
        side = self.RESULT_MAP.get(result, TickSide.FLAT)

        # 语义化路纸序列
        road_seq_sanitized = []
        if road_sequence:
            road_seq_sanitized = [
                self.ROAD_MAP.get(r, "F") for r in road_sequence
            ]

        metadata: dict = {}
        if road_seq_sanitized:
            metadata["road_seq"] = road_seq_sanitized
        metadata["table_no"] = table_id
        if table_type_id:
            metadata["table_type_id"] = table_type_id
        if round_id:
            metadata["round_id"] = round_id
        if boot_no:
            metadata["boot_no"] = boot_no
        if extra_metadata:
            metadata.update(extra_metadata)

        return MarketTick(
            counter_id=counter_id,
            trade_seq=trade_seq,
            status=status,
            countdown=countdown,
            side=side,
            score=score,
            long_score=long_score,
            short_score=short_score,
            volume=1,
            confidence=confidence,
            timestamp=int(time.time() * 1000),
            metadata=metadata,
        )

    @staticmethod
    def build_bet_metadata(bets: dict) -> dict:
        """将 parse_dynamic 输出的 bets dict 转为语义化 metadata。

        Input bets 结构:
          {
            "total": {"amount_raw": "39.1K", "amount": 39100, "count": 196},
            "areas": {"庄": {"amount_raw": "16.2K", "amount": 16200, "count": 94}, ...}
          }

        Returns:
          {"bet_total_amount": 39100, "bet_total_count": 196,
           "bet_long_amount": 16200, "bet_long_count": 94, ...}
        """
        meta = {}
        total = bets.get("total", {})
        if total.get("amount"):
            meta["bet_total_amount"] = total["amount"]
            meta["bet_total_count"] = total.get("count", 0)

        for raw_name, data in bets.get("areas", {}).items():
            semantic_key = BET_AREA_MAP.get(raw_name, raw_name)
            if data.get("amount"):
                meta[f"bet_{semantic_key}_amount"] = data["amount"]
                meta[f"bet_{semantic_key}_count"] = data.get("count", 0)

        return meta
