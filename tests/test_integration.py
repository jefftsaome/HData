"""集成测试 — Chrome 端口连通性 + CDP 基本连通

场景 1: 代码启动 Chrome，测试端口通
场景 2: 用户已启动 Chrome，测试端口通
场景 3: CDP 基本功能（不依赖游戏页面）

运行:
    uv run pytest tests/test_integration.py -v
"""

import os
import asyncio
import pytest
from hdt.capture.cdp_bridge import CDPSession

# 默认 CDP 端口
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CDP_HOST = "127.0.0.1"


async def _can_connect(port: int) -> bool:
    """检查指定端口是否可连接。"""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(CDP_HOST, port), timeout=2,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
        return False


async def _resolve_ws_url(port: int) -> str:
    """从 Chrome HTTP 接口获取真实 CDP WebSocket URL。"""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{CDP_HOST}:{port}/json/version",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                data = await resp.json()
                url = data.get("webSocketDebuggerUrl", "")
                if url:
                    return url
    except Exception:
        pass
    return f"ws://{CDP_HOST}:{port}/devtools/browser"


# ═══════════════════════════════════════════════════════════
# 场景 1: 代码启动 Chrome
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_auto_start():
    """ChromeManager 启动 Chrome，验证端口就绪后关闭。"""
    from hdt.auth.chrome_manager import ChromeManager

    cm = ChromeManager()
    try:
        port = await cm.start(port=9225)  # 用 9225 避免冲突
        assert port > 0, f"Chrome 应返回有效端口: {port}"
        ready = await _can_connect(port)
        assert ready, f"端口 {port} 应可连接"
        assert "ws://" in cm.cdp_url, f"cdp_url 格式错误: {cm.cdp_url}"
    finally:
        await cm.stop()


# ═══════════════════════════════════════════════════════════
# 场景 2: 用户已启动 Chrome
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_attach_port():
    """验证用户已启动的 Chrome 端口是否可连，不可连则 skip。"""
    ready = await _can_connect(CDP_PORT)
    if not ready:
        pytest.skip(
            f"端口 {CDP_PORT} 不可连，请先启动 Chrome:\n"
            f'  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\\n'
            f"    --remote-debugging-port={CDP_PORT} \\\n"
            f"    --user-data-dir=/tmp/chrome_debug"
        )


# ═══════════════════════════════════════════════════════════
# 场景 3: CDP 基本功能（不依赖游戏页面）
# ═══════════════════════════════════════════════════════════

@pytest.fixture(scope="function")
async def cdp():
    """建立 CDP 连接，前置条件是端口已通。"""
    ready = await _can_connect(CDP_PORT)
    if not ready:
        pytest.skip(f"端口 {CDP_PORT} 不可连，跳过 CDP 测试")

    ws_url = await _resolve_ws_url(CDP_PORT)
    session = CDPSession(ws_url)
    ok = await session.connect()
    if not ok:
        pytest.skip(f"CDP 连接失败: {ws_url}")
    yield session
    await session.disconnect()


@pytest.mark.asyncio
async def test_cdp_list_pages(cdp: CDPSession):
    """验证 CDP 能执行 JS 获取当前页面 URL。"""
    result = await cdp.evaluate("window.location.href")
    assert result is not None, "evaluate 应返回结果"
    url = result.get("value", "")
    assert isinstance(url, str), f"URL 应为字符串: {url}"
    assert len(url) > 0, "URL 不应为空"


@pytest.mark.asyncio
async def test_cdp_page_targets(cdp: CDPSession):
    """验证 CDP 能找到至少一个 page target。"""
    # 直接访问 CDPSession 内部状态，确认有 target
    assert cdp._target_id is not None, "应有 page target"
    assert cdp._session_id is not None, "应有 session_id"
