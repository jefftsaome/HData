"""牌局追踪、庄闲和判定、靴次管理"""

from dataclasses import dataclass, field
from typing import Literal

Result = Literal["B", "P", "T"]  # 庄/闲/和


@dataclass
class TableState:
    """单桌牌局状态"""
    table_id: int
    last_round_id: int | None = None
    streak_side: str | None = None
    streak_count: int = 0
    history: list[dict] = field(default_factory=list)

    def add_result(self, result: str, round_id: int, scores: dict | None = None) -> None:
        """记录一轮结果，检测连庄/连闲"""
        if result == self.streak_side and round_id != self.last_round_id:
            self.streak_count += 1
        elif result != self.streak_side:
            self.streak_side = result
            self.streak_count = 1
        self.last_round_id = round_id
        self.history.append({
            "round_id": round_id,
            "result": result,
            "scores": scores or {},
        })


class RoundTracker:
    """多桌牌局追踪器 — 管理所有桌台的 TableState"""

    def __init__(self, max_history: int = 100):
        self._tables: dict[int, TableState] = {}
        self._max_history = max_history

    def get_table(self, table_id: int) -> TableState:
        """获取指定桌台的追踪状态，不存在则创建"""
        if table_id not in self._tables:
            self._tables[table_id] = TableState(table_id=table_id)
        return self._tables[table_id]

    def feed(self, table_id: int, result: str, round_id: int,
             scores: dict | None = None) -> dict | None:
        """投喂一轮牌局结果，返回长龙信号（如有）。

        Returns:
            检测到长龙时返回信号 dict，否则 None。
            信号格式: {"table_id": int, "streak_side": str, "streak_count": int}
        """
        table = self.get_table(table_id)
        table.add_result(result, round_id, scores)

        if table.streak_count >= 5 and result == table.streak_side:
            return {
                "table_id": table_id,
                "streak_side": table.streak_side,
                "streak_count": table.streak_count,
            }
        return None

    def get_history(self, table_id: int) -> list[dict]:
        """获取指定桌台的牌局历史"""
        return self.get_table(table_id).history

    def reset(self, table_id: int | None = None):
        """重置指定桌台（或全部）的追踪状态"""
        if table_id is not None:
            self._tables.pop(table_id, None)
        else:
            self._tables.clear()
