"""JWT 认证管理、WS URL 构造"""

import json
import time
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AuthData:
    """认证数据"""
    token: str
    player_id: int
    domain: str
    expires_at: float = 0


class AuthManager:
    """管理 JWT 认证与 WS 连接信息。"""

    def __init__(self):
        self._auth: AuthData | None = None

    def set_auth(self, token: str, player_id: int, domain: str):
        """设置认证信息"""
        self._auth = AuthData(
            token=token,
            player_id=player_id,
            domain=domain,
            expires_at=time.time() + 3600,
        )
        logger.info("Auth set: player_id=%s, domain=%s", player_id, domain)

    def get_ws_url(self, game_type: int, table_id: int) -> str:
        """构造 WebSocket 连接 URL。

        URL 格式: wss://wsproxy.{domain}/ws?token={jwt}&gameType={gameTypeId}&tableId={tableId}
        """
        if not self._auth:
            raise RuntimeError("Auth not set. Call set_auth() first.")
        return (
            f"wss://wsproxy.{self._auth.domain}/ws"
            f"?token={self._auth.token}"
            f"&gameType={game_type}"
            f"&tableId={table_id}"
        )

    def extract_token_from_js(self, js_result: dict[str, Any]) -> str | None:
        """从 CDP JS 执行结果中提取 JWT token。"""
        try:
            if "result" in js_result and "value" in js_result["result"]:
                return js_result["result"]["value"].get("token")
        except (KeyError, TypeError, AttributeError):
            pass
        return None

    def extract_from_local_storage_js(self, keys: list[str] | None = None) -> str:
        """生成从 localStorage 提取认证信息的 JS 代码。"""
        keys = keys or ["token", "playerId", "domain"]
        return f"""
        (() => {{
            const result = {{}};
            {json.dumps(keys)}.forEach(k => {{
                try {{ result[k] = localStorage.getItem(k); }} catch(e) {{}}
            }});
            return JSON.stringify(result);
        }})()
        """
