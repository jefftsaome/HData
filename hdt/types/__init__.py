# hdt/types/__init__.py
"""hdt 内部类型定义 — 百家乐专有数据模型，不对外暴露"""

from enum import IntEnum
from typing import Literal, TypedDict


class SourceType(IntEnum):
    """数据来源类型 — 标识采集来源"""
    DOM = 1       # CDP DOM 实时采集
    SCREEN = 2    # 屏幕截图采集
    USB = 3       # USB/ADB 设备采集
    PACKET = 4    # 网络抓包采集


# 路纸结果类型
RoadResult = Literal["B", "P", "T"]
RoadCell = Literal["B", "P", "T", "."]


class CardInfo(TypedDict, total=False):
    """单张卡牌的信息"""
    display: str
    baccarat_value: int


class BetEntry(TypedDict, total=False):
    """投注金额/人数条目"""
    amount_raw: str
    amount: int
    count: int


class OddsEntry(TypedDict, total=False):
    """赔率条目"""
    odds: str
    rest: str


class FixedData(TypedDict, total=False):
    """桌台固定信息"""
    game_name: str
    table_id: str
    gameplay: str
    bet_limit: str
    dealer: str
    odds: dict[str, OddsEntry]


class DynamicCards(TypedDict, total=False):
    """卡牌信息"""
    player: list[CardInfo]
    banker: list[CardInfo]
    player_total: int | None
    banker_total: int | None


class DynamicBets(TypedDict, total=False):
    """投注信息"""
    total: BetEntry
    areas: dict[str, BetEntry]


class DynamicBootStats(TypedDict, total=False):
    """靴盘统计"""
    total_rounds: int
    banker_wins: int
    player_wins: int
    ties: int
    banker_pair: int
    player_pair: int
    extra1: int


class DynamicData(TypedDict, total=False):
    """牌局动态数据"""
    ts: int
    round_id: str
    status: str
    countdown_seconds: int | None
    server_time: str
    cards: DynamicCards
    bets: DynamicBets
    boot_stats: DynamicBootStats
    streaks: list[int]


class RoadPapers(TypedDict, total=False):
    """路纸数据"""
    sequence: list[RoadResult]
    stats: dict[RoadResult, int]
    matrix: list[list[RoadCell]]
    canvas_raw: dict | None
    cols: int
    rows: int


class LatestState(TypedDict, total=False):
    """全量数据快照"""
    fixed: FixedData
    dynamic: DynamicData
    road_papers: RoadPapers
    raw_result: str | None


__all__ = [
    "SourceType",
    "RoadResult", "RoadCell",
    "CardInfo", "BetEntry", "OddsEntry",
    "FixedData", "DynamicCards", "DynamicBets",
    "DynamicBootStats", "DynamicData",
    "RoadPapers", "LatestState",
]
