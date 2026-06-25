# hdt/types/__init__.py
"""hdt 内部类型定义 — 不对外暴露"""

from enum import IntEnum
from typing import Literal, NotRequired, TypedDict


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
    """单张牌的信息"""
    display: str
    baccarat_value: int


class BetEntry(TypedDict, total=False):
    """金额/人数条目"""
    amount_raw: str
    amount: int
    count: int


class OddsEntry(TypedDict, total=False):
    """赔率条目"""
    odds: str
    rest: str


class FixedData(TypedDict, total=False):
    """固定信息"""
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
    """交易信息"""
    total: BetEntry
    areas: dict[str, BetEntry]


class DynamicBootStats(TypedDict, total=False):
    """统计"""
    total_rounds: int
    banker_wins: int
    player_wins: int
    ties: int
    banker_pair: int
    player_pair: int
    extra1: int


class DynamicData(TypedDict, total=False):
    """动态数据"""
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


class DynamicExtractResult(TypedDict, total=False):
    """CDP JS 全量提取的原始返回结构"""
    ts: int
    roundId: str
    status: str
    countdownText: str
    timeDisplay: str
    tableName: str
    playerRaw: str
    bankerRaw: str
    betRaw: str
    bootItems: list[dict]
    streaks: list[str]
    canvasRoad: dict
    urlTableId: int
    urlGameType: int


class FixedGameInfo(TypedDict, total=False):
    """固定信息（CDP 一次提取，反复复用）"""
    game_name: str
    table_id: str
    gameplay: str
    bet_limit: str
    dealer: str
    odds: dict


class AdapterInput(TypedDict):
    """Adapter 输入 — 统一格式，同时兼容 CDP 和 WS 来源"""
    source_type: str          # "cdp" | "ws"
    result: str               # "L" | "S" | "F"  （LONG/SHORT/FLAT 简写）
    score: float              # 点数 0-9
    table_id: int             # 数字 tableId
    counter_id: str           # CDP tableName 后缀 "U11"
    trade_seq: str            # CDP roundNo "GB..."
    round_id: int             # WS roundId 数字
    game_type: int            # gameTypeId 2001
    boot_no: int              # 靴次
    road_sequence: list[str]  # 路纸序列 ["B","P","B",...]


__all__ = [
    "SourceType",
    "RoadResult", "RoadCell",
    "CardInfo", "BetEntry", "OddsEntry",
    "FixedData", "DynamicCards", "DynamicBets",
    "DynamicBootStats", "DynamicData",
    "RoadPapers", "LatestState",
]

__all__ += [
    "DynamicExtractResult",
    "FixedGameInfo",
    "AdapterInput",
]
