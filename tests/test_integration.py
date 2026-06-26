"""集成测试 — 直连已在运行的 Chrome

要求:
  1. Chrome 已启动并开启了远程调试端口
  2. 通过 CDP_URL 环境变量指定地址，或默认连 ws://127.0.0.1:9222

启动 Chrome:
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
    --remote-debugging-port=9222 \\
    --user-data-dir=/tmp/chrome_debug

运行测试:
  uv run pytest tests/test_integration.py -v
  CDP_URL=ws://127.0.0.1:9333 uv run pytest tests/test_integration.py -v
"""

import os
import pytest

CDP_URL = os.environ.get("CDP_URL", "ws://127.0.0.1:9222/devtools/browser")


async def _resolve_cdp_url() -> str:
    """从 Chrome HTTP 接口获取真实 CDP WebSocket URL（含 UUID）。"""
    import aiohttp
    port = "9222"
    if ":" in CDP_URL:
        parts = CDP_URL.split(":")
        if len(parts) >= 3:
            port = parts[2].split("/")[0]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/json/version",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                data = await resp.json()
                url = data.get("webSocketDebuggerUrl", "")
                if url:
                    return url
    except Exception:
        pass
    return CDP_URL


@pytest.fixture(scope="function")
async def cdp():
    from hdt.capture.cdp_bridge import CDPSession
    real_url = await _resolve_cdp_url()
    session = CDPSession(real_url)
    ok = await session.connect()
    assert ok, (
        f"无法连接到 {real_url}\n"
        "请确认 Chrome 已启动:\n"
        '  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\\n'
        "    --remote-debugging-port=9222 \\\n"
        "    --user-data-dir=/tmp/chrome_debug"
    )
    yield session
    await session.disconnect()


class TestCDPConnection:
    """验证 CDP 能连通并执行 JS"""

    @pytest.mark.asyncio
    async def test_evaluate_number(self, cdp):
        result = await cdp.evaluate("1 + 1")
        assert result is not None
        assert result.get("value") == 2

    @pytest.mark.asyncio
    async def test_evaluate_string(self, cdp):
        result = await cdp.evaluate("'hello' + ' world'")
        assert result is not None
        assert result.get("value") == "hello world"

    @pytest.mark.asyncio
    async def test_evaluate_page_title(self, cdp):
        result = await cdp.evaluate("document.title")
        assert result is not None
        # 当前页面标题，不会是空的
        assert len(result.get("value", "")) > 0


class TestDOMExtractor:
    """验证 DOMExtractor 能在页面中提取数据"""

    @pytest.mark.asyncio
    async def test_extract_fixed_info(self, cdp):
        from hdt.capture.dom_extractor import DOMExtractor
        ext = DOMExtractor(cdp)
        # 当前页面没有百家乐元素，返回空数据但不抛异常
        result = await ext.extract_fixed_info()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_extract_dynamic(self, cdp):
        from hdt.capture.dom_extractor import DOMExtractor
        ext = DOMExtractor(cdp)
        # 当前页面没有百家乐 DOM，JS 执行但不抛异常即可
        result = await ext.extract_dynamic()
        # 可能返回 None（无对应 DOM 元素）或 dict（有元素）
        # 无论哪种，不抛异常就算通过
        assert result is None or isinstance(result, dict)
