"""client 包共享叶子模块：协议常量、公开数据结构与纯函数。

被门面 __init__ 与 transport/tables 子模块共同引用；保持叶子
（只依赖 stdlib + hdata.protocol），避免与门面形成循环 import。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hdata.protocol.codec import (
    DEVICE_TYPE_PC,
    OT_HALL,
    build_message,
)

# ── 协议常量（内部使用，不导出） ──
_QS_TABLE_LIST_ALL = 10089
_QS_TABLE_LIST_LIMIT = 10053   # 分页桌台元数据（二进制 schema 帧）
_QS_TABLE_ROAD = 10071         # TABLE_ROAD — 主动拉取指定桌全量路纸（官方 sendReqRoadPaper）
_QS_GAME_LIST_SWITCH_TAB = 10027  # GAME_LIST_SWITCH_TAB 大厅订阅（官方进大厅第一步）
_QS_TABLE_DATA_UPDATE = 10052  # TABLE_DATA_UPDATE 桌台增量推送（10027 订阅后下发）
_YT_ALL_GAME = 41              # Yt.ALL_GAME — 大厅 groupId 全集
_QS_HEARTBEAT = 3              # 协议心跳（pid=3，与官方前端一致）
_HEARTBEAT_INTERVAL = 10       # 秒
_RECV_SILENCE_S = 120          # 收帧静默判死窗口（秒，关闭传输层 ping 后启用）
_QS_NEW_INTER_GAME = 401
_QS_INTER_GAME = 101
_QS_OUT_GAME = 102
_QS_NOTICE = 123      # 系统通知推送（含连续3局未下注预警 noticeId=21002）
_QS_PROT_DECODE_CONFIG = 10115  # 服务器推送协议 schema 配置（热更新版本指纹）
_FORCE_101_GAME_TYPES = {2003, 2004, 2014, 2020}

_HT_SEAT = 1
_PT_BASE = 2

# TableMonitor 分片建连控制（实测同 IP 密集建连会被 WAF 403/短封）。
# 2026-07-27 静默事件后改为**按代理出口分组限速**：同一出口下的
# 连接串行、间隔 _SHARD_CONNECT_INTERVAL_S（3s→18s 上调）；不同
# 出口的连接并行建；无代理（直连）全部视为同一组。
_SHARD_CONNECT_INTERVAL_S = 18.0  # 同一代理出口的 WS 建连间隔（秒）
_SHARD_CONNECT_RETRIES = 3        # 单分片失败重试次数
_SHARD_RETRY_BACKOFF_S = 5.0      # 退避基数（第 n 次失败睡 n×base 秒）
# ── 公开数据结构 ──────────────────────────────────────

@dataclass
class TableInfo:
    """一张桌台的摘要信息（来自大厅快照）。"""

    table_id: int
    game_type_id: int
    game_type_name: str
    table_name: str
    status: int                 # gameStatus：2  betting, 3  dealing, 4 开牌/结算
    online: int                 # 在线人数
    total_amount: float = 0.0   # 大厅总下注额（tableOnline.totalAmount；
                                # 平台目前恒推 0，字段保留待平台填数）
    boot_no: str = ""           # 靴号
    road_flat: str = ""         # 珠盘 B/P/T 序列（如 "BBPTPBPB"）
    road_count: int = 0         # 本靴已开局的局数
    good_roads: list[str] = field(default_factory=list)  # 生效好路名

    def to_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "game_type_id": self.game_type_id,
            "game_type_name": self.game_type_name,
            "table_name": self.table_name,
            "status": self.status,
            "online": self.online,
            "total_amount": self.total_amount,
            "boot_no": self.boot_no,
            "road_flat": self.road_flat,
            "road_count": self.road_count,
            "good_roads": list(self.good_roads),
        }
# gameTypeId → 官方名称（逆向自大厅前端 JS：枚举 It + _gameNameMap，
# 与网页大厅实际显示的 8 个分类逐一对上；2019/2021/2023/2025 为
# 2026-07-24 大厅实测补充）
_GAME_TYPE_NAMES = {
    2001: "经典百家乐", 2002: "极速百家乐", 2003: "竞咪百家乐",
    2004: "包桌百家乐", 2005: "共咪百家乐", 2006: "龙虎",
    2007: "轮盘", 2008: "骰宝", 2009: "牛牛",
    2010: "炸金花", 2011: "三公", 2012: "21点",
    2013: "多台", 2014: "高额百家乐", 2015: "斗牛",
    2016: "保险百家乐", 2018: "百家乐大赛", 2019: "德州扑克",
    2020: "番摊", 2021: "21点", 2023: "温州牌九",
    2025: "安达巴哈", 2027: "劲舞百家乐", 2030: "主播百家乐",
    2034: "闪电百家乐", 2038: "电投百家乐",
}
# 注意：桌名 ≠ 游戏类型。实测"龙争虎斗 01~10"系列桌（越南厅）挂
# 在经典百家乐(2001)下，是百家乐主题桌而非龙虎斗；真龙虎桌为
# gameTypeId=2006（桌名"龙虎E25"式）。物理桌台系列看 physicsTableNo
# 字母前缀（J=龙争虎斗系、H/C/B=经典百家乐系、U=极速系、E=龙虎/边游戏系）。

# 好路类型 id → 官方名称（逆向自前端 GoodRoadType 字典 + 中文语言包
# @grd_20001~20011；用于 set_road_filter 的参数与 goodRoadPoints 解读）
GOOD_ROAD_NAMES = {
    1: "长闲", 2: "长庄", 3: "大路单跳", 4: "长路转单跳",
    5: "一庄两闲", 6: "一闲两庄", 7: "逢庄跳", 8: "逢闲跳",
    9: "逢庄连", 10: "逢闲连", 11: "排排连",
}
def build_hall_switch_msg(player_id: int, device_id: str) -> dict:
    """构造大厅订阅消息（GAME_LIST_SWITCH_TAB=10027，groupId=41 全集）。

    官方客户端进大厅流程（release.js request()）：先 sendSwitch(Yt.ALL_GAME)
    订阅大厅推送，再 sendGetHallListAll() 拉全量桌台；之后服务器持续推
    10052 TABLE_DATA_UPDATE 增量。只发 10089 不发 10027 时，服务器可能
    不下发/不下全 10052 推送（2026-08-01 静态分析发现，见 docs/数据样本.md）。
    """
    offset = -time.timezone // 60 if time.daylight == 0 else -time.altzone // 60
    return build_message(
        _QS_GAME_LIST_SWITCH_TAB,
        {"groupId": _YT_ALL_GAME, "isAll": 1,
         "deviceType": DEVICE_TYPE_PC, "deviceId": device_id,
         "timeZoneArea": "Asia/Shanghai", "offsetMinutes": offset},
        player_id=player_id, game_type_id=2013,
        table_id=0, service_type_id=OT_HALL)
# ── 连胜计算 ──────────────────────────────────────────


def round_result_token(round_result) -> str:
    """把 107 牌局事件的 roundResult 解析为路纸 token。

    实测格式：`"{庄点};{闲点}"`（**庄在前**），
    如 "9;5"=庄9闲5、"6;4"=庄6闲4。判定：
      - 庄点 > 闲点 → "B"；庄点 == 6 且庄赢 → "B6"（幸运6庄）
      - 庄点 < 闲点 → "P"
      - 相等 → "T"
      - 无法解析 → ""

    Examples:
        >>> round_result_token("9;5")
        'B'
        >>> round_result_token("6;4")
        'B6'
        >>> round_result_token("4;6")
        'P'
        >>> round_result_token("5;5")
        'T'
    """
    if not isinstance(round_result, str) or ";" not in round_result:
        return ""
    try:
        b_s, p_s = round_result.split(";", 1)
        banker, player = int(b_s.strip()), int(p_s.strip())
    except (ValueError, AttributeError):
        return ""
    if banker > player:
        return "B6" if banker == 6 else "B"
    if banker < player:
        return "P"
    return "T"


def road_streak(road: str) -> tuple[str, int]:
    """计算路纸末尾连胜（对齐口径）。

    规则:
      - `T`(和) 归属于之前最近一局非和局的胜方，**不打断连胜也不计数**；
      - `B6`(幸运6庄) 视为 `B`；
      - 连胜 = 末尾同一胜方的连续非和局数（中间允许夹 T）。

    Returns:
        (side, count): side 为 "B"/"P"（无连胜为空串），count 为连胜局数。

    Examples:
        >>> road_streak("PTBBB")
        ('B', 3)
        >>> road_streak("BTTBB")   # 中间2局T视为庄和不占局数
        ('B', 3)
        >>> road_streak("BTBBT")   # 末尾T归庄
        ('B', 3)
    """
    seq = road.replace("B6", "B").rstrip("T")
    if not seq:
        return ("", 0)
    side = seq[-1]
    count = 0
    for ch in reversed(seq):
        if ch == side:
            count += 1
        elif ch == "T":
            continue
        else:
            break
    return (side, count)
# ── 多台模式（INTER_MULTIPLE=301）全桌订阅会话 ──

_QS_INTER_MULTIPLE = 301   # INTER_MULTIPLE 进入多台模式（Qs）
_OT_MULTIPLE = 2           # Ot.MULTIPLE — serviceTypeId 多台
_IT_MULTIPLAY = 2013       # It.MULTIPLAY — gameTypeId 多台
