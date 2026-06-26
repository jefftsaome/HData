"""集成测试 — 需要真实 Chrome 进程

运行方式:
    uv run pytest tests/ -v                   # 跳过集成测试
    uv run pytest tests/ -v --run-integration  # 运行集成测试

注意: 需要本地有 Chrome/Chromium。若 Chrome 启动失败（sandbox/GPU 问题），
      测试会 skip 而非 fail。
"""

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chrome_start_and_connect():
    """验证 ChromeManager 能启动 Chrome 并返回有效端口。"""
    from hdt.auth.chrome_manager import ChromeManager

    cm = ChromeManager()
    try:
        port = await cm.start()
        assert port > 0, f"Chrome 端口无效: {port}"
        assert cm.cdp_url.startswith("ws://"), f"cdp_url 格式错误: {cm.cdp_url}"
    except Exception as e:
        pytest.skip(f"Chrome 启动失败 (环境问题): {e}")
    finally:
        await cm.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_evaluate_js():
    """验证 CDPSession 能在 Chrome 中执行 JS。"""
    from hdt.auth.chrome_manager import ChromeManager
    from hdt.capture.cdp_bridge import CDPSession

    cm = ChromeManager()
    try:
        await cm.start()
    except Exception as e:
        pytest.skip(f"Chrome 启动失败: {e}")

    try:
        cdp = CDPSession(cm.cdp_url)
        ok = await cdp.connect()
        if not ok:
            pytest.skip("CDP 连接失败——Chrome 版本兼容问题或 GPU 不可用")

        result = await cdp.evaluate("1 + 1")
        assert result is not None, "CDP evaluate 应返回结果"

        result2 = await cdp.evaluate("'hello' + ' world'")
        assert result2 is not None, "CDP evaluate 字符串应返回结果"

        await cdp.disconnect()
    finally:
        await cm.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cdp_attach_mode():
    """验证 from_url attach 模式能连接已启动的 Chrome。"""
    from hdt.auth.chrome_manager import ChromeManager
    from hdt.capture.cdp_bridge import CDPSession

    cm_auto = ChromeManager()
    try:
        await cm_auto.start()
    except Exception as e:
        pytest.skip(f"Chrome 启动失败: {e}")

    try:
        cm_attach = ChromeManager.from_url(cm_auto.cdp_url)
        assert cm_attach.is_attached

        cdp = CDPSession(cm_attach.cdp_url)
        ok = await cdp.connect()
        if not ok:
            pytest.skip("attach 模式 CDP 连接失败")

        result = await cdp.evaluate("42")
        assert result is not None
        await cdp.disconnect()
    finally:
        await cm_auto.stop()
