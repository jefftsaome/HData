"""browser-act 进程生命周期管理。

让 get_token() 内部自动启停 browser-act，使用者完全无感。

用法:
    manager = BrowserActManager()
    port = await manager.ensure_running()
    # ... 用完 ...
    await manager.stop()  # 可选，进程也可常驻
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import urllib.request

from htools.utils.logger import get_logger

logger = get_logger(__name__)

# browser-act 配置 — 从环境变量读取，兜底用内置值
DEFAULT_SESSION = os.getenv("BROWSERACT_SESSION", "hdt")
DEFAULT_BROWSER_ID = os.getenv("BROWSERACT_BROWSER_ID", "104101531036728124")
CDP_HOST = "127.0.0.1"


class BrowserActManager:
    """管理 browser-act 进程。"""

    def __init__(self, session: str = "", browser_id: str = ""):
        self._session = session or DEFAULT_SESSION
        self._browser_id = browser_id or DEFAULT_BROWSER_ID

    # ── 公开 API ──────────────────────────────────────────

    async def ensure_running(self) -> int:
        """确保 browser-act Chrome 在运行，返回 CDP 端口。

        已运行 → 直接返回端口。
        未运行 → 通过 CLI 启动，阻塞等待 CDP 就绪。
        """
        port = self.discover_port()
        if port and self._cdp_ready(port):
            return port

        logger.info(f"[{self._session}] 启动 browser-act...")
        self._start()
        return await self._wait_ready(timeout=30)

    def discover_port(self) -> int | None:
        """发现运行中 browser-act 的 CDP 端口。"""
        env = os.getenv("LEYU_CDP_PORT", "")
        if env and env.isdigit():
            return int(env)

        try:
            result = subprocess.run(
                ["ps", "aux"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split('\n'):
                if "BrowserAct" in line and "remote-debugging-port" in line:
                    m = re.search(r'remote-debugging-port=(\d+)', line)
                    if m:
                        return int(m.group(1))
        except Exception:
            pass
        return None

    def stop(self):
        """关闭 browser-act 进程。"""
        port = self.discover_port()
        if port:
            try:
                subprocess.run(
                    ["browser-act", "--session", self._session, "session", "close", self._session],
                    capture_output=True, timeout=10)
            except Exception:
                pass

    # ── 内部 ──────────────────────────────────────────────

    def _cdp_ready(self, port: int) -> bool:
        """检查 CDP 端口是否可连接。"""
        try:
            r = urllib.request.urlopen(
                f"http://{CDP_HOST}:{port}/json/version", timeout=3)
            return r.status == 200
        except Exception:
            return False

    def _start(self):
        """启动 browser-act 浏览器。"""
        subprocess.Popen(
            ["browser-act", "--session", self._session,
             "browser", "open", self._browser_id, "--headed"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    async def _wait_ready(self, timeout: int) -> int:
        """轮询等待 CDP 端口就绪。"""
        deadline = time.time() + timeout
        last_port = None
        while time.time() < deadline:
            port = self.discover_port()
            if port and self._cdp_ready(port):
                logger.info(f"[{self._session}] browser-act 就绪 @ port {port}")
                return port
            if port and port != last_port:
                last_port = port
            await asyncio.sleep(1)
        raise RuntimeError(
            f"browser-act 启动超时 ({timeout}s)。请手动运行: "
            f"browser-act --session {self._session} browser open {self._browser_id} --headed"
        )
