"""WebSocket 直连采集客户端"""

import asyncio
import json
import gzip
from typing import Any, Callable

from hdata.protocol.decoder import decode_frame
from htools.utils.logger import get_logger

logger = get_logger(__name__)


class WSClient:
    """WS 直连客户端 — 连接 Leyu WS 代理，接收和发送协议帧。

    帧格式: [0x04][3B payload_len][2B msg_id][payload]
    """

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws: Any = None
        self._running = False
        self._on_message: Callable | None = None

    def on_message(self, callback: Callable):
        """注册消息回调。callback(msg_id, data_dict)"""
        self._on_message = callback

    async def connect(self) -> bool:
        """连接到 WS 代理服务器。"""
        try:
            import websockets
            self._ws = await websockets.connect(self._ws_url)
            self._running = True
            logger.info("WS connected: {}", self._ws_url[:80])
            return True
        except Exception as e:
            logger.error("WS connect failed: {}", e)
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

    def _process_raw(self, raw: bytes):
        """处理原始 WS 帧数据。"""
        if not raw or raw[0] != 0x04:
            return
        payload_len = int.from_bytes(raw[1:4], "big")
        msg_id = int.from_bytes(raw[4:6], "big")
        payload = raw[6:6 + payload_len]

        decoded = decode_frame(payload)
        if decoded and self._on_message:
            self._on_message(msg_id, decoded)

    async def send_frame(self, msg_id: int, data: dict) -> bool:
        """发送协议帧。"""
        if not self._ws:
            return False
        payload = json.dumps(data, separators=(",", ":")).encode()
        compressed = gzip.compress(payload)
        frame = (
            bytes([0x04])
            + len(compressed).to_bytes(3, "big")
            + msg_id.to_bytes(2, "big")
            + compressed
        )
        try:
            await self._ws.send(frame)
            return True
        except Exception as e:
            logger.error("WS send error: {}", e)
            return False

    async def _send_heartbeat(self):
        """发送心跳包（msgId=301）"""
        await self.send_frame(301, {})

    async def disconnect(self):
        """断开连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
