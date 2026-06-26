"""CDP 桥接 — 通过 Chrome DevTools Protocol 与游戏页面交互"""

import json
from typing import Any

from htools.utils.logger import get_logger

logger = get_logger(__name__)


class CDPSession:
    """CDP 会话 — 连接 Chrome 调试端口，执行 JS，拦截消息。"""

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url
        self._ws: Any = None
        self._target_id: str | None = None
        self._session_id: str | None = None
        self._msg_id = 1

    async def connect(self) -> bool:
        """连接到 Chrome CDP 并获取页面目标。"""
        try:
            import websockets
            self._ws = await websockets.connect(self._cdp_url)
            targets = await self._send("Target.getTargets")
            for t in targets.get("targetInfos", []):
                if t["type"] == "page":
                    self._target_id = t["targetId"]
                    break
            if not self._target_id:
                logger.warning("No page target found")
                return False
            # attachToTarget with flatten=True
            # Chrome 150+ 先推一个 Target.attachedToTarget 事件（含 sessionId），
            # 然后才是命令响应。需读两帧。
            await self._send_cmd("Target.attachToTarget", {
                "targetId": self._target_id,
                "flatten": True,
            })
            # 第一帧：事件
            event = json.loads(await self._ws.recv())
            if event.get("method") == "Target.attachedToTarget":
                self._session_id = event.get("params", {}).get("sessionId")
            # 第二帧：命令响应（可忽略）
            await self._ws.recv()
            return self._session_id is not None
        except Exception as e:
            logger.error("CDP connect failed: {}", e)
            return False

    async def evaluate(self, js: str) -> dict[str, Any] | None:
        """在页面中执行 JS 并返回结果。"""
        if not self._session_id:
            return None
        try:
            result = await self._send("Runtime.evaluate", {
                "expression": js,
                "returnByValue": True,
            }, session_id=self._session_id)
            # CDP 返回结构: {result: {type: "number", value: 2, ...}}
            return result.get("result") if result else None
        except Exception as e:
            logger.warning("CDP evaluate failed: {}", e)
            return None

    async def _send(self, method: str, params: dict | None = None,
                    session_id: str | None = None) -> dict:
        """发送 CDP 命令并等待响应"""
        if not self._ws:
            raise RuntimeError("Not connected")
        msg = {
            "id": self._msg_id,
            "method": method,
            "params": params or {},
        }
        if session_id:
            msg["sessionId"] = session_id
        self._msg_id += 1
        await self._ws.send(json.dumps(msg))
        resp = await self._ws.recv()
        return json.loads(resp).get("result", {})

    async def _send_cmd(self, method: str, params: dict | None = None):
        """发送 CDP 命令，不等待响应（用于需要先读事件的场景）。"""
        if not self._ws:
            raise RuntimeError("Not connected")
        msg = {
            "id": self._msg_id,
            "method": method,
            "params": params or {},
        }
        self._msg_id += 1
        await self._ws.send(json.dumps(msg))

    async def disconnect(self):
        """断开 CDP 连接"""
        if self._ws:
            await self._ws.close()
            self._ws = None
