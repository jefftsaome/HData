"""hdata — 乐鱼(leyu)平台数据采集 Python SDK。

对外公共入口。稳定用法:

    采集主线(推荐):
        from hdata import GameClient, get_login

        async def main():
            # 1. 登录 → 会话凭证
            session = await get_login("account", "password",
                                      geepass_token="...", jfbym_token="...")
            # 2. 客户端 + 拉桌台列表
            client = GameClient(entry_url="https://leyu.me")
            tables = await client.get_tables()
            # 3. 进桌持续读取牌局事件
            async with await client.enter_table(tables[0]["table_id"]) as ts:
                async for event in ts.events():
                    print(event)

    登录(多级降级:缓存 → HTTP 验证码 → 浏览器):
        from hdata import get_login

    反爬/签名/协议等内部机制不在此暴露，见各子包。

模块结构(2026-08 重构后):
    hdata.client     对外门面 GameClient + 传输/状态机(公共 API 主要来源)
    hdata.auth       登录/会话/token/签名/打码
    hdata.protocol   协议编解码 + schema 热更新
    hdata.capture    CDP DOM 采集
    hdata.proxy      代理池
    hdata.types      统一数据结构
"""

from hdata.client import (
    GameClient,
    GOOD_ROAD_NAMES,
    LoginError,
    MultiTableSession,
    TableInfo,
    TableMonitor,
    TableSession,
    road_streak,
)
from hdata.auth import get_login

__all__ = [
    # 采集主线
    "GameClient",
    "get_login",
    # 会话/状态
    "TableSession",
    "MultiTableSession",
    "TableMonitor",
    "TableInfo",
    # 工具
    "LoginError",
    "road_streak",
    "GOOD_ROAD_NAMES",
]
