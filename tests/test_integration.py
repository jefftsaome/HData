"""集成测试 — 需要真实 Chrome 进程

运行方式:
    uv run pytest tests/ -v                   # 跳过集成测试
    uv run pytest tests/ -v --run-integration  # 运行集成测试
"""

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chrome_start_and_connect():
    """验证 ChromeManager 能启动 Chrome 并建立 CDP 连接"""
    from hdt.auth.chrome_manager import ChromeManager

    cm = ChromeManager()
    try:
        port = await cm.start()
        assert port > 0, f"Chrome 端口无效: {port}"
        assert cm.cdp_url.startswith("ws://"), f"cdp_url 格式错误: {cm.cdp_url}"
    finally:
        await cm.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_evaluate_js():
    """验证 CDPSession 能在 Chrome 中执行 JS"""
    from hdt.auth.chrome_manager import ChromeManager
    from hdt.capture.cdp_bridge import CDPSession

    cm = ChromeManager()
    try:
        await cm.start()
        cdp = CDPSession(cm.cdp_url)
        ok = await cdp.connect()
        assert ok, "CDP 连接失败"

        result = await cdp.evaluate("1 + 1")
        assert result is not None
        assert result.get("value") == 2, f"JS 1+1 应返回 2, 实际: {result}"

        result = await cdp.evaluate("'hello' + ' world'")
        assert result is not None
        assert "hello world" in str(result)

        await cdp.disconnect()
    finally:
        await cm.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_attach_mode():
    """验证 attach 模式"""
    from hdt.auth.chrome_manager import ChromeManager
    from hdt.capture.cdp_bridge import CDPSession

    cm_auto = ChromeManager()
    try:
        port = await cm_auto.start()
        cm_attach = ChromeManager.from_url(f"ws://127.0.0.1:{port}/devtools/browser")
        assert cm_attach.is_attached
        assert cm_attach.cdp_url == cm_auto.cdp_url

        cdp = CDPSession(cm_attach.cdp_url)
        ok = await cdp.connect()
        assert ok, "attach 模式 CDP 连接失败"

        result = await cdp.evaluate("42")
        assert result is not None

        await cdp.disconnect()
    finally:
        await cm_auto.stop()
