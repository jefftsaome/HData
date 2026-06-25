"""Headless Chrome 进程管理"""

import asyncio
import os
import socket
import tempfile
import shutil

from htools.utils.logger import get_logger

logger = get_logger(__name__)

# 默认 CDP 调试端口
DEFAULT_CDP_PORT = 9222


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
        "--remote-debugging-port=9222",
    ]

    def __init__(self, chrome_path: str | None = None, data_dir: str | None = None):
        self._chrome_path = chrome_path or self._find_chrome()
        self._data_dir = data_dir
        self._process: asyncio.subprocess.Process | None = None
        self._cdp_port: int = 0
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

    async def start(self, port: int = 0) -> int:
        """启动 Chrome，返回 CDP 端口号。

        Args:
            port: CDP 调试端口，0 表示使用默认 9222。

        Returns:
            实际使用的 CDP 端口号。
        """
        if self._process:
            if self._is_alive():
                logger.warning("Chrome already running (PID={})", self._process.pid)
                return self._cdp_port
            else:
                logger.warning("Chrome process dead, restarting")
                self._process = None

        self._cdp_port = port or DEFAULT_CDP_PORT

        # 如果端口已被占用，尝试端口 +1 递增
        while await self._port_in_use(self._cdp_port):
            logger.warning("Port {} in use, trying {}", self._cdp_port, self._cdp_port + 1)
            self._cdp_port += 1

        if not self._data_dir:
            self._temp_dir = tempfile.mkdtemp(prefix="chrome_")
            self._data_dir = self._temp_dir

        args = [
            self._chrome_path,
            f"--remote-debugging-port={self._cdp_port}",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--mute-audio",
            f"--user-data-dir={self._data_dir}",
        ]

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        logger.info("Chrome started (PID={}), waiting for port {}...",
                    self._process.pid, self._cdp_port)

        # 等待端口就绪（超时 15 秒）
        await self._wait_for_port(self._cdp_port, timeout=15)
        logger.info("Chrome ready on port {}", self._cdp_port)
        return self._cdp_port

    async def _wait_for_port(self, port: int, timeout: float = 15) -> None:
        """等待指定端口可连接。

        Raises:
            TimeoutError: 超时后端口仍未就绪
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self._port_in_use(port):
                # 再快速确认一下 CDP 端口是否真的可通信
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", port),
                        timeout=2,
                    )
                    writer.close()
                    await writer.wait_closed()
                    return
                except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
                    pass
            await asyncio.sleep(0.3)

        raise TimeoutError(f"Chrome port {port} not ready within {timeout}s")

    @staticmethod
    async def _port_in_use(port: int) -> bool:
        """检查端口是否已被占用。"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port),
                timeout=1,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return False

    def _is_alive(self) -> bool:
        """检查 Chrome 子进程是否仍在运行。"""
        if self._process is None:
            return False
        return self._process.returncode is None

    async def stop(self):
        """关闭 Chrome 进程并清理临时目录。"""
        if self._process and self._is_alive():
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await asyncio.wait_for(self._process.wait(), timeout=3)
            self._process = None
            logger.info("Chrome stopped")

        # 清理临时用户数据目录
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
            self._data_dir = None

    @property
    def cdp_url(self) -> str:
        """获取 CDP WebSocket URL。

        Returns:
            ws://127.0.0.1:{port}/devtools/browser
        """
        if not self._cdp_port:
            raise RuntimeError("Chrome not started")
        return f"ws://127.0.0.1:{self._cdp_port}/devtools/browser"
