"""WebSocket 直连采集客户端"""

import asyncio
from typing import Any, Callable

from hdata.protocol.codec import decode_frame, encode_frame
from htools.utils.logger import get_logger

logger = get_logger(__name__)


class WSClient:
    """WS 直连客户端 — 连接 Leyu WS 代理，接收和发送协议帧。

    帧格式（2026-07-17 实测验证）：整帧 = AES-128-CBC(gzip(JSON)) 密文，
    key=iv="ED7AA06BD8628B55"，无任何明文协议头。
    """

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws: Any = None
        self._running = False
        self._on_message: Callable | None = None

    def on_message(self, callback: Callable):
        """注册消息回调。callback(protocol_id, frame_dict)"""
        self._on_message = callback

    async def connect(self) -> bool:
        """连接到 WS 代理服务器。"""
        try:
            import websockets
            self._ws = await websockets.connect(
                self._ws_url, open_timeout=12, close_timeout=3,
                max_size=50 * 1024 * 1024,
            )
            self._running = True
            logger.info("WS connected: {}", self._ws_url[:80])
            return True
        except Exception as e:
            logger.error("WS connect failed: url={}, err={!r}", self._ws_url, e)
            return False

    async def listen(self):
        """持续接收消息（需在 Task 中运行）。"""
        while self._running and self._ws:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                self._process_raw(raw)
            except asyncio.TimeoutError:
                await self._send_heartbeat()
            except Exception as e:
                if self._running:
                    logger.error("WS recv error: {}", e)
                    await asyncio.sleep(1)

    def _process_raw(self, raw):
        """处理原始 WS 帧数据。"""
        if isinstance(raw, str) or not raw:
            return
        decoded = decode_frame(raw)
        if decoded and self._on_message:
            self._on_message(decoded.get("protocolId"), decoded)

    async def send_message(self, msg: dict) -> bool:
        """发送一个完整签名的协议消息（见 hdata.protocol.codec.build_message）。"""
        if not self._ws:
            return False
        try:
            await self._ws.send(encode_frame(msg))
            return True
        except Exception as e:
            logger.error("WS send error: {}", e)
            return False

    async def _send_heartbeat(self):
        """发送心跳（q9.HEART_BEAT=1，游戏前端心跳为协议帧）。"""
        from hdata.protocol.codec import build_message
        # 心跳协议体很小；playerId 由外层在登录后注入更佳，这里发空参心跳
        await self.send_message(build_message(1, {}, player_id=0))

    async def disconnect(self):
        """断开连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
