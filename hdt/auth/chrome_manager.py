"""Headless Chrome 进程管理"""

import asyncio
import os
import tempfile
import shutil

from htools.utils.logger import get_logger

logger = get_logger(__name__)


class ChromeManager:
    """管理 Chrome 进程的生命周期：启动、保活、崩溃重启、关闭。

    通过 CDP (Chrome DevTools Protocol) 与 Chrome 实例通信。
    """

    CHROME_ARGS = [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--mute-audio",
        "--remote-debugging-port=0",
    ]

    def __init__(self, chrome_path: str | None = None, data_dir: str | None = None):
        self._chrome_path = chrome_path or self._find_chrome()
        self._data_dir = data_dir
        self._process: asyncio.subprocess.Process | None = None
        self._cdp_port: int | None = None
        self._temp_dir: str | None = None

    @staticmethod
    def _find_chrome() -> str:
        """自动查找系统 Chrome/Chromium 路径"""
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        raise RuntimeError("Chrome not found. Install Google Chrome or set chrome_path.")

    async def start(self) -> int:
        """启动 Chrome，返回 CDP 端口号。"""
        if self._process:
            logger.warning("Chrome already running")
            return self._cdp_port or 0

        if not self._data_dir:
            self._temp_dir = tempfile.mkdtemp(prefix="chrome_")
            self._data_dir = self._temp_dir

        args = [
            self._chrome_path,
            *self.CHROME_ARGS,
            f"--user-data-dir={self._data_dir}",
        ]

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        self._cdp_port = int(os.environ.get("CDP_PORT", "0")) or 9222
        logger.info("Chrome started (PID={}, CDP port={})", self._process.pid, self._cdp_port)
        return self._cdp_port

    async def stop(self):
        """关闭 Chrome 进程"""
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

        logger.info("Chrome stopped")

    @property
    def cdp_url(self) -> str:
        """获取 CDP WebSocket URL"""
        if not self._cdp_port:
            raise RuntimeError("Chrome not started")
        return f"ws://127.0.0.1:{self._cdp_port}/devtools/browser"
