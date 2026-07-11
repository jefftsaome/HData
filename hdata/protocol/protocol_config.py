"""Leyu 协议消息 ID 定义与字段配置"""

# ── 消息 ID ──
MSG_LOGIN = 10000           # 登录认证
MSG_ENTER_TABLE = 401       # 进桌
MSG_GAME_STATUS = 302       # 游戏状态
MSG_TABLE_BASE = 303        # 基础数据（含 cardResult/roadPaper）
MSG_ROAD_UPDATE = 161       # 路纸增量更新
MSG_LOBBY_TABLES = 10052    # 大厅桌台总览
MSG_ROAD_QUERY_REQ = 10075  # 路纸查询请求
MSG_ROAD_QUERY_RESP = 10071 # 路纸查询响应
MSG_SWITCH_TABLE = 160      # 换桌
MSG_HEARTBEAT = 301         # 心跳

# ── 游戏类型 ──
GAME_TYPE_MINI = 2001       # 迷你百家乐
GAME_TYPE_NORMAL = 2002     # 普通百家乐
GAME_TYPE_OFFLINE = 2003    # 线下百家乐
GAME_TYPE_VIP = 2004        # 高级百家乐
GAME_TYPE_TEST = 2013       # 测试牌桌

# ── 设备常量 ──
DEVICE_TYPE = 15
IDENTITY = 0
VIP_MODE = 0
JOIN_MODE = 1

# ── 协议帧格式 ──
FRAME_HEADER_LEN = 6        # [0x04][3B len][2B msg_id]


def make_table_param(table_id: int, device_id: str, **extra) -> dict:
    """构造进桌/请求的 param 字典"""
    param = {
        "tableId": table_id,
        "deviceType": DEVICE_TYPE,
        "deviceId": device_id,
        "identity": IDENTITY,
        "vipMode": VIP_MODE,
        "joinTableMode": JOIN_MODE,
    }
    param.update(extra)
    return param
