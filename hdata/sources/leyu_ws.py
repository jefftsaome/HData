"""WSSource — 纯 HTTP 获取 JWT + 直连 WebSocket 采集。

与 CDPSource 不同：不需要 Chrome/CDP，直接连接游戏 WS 代理。
通过 token_manager 纯 HTTP 获取游戏 JWT → 连接 WS → 解密帧 → MarketTick。

用法:
    import asyncio
    from hdata.sources.leyu_ws import WSSource

    async def main():
        src = WSSource(table_id=2718)
        async for tick in src.start():
            print(tick)

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, Callable

from hdata.adapters.leyu_adapter import LeyuAdapter
from hdata.capture.direct_client import WSClient
from htools.interfaces import DataSource, SourceStatus
from htools.types import MarketTick, SourceStatusEvent
from htools.utils.logger import get_logger, setup_logging
from htools.utils.time import now_ms

logger = get_logger(__name__)


class WSSource(DataSource):
    """纯 HTTP + WebSocket 直连数据源。

    采集流程:
      1. 通过 token_manager 纯 HTTP 获取游戏 JWT
      2. 构造 WS URL → 连接 wsproxy
      3. 发送进桌消息（401）
      4. 持续接收并解密帧 → MarketTick
      5. JWT 过期前自动刷新重连

    Usage:
        src = WSSource(table_id=2718)
        async for tick in src.start(): ...

        src = WSSource()  # 只监听，不指定桌台
        async for tick in src.start(): ...
    """

    # 乐鱼 AES 密钥（接收方向：AES-CBC, IV=KEY）
    AES_KEY = b"ED7AA06BD8628B55"

    def __init__(
        self,
        table_id: int = 0,
        game_type_id: int = 2001,
        en_name: str = "YBZR",
        account: str = "default",
    ):
        self._table_id = table_id
        self._game_type_id = game_type_id
        self._en_name = en_name
        self._account = account
        self._adapter = LeyuAdapter()
        self._status: SourceStatus = "idle"
        self._on_status_change: Callable[[SourceStatusEvent], None] | None = None
        self._client: WSClient | None = None
        self._token: str = ""
        self._ws_url: str = ""
        self._player_id: int = 0

    # ── DataSource 接口 ──────────────────────────────────

    @property
    def id(self) -> str:
        return "ws_source"

    @property
    def name(self) -> str:
        return "WS Source (pure HTTP)"

    @property
    def status(self) -> SourceStatus:
        return self._status

    def set_on_status_change(self, callback: Callable[[SourceStatusEvent], None]):
        self._on_status_change = callback

    def _emit_status(self, status: SourceStatus, message: str = ""):
        self._status = status
        if self._on_status_change:
            self._on_status_change(SourceStatusEvent(
                source_id=self.id, status=status, message=message,
                timestamp=now_ms(),
            ))

    # ── 启动 / 停止 ──────────────────────────────────────

    async def start(self) -> AsyncIterator[MarketTick]:
        """启动采集：获取 JWT → 连接 WS → 接收帧 → 产出 MarketTick。"""
        setup_logging()
        self._emit_status("running")

        while self._status == "running":
            try:
                # 1. 获取/刷新 JWT
                await self._ensure_token()

                # 2. 连接 WS
                self._client = WSClient(self._ws_url)
                if not await self._client.connect():
                    logger.error("WS 连接失败，5s 后重试...")
                    await asyncio.sleep(5)
                    continue

                logger.info("WS 已连接: table_id={}, player_id={}",
                            self._table_id, self._player_id)

                # 3. 发送进桌消息
                if self._table_id > 0:
                    await self._send_enter_table()

                # 4. 消息循环
                queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
                self._client.on_message(lambda mid, data: asyncio.ensure_future(
                    queue.put({"msg_id": mid, "data": data})
                ))

                listen_task = asyncio.ensure_future(self._client.listen())

                while self._status == "running":
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=30)
                        tick = self._msg_to_tick(msg)
                        if tick:
                            yield tick
                    except asyncio.TimeoutError:
                        # 检查是否需要刷新 token
                        if self._token_expiring():
                            logger.info("JWT 即将过期，准备刷新...")
                            break

                listen_task.cancel()
                try:
                    await listen_task
                except asyncio.CancelledError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WSSource 异常: {}", e)
                self._emit_status("error", str(e))
                await asyncio.sleep(5)

        await self._disconnect()
        logger.info("WSSource 已停止")

    async def stop(self):
        self._emit_status("stopped")
        await self._disconnect()

    async def _disconnect(self):
        if self._client:
            await self._client.disconnect()
            self._client = None

    # ── Token 管理 ────────────────────────────────────────

    async def _ensure_token(self):
        """确保有有效的游戏 JWT，通过 TokenManager 自动处理缓存和刷新。"""
        from hdata.auth.token_manager import TokenManager
        tm = TokenManager(self._account)

        token = await tm.get_token()
        session = tm._load()
        if not session:
            raise RuntimeError(f"[{self._account}] 无缓存，请先 --init 或 --login")

        self._token = token
        self._player_id = session.get("game_player_id", 0)
        backend = session.get("game_backend", "")
        if backend:
            host = backend.split(":")[0] if ":" in backend else backend
            self._ws_url = (
                f"wss://wsproxy.{host}:18026/"
                f"?playerId={self._player_id}"
                f"&jwtToken={self._token}"
                f"&deviceType=2&platform=6"
            )

    def _token_expiring(self) -> bool:
        """检查 JWT 是否将在 5 分钟内过期。"""
        if not self._token:
            return True
        from hdata.auth.token_manager import decode_jwt
        jwt_info = decode_jwt(self._token)
        if jwt_info:
            return jwt_info.get("exp", 0) - time.time() < 300
        return True

    # ── WS 协议 ───────────────────────────────────────────

    async def _send_enter_table(self):
        """发送 401 进桌消息。"""
        if not self._client:
            return
        param = {
            "tableId": self._table_id,
            "deviceType": 15,
            "deviceId": "",
            "identity": 0,
            "vipMode": 0,
            "joinTableMode": 1,
        }
        data = {
            "id": 401,
            "data": {},
            "param": json.dumps(param, separators=(",", ":")),
            "protocolId": 401,
        }
        ok = await self._client.send_frame(401, data)
        logger.info("进桌 401: table_id={}, ok={}", self._table_id, ok)

    # ── 消息转换 ──────────────────────────────────────────

    def _msg_to_tick(self, msg: dict) -> MarketTick | None:
        """将 WS 消息转换为 MarketTick。"""
        data = msg.get("data", {})
        if not data:
            return None

        try:
            # 解析三层嵌套（如有）
            inner_str = data.get("jsonData", "{}")
            inner = json.loads(inner_str) if isinstance(inner_str, str) else inner_str

            msg_id = inner.get("id", msg.get("msg_id", 0))
            table_id = data.get("tableId", self._table_id)
            round_id = inner.get("roundId", "")

            # 根据消息类型转换
            game_data = inner.get("data", "{}")
            if isinstance(game_data, str):
                try:
                    game_data = json.loads(game_data)
                except json.JSONDecodeError:
                    game_data = {}

            # 提取路纸序列（如有）
            road_paper = game_data.get("roadPaper", [])
            road_seq = []
            if road_paper:
                road_seq = [r.get("result", "") for r in road_paper if r.get("result")]

            # 构造 MarketTick
            return self._adapter.create_tick(
                result="N",  # WS 数据源不直接判断结果，由策略引擎判断
                table_id=table_id,
                counter_id=str(table_id),
                trade_seq=str(round_id),
                round_id=round_id,
                table_type_id=self._game_type_id,
                road_sequence=road_seq,
                confidence=0.99,
                extra_metadata={
                    "msg_id": msg_id,
                    "source": "ws_direct",
                },
            )
        except Exception as e:
            logger.warning("WS 消息转换失败: {}", e)
            return None

    # ── 工具 ──────────────────────────────────────────────

    async def select_table(self, table_id: int, game_type_id: int = 2001) -> bool:
        """切换监控桌台。"""
        self._table_id = table_id
        self._game_type_id = game_type_id
        if self._client and self._status == "running":
            await self._send_enter_table()
        return True
