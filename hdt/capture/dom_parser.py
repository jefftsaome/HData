"""DOM 解析器 — 将 JS 提取的原始 DOM 数据解析为结构化结果"""

import hashlib
import json
import re
from htools.utils.logger import get_logger

logger = get_logger(__name__)

# 投注区域名称（按页面显示顺序）
AREA_NAMES = ["庄", "闲", "和", "庄对", "闲对"]


def baccarat_value(card: str) -> int:
    """计算单张百家乐牌的点数。

    Args:
        card: 单牌字符如 "A", "8", "K", "10"

    Returns:
        点数：A=1, 10/J/Q/K=0, 数字=面值
    """
    c = card.upper()
    if c == "A":
        return 1
    if c in ("J", "Q", "K", "10"):
        return 0
    return int(c)


def parse_number(text: str) -> int:
    """解析带后缀的金额文本为整数值。

    Args:
        text: 如 "16.2K", "39.1W", "5M", "100"

    Returns:
        整数值
    """
    text = text.strip().upper()
    m = re.match(r"([\d.]+)([KMW]?)", text)
    if not m:
        return 0
    val = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K":
        val *= 1000
    elif suffix == "W":
        val *= 10000
    elif suffix == "M":
        val *= 1000000
    return int(val)


def parse_cards(raw_text: str) -> list[dict]:
    """解析卡牌文本为结构化列表。

    Args:
        raw_text: 如 "8 9"、"A K"、"闲"（下注中占位）

    Returns:
        卡牌列表，每张 {display, baccarat_value}
    """
    vals = re.findall(r"(\d+|[AJQKajqk]|10)", raw_text)
    return [
        {"display": c, "baccarat_value": baccarat_value(c)}
        for c in vals
    ]


def parse_bets(bet_raw: str) -> tuple[dict, dict]:
    """解析投注文本。

    Args:
        bet_raw: 如 "39.1K/196本局总投注庄16.2K/94闲22.4K/95和35/3..."

    Returns:
        (total_bet, areas): 总投注 dict + 各区域投注 dict
    """
    total = {}
    areas = {}
    if not bet_raw:
        return total, areas

    parts = bet_raw.split("本局总投注")
    if parts[0].strip():
        m = re.match(r"([\d.]+[KkMWw]?)\s*[/]\s*(\d+)", parts[0].strip())
        if m:
            total = {
                "amount_raw": m.group(1),
                "amount": parse_number(m.group(1)),
                "count": int(m.group(2)),
            }

    if len(parts) > 1 and parts[1].strip():
        remaining = parts[1].strip()
        for area_name in AREA_NAMES:
            m = re.match(
                re.escape(area_name) + r"([\d.]+[KkMWw]?)\s*[/]\s*(\d+)",
                remaining,
            )
            if m:
                areas[area_name] = {
                    "amount_raw": m.group(1),
                    "amount": parse_number(m.group(1)),
                    "count": int(m.group(2)),
                }
                remaining = remaining[m.end():].strip()
            else:
                areas[area_name] = {"amount_raw": "", "amount": 0, "count": 0}

    return total, areas


def parse_boot_stats(boot_items: list[dict]) -> dict:
    """解析靴盘统计。

    Args:
        boot_items: JS 返回的 bootItems 列表

    Returns:
        靴盘统计 dict
    """
    stats = {"total_rounds": 0, "banker_wins": 0, "player_wins": 0,
             "ties": 0, "banker_pair": 0, "player_pair": 0, "extra1": 0}
    if not boot_items:
        return stats

    for i, item in enumerate(boot_items):
        try:
            val = int(item.get("value", 0))
            icon = item.get("icon", "")
            if i == 0:
                stats["total_rounds"] = val
            elif icon == "庄":
                stats["banker_wins"] = val
            elif icon == "闲":
                stats["player_wins"] = val
            elif icon == "和":
                stats["ties"] = val
            elif icon in ("庄对", "庄對"):
                stats["banker_pair"] = val
            elif icon in ("闲对", "閑對"):
                stats["player_pair"] = val
            else:
                stats["extra1"] = val
        except (ValueError, IndexError):
            pass
    return stats


def parse_dynamic(raw: dict) -> dict:
    """将 JS 返回的原始 dict 解析为结构化动态数据。

    Args:
        raw: JS 返回的原始 dict，包含 roundId/status/cards/betRaw/bootItems 等

    Returns:
        结构化动态数据 dict：
        {ts, round_id, status, countdown_seconds, server_time,
         cards: {player, banker, player_total, banker_total},
         bets: {total, areas},
         boot_stats: {...},
         streaks}
    """
    # 卡牌解析
    player_cards = parse_cards(raw.get("player_score_text", ""))
    banker_cards = parse_cards(raw.get("banker_score_text", ""))
    player_total = sum(c["baccarat_value"] for c in player_cards) % 10
    banker_total = sum(c["baccarat_value"] for c in banker_cards) % 10

    # 投注解析
    total_bet, areas = parse_bets(raw.get("betRaw", ""))

    # 靴盘统计
    boot_stats = parse_boot_stats(raw.get("bootItems", []))

    # 倒计时
    countdown = None
    ctext = raw.get("countdownText", "")
    if ctext:
        try:
            countdown = int(ctext)
        except ValueError:
            pass

    return {
        "ts": raw.get("ts", 0),
        "round_id": raw.get("roundId", ""),
        "status": raw.get("status", ""),
        "countdown_seconds": countdown,
        "server_time": raw.get("timeDisplay", ""),
        "cards": {
            "player": player_cards,
            "banker": banker_cards,
            "player_total": player_total,
            "banker_total": banker_total,
        },
        "bets": {"total": total_bet, "areas": areas},
        "boot_stats": boot_stats,
        "streaks": raw.get("streaks", []),
    }


def detect_result(dynamic: dict) -> str | None:
    """从卡牌数据检测牌局结果。

    Args:
        dynamic: parse_dynamic 返回的结构化数据

    Returns:
        "B" (庄/banker) / "P" (闲/player) / "T" (和/tie)，数据不足返回 None
    """
    cards = dynamic.get("cards", {})
    player_total = cards.get("player_total")
    banker_total = cards.get("banker_total")

    if player_total is None or banker_total is None:
        return None
    if player_total < 0 or banker_total < 0:
        return None

    if player_total > banker_total:
        return "P"
    elif banker_total > player_total:
        return "B"
    else:
        return "T"


def decode_card_value(dv: str) -> str | None:
    """将 data-value 属性解码为牌面字符串。

    编码规则:
        rank = (data-value // 4) + 1    (1=A, 11=J, 12=Q, 13=K)
        suit = data-value % 4           (0=D, 1=C, 2=H, 3=S)
        -2 = 未翻牌

    Returns:
        "7S"、"10H"、"QH" 等，未翻牌返回 None
    """
    try:
        v = int(dv)
    except (ValueError, TypeError):
        return None
    if v < 0:
        return None
    rank_names = {1: "A", 11: "J", 12: "Q", 13: "K"}
    suit_names = {0: "D", 1: "C", 2: "H", 3: "S"}
    rank = (v // 4) + 1
    suit = v % 4
    return f"{rank_names.get(rank, rank)}{suit_names.get(suit, '?')}"


def decode_cards(values: list[str]) -> list[str]:
    """将 data-value 列表解码为牌面字符串列表。"""
    return [c for v in values if (c := decode_card_value(v)) is not None]


def parse_canvas_roads(canvas_data: dict | None) -> list[str]:
    """从 Canvas 像素分析结果中提取大路序列。

    Args:
        canvas_data: JS 返回的 canvasRoad dict，
                    包含 {sequence: ["B","P","B",...], stats: {...}}

    Returns:
        语义化后的序列 ["L","S","L",...]，无数据返回 []
    """
    if not canvas_data:
        return []
    seq = canvas_data.get("sequence", [])
    if not seq:
        return []
    road_map = {"B": "L", "P": "S", "T": "F"}
    return [road_map.get(s, "F") for s in seq]


def make_fingerprint(dynamic: dict, raw_result: str | None) -> str:
    """计算动态数据的 MD5 指纹（用于去重）。

    Args:
        dynamic: parse_dynamic 返回的结构化数据
        raw_result: detect_result 返回的结果 "B"/"P"/"T"/None

    Returns:
        MD5 指纹字符串
    """
    src = json.dumps(
        {
            "rid": dynamic.get("round_id", ""),
            "status": dynamic.get("status", ""),
            "countdown": dynamic.get("countdown_seconds"),
            "cards": dynamic.get("cards"),
            "bets": dynamic.get("bets"),
            "boot_stats": dynamic.get("boot_stats"),
            "result": raw_result,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.md5(src.encode()).hexdigest()
