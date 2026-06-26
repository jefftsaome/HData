"""乐鱼原始数据 → MarketTick 适配器（语义隔离边界）"""

import time
from htools.types import MarketTick, TickSide


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
        score: float,
        table_id: int,
        counter_id: str = "",
        trade_seq: str = "",
        status: str = "",
        countdown: str = "",
        round_id: int = 0,
        game_type: int = 0,
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
            countdown: 倒计时文本
            round_id: WS 协议 roundId（数字）
            game_type: 合约类型编号
            boot_no: 靴次
            road_sequence: 路纸序列
            confidence: 置信度
            extra_metadata: 额外的 metadata 字段，合并到 metadata
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
        # 数字 tableId 移入 metadata（UI/策略不需要）
        metadata["table_no"] = str(table_id) if table_id else ""
        if game_type:
            metadata["game_type"] = game_type
        if round_id:
            metadata["round_id"] = round_id
        if boot_no:
            metadata["boot_no"] = boot_no
        if extra_metadata:
            metadata.update(extra_metadata)

        # instrument_id 不再放 tableId（已移入 metadata.table_no）
        # 用 counter_id 作为主体标识
        return MarketTick(
            instrument_id=counter_id or str(table_id),
            counter_id=counter_id,
            trade_seq=trade_seq,
            status=status,
            countdown=countdown,
            side=side,
            score=score,
            volume=1,
            confidence=confidence,
            timestamp=int(time.time() * 1000),
            metadata=metadata,
        )
