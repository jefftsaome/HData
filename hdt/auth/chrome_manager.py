"""Headless Chrome 进程管理

两种使用模式:
  1. 自动模式 (auto-start) — ChromManager() 自动查找并启动 Chrome
  2. 附加模式 (attach) — ChromeManager.from_url(url) 连接用户已启动的 Chrome
"""

import asyncio
import os
import platform
import shutil
import tempfile

from htools.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CDP_PORT = 9222


def _find_chrome() -> str:
    """跨平台查找 Chrome/Chromium 可执行路径。"""
    system = platform.system()
    candidates: list[str] = []

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        # 常见安装路径 + PATH 中的 chrome
        candidates = [
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            "chrome.exe",
        ]
    elif system == "Linux":
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ]

    for c in candidates:
        # 相对路径/PATH中的可执行文件用 shutil.which
        if not os.path.isabs(c):
            found = shutil.which(c)
            if found:
                return found
        elif os.path.isfile(c):
            return c

    raise RuntimeError(
        f"Chrome not found on {system}. "
        "Install Google Chrome or pass chrome_path explicitly."
    )


class ChromeManager:
    """Chrome 进程生命周期管理。

    支持两种模式:
      - auto: ChromeManager() 自动启动/管理 Chrome
      - attach: ChromeManager.from_url(url) 连接已运行的 Chrome
    """

    # 跨平台通用参数
    BASE_ARGS = [
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--mute-audio",
        "--disable-blink-features=AutomationControlled",
    ]

    # Linux 专用参数
    LINUX_ARGS = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    def __init__(self, chrome_path: str | None = None, data_dir: str | None = None):
        self._chrome_path = chrome_path
        self._data_dir = data_dir
        self._process: asyncio.subprocess.Process | None = None
        self._cdp_port: int = 0
        self._temp_dir: str | None = None
        self._ws_url: str = ""  # 真实 CDP WebSocket URL

        # attach 模式：用户提供了 URL，不管理 Chrome 进程
        self._attached_url: str | None = None
        self._attached_host: str = "127.0.0.1"

    @classmethod
    def from_url(cls, url: str) -> "ChromeManager":
        """附加到用户已启动的 Chrome（不管理进程生命周期）。

        自动从 URL 解析 host 和 port，供 health check 使用。

        Args:
            url: CDP WebSocket URL，如 ws://127.0.0.1:9222/devtools/browser

        Usage:
            cm = ChromeManager.from_url("ws://127.0.0.1:9222/...")
            cm.cdp_url  # → ws://127.0.0.1:9222/...
            cm.is_attached  # → True
        """
        cm = cls()
        cm._attached_url = url
        # 解析 host 和 port 供 health check 使用
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            cm._attached_host = parsed.hostname or "127.0.0.1"
            cm._cdp_port = parsed.port or DEFAULT_CDP_PORT
        except Exception:
            cm._attached_host = "127.0.0.1"
            cm._cdp_port = DEFAULT_CDP_PORT
        return cm

    @property
    def is_attached(self) -> bool:
        """是否附加到外部 Chrome（非本类启动）。"""
        return self._attached_url is not None

    async def start(self, port: int = 0) -> int:
        """启动 Chrome（仅 auto 模式可用）。

        Args:
            port: CDP 端口，0=默认 9222。

        Returns:
            CDP 端口号。
        """
        if self.is_attached:
            raise RuntimeError("Cannot start() in attach mode. Use from_url() instead.")

        if self._process:
            if self._is_alive():
                logger.warning("Chrome already running (PID={})", self._process.pid)
                return self._cdp_port
            logger.warning("Chrome process dead, restarting")
            self._process = None

        self._cdp_port = port or DEFAULT_CDP_PORT

        # 端口冲突自增
        while await self._port_in_use(self._cdp_port):
            logger.warning("Port {} in use, trying {}", self._cdp_port, self._cdp_port + 1)
            self._cdp_port += 1

        # 延迟查找 Chrome 路径（避免 attach 模式无谓查找）
        if not self._chrome_path:
            self._chrome_path = _find_chrome()

        if not self._data_dir:
            self._temp_dir = tempfile.mkdtemp(prefix="chrome_")
            self._data_dir = self._temp_dir

        # 组装启动参数
        system = platform.system()
        args = [self._chrome_path, f"--remote-debugging-port={self._cdp_port}",
                f"--user-data-dir={self._data_dir}"]
        args += self.BASE_ARGS
        if system == "Linux":
            args += self.LINUX_ARGS

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        logger.info("Chrome started (PID={}), waiting for port {}...",
                    self._process.pid, self._cdp_port)

        await self._wait_for_port(timeout=15)

        # 从 Chrome HTTP 接口获取真实 CDP WebSocket URL
        self._ws_url = await self._fetch_ws_url()
        logger.info("Chrome ready on port {} (ws: {})", self._cdp_port, self._ws_url)
        return self._cdp_port

    async def _wait_for_port(self, timeout: float = 15) -> None:
        """轮询等待 CDP 端口可连接。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self._can_connect_host("127.0.0.1", self._cdp_port):
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(
            f"Chrome port {self._cdp_port} not ready within {timeout}s"
        )

    @staticmethod
    async def _can_connect(port: int) -> bool:
        """检查 127.0.0.1 的指定端口是否可连接。"""
        return await ChromeManager._can_connect_host("127.0.0.1", port)

    @staticmethod
    async def _can_connect_host(host: str, port: int) -> bool:
        """检查指定 host:port 是否可连接。"""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return False

    async def _fetch_ws_url(self) -> str:
        """从 Chrome HTTP 接口获取真实 CDP WebSocket URL。

        Chrome 启动后在 http://127.0.0.1:{port}/json/version 暴露
        webSocketDebuggerUrl，这个才是可用的 CDP 连接地址。
        """
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{self._cdp_port}/json/version",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    data = await resp.json()
                    url = data.get("webSocketDebuggerUrl", "")
                    if url:
                        return url
        except Exception as e:
            logger.warning("Failed to fetch CDP WS URL: {}", e)
        # 兜底：拼接标准路径（部分 Chrome 版本可用）
        return f"ws://127.0.0.1:{self._cdp_port}/devtools/browser"

    @staticmethod
    async def _port_in_use(port: int) -> bool:
        """检查端口是否已被占用。"""
        return await ChromeManager._can_connect(port)

    def _is_alive(self) -> bool:
        """检查 Chrome 子进程是否仍在运行。"""
        return self._process is not None and self._process.returncode is None

    async def health_check(self) -> str:
        """主动健康检查。

        Returns:
            状态字符串:
              - "healthy":  进程存活 + 端口可连
              - "dead":     进程已退出或端口不可达
              - "unknown":  未启动
        """
        if self.is_attached:
            ok = await self._can_connect_host(self._attached_host, self._cdp_port)
            return "healthy" if ok else "dead"

        if self._process is None:
            return "unknown"

        if not self._is_alive():
            logger.warning("Chrome process died (returncode={})", self._process.returncode)
            self._process = None
            return "dead"

        # 进程存活，检查端口响应
        ok = await self._can_connect_host("127.0.0.1", self._cdp_port)
        return "healthy" if ok else "dead"

    async def stop(self):
        """关闭 Chrome 进程。attach 模式下无操作。"""
        if self.is_attached:
            return

        if self._process and self._is_alive():
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None
            logger.info("Chrome stopped")

        # 清理临时用户数据目录
        if self._temp_dir:
            if platform.system() == "Windows":
                # Windows 下文件可能被锁，多次重试
                for attempt in range(3):
                    shutil.rmtree(self._temp_dir, ignore_errors=True)
                    if not os.path.exists(self._temp_dir):
                        break
                    await asyncio.sleep(0.5)
            else:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
            self._data_dir = None

    @property
    def cdp_url(self) -> str:
        """获取 CDP WebSocket URL。"""
        if self.is_attached:
            return self._attached_url
        if not self._cdp_port:
            raise RuntimeError("Chrome not started")
        return self._ws_url or f"ws://127.0.0.1:{self._cdp_port}/devtools/browser"
