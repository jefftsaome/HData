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


# ═══════════════════════════════════════════════════════════
# 游戏页面测试（需要 Chrome 在乐鱼游戏页面）
# ═══════════════════════════════════════════════════════════

@pytest.fixture(scope="function")
async def game_data(cdp):
    """从游戏页面提取原始 DOM 数据。若不在游戏页面则 skip。"""
    from hdt.capture.dom_extractor import DOMExtractor
    ext = DOMExtractor(cdp)
    raw = await ext.extract_dynamic()
    if not raw or not raw.get("roundId"):
        pytest.skip("未检测到游戏数据，请确认 Chrome 在游戏桌台页面。")
    # 同时提取固定信息（桌台名、编号等）
    await ext.extract_fixed_info()
    return raw, ext


class TestDOMExtraction:
    """验证从游戏页面能正确提取原始 DOM 数据"""

    @pytest.mark.asyncio
    async def test_round_id_is_present(self, game_data):
        raw, _ = game_data
        rid = raw.get("roundId", "")
        assert rid, "roundId 不应为空"
        assert len(rid) > 5, f"roundId 格式异常: {rid}"

    @pytest.mark.asyncio
    async def test_table_name_is_present(self, game_data):
        raw, _ = game_data
        name = raw.get("tableName", "")
        assert name, "tableName 不应为空"

    @pytest.mark.asyncio
    async def test_player_banker_cards(self, game_data):
        raw, _ = game_data
        player = raw.get("player_score_text", "")
        banker = raw.get("banker_score_text", "")
        assert player != "", "player_score_text 不应为空"
        assert banker != "", "banker_score_text 不应为空"

    @pytest.mark.asyncio
    async def test_status_and_countdown(self, game_data):
        raw, _ = game_data
        status = raw.get("status", "")
        ct = raw.get("countdownText", "")
        assert status != "", "status 不应为空"


class TestParseAndDetect:
    """验证 DOM 数据能被正确解析和判定结果"""

    @pytest.mark.asyncio
    async def test_parse_dynamic(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic
        raw, _ = game_data
        dyn = parse_dynamic(raw)
        assert dyn["round_id"] == raw.get("roundId", "")
        assert isinstance(dyn["ts"], int)
        assert dyn["status"] != ""
        cards = dyn.get("cards", {})
        pt = cards.get("player_total")
        bt = cards.get("banker_total")
        if pt is not None:
            assert 0 <= pt <= 9, f"闲点数应在 0-9: {pt}"
        if bt is not None:
            assert 0 <= bt <= 9, f"庄点数应在 0-9: {bt}"

    @pytest.mark.asyncio
    async def test_detect_result(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic, detect_result
        raw, _ = game_data
        dyn = parse_dynamic(raw)
        result = detect_result(dyn)
        assert result in ("B", "P", "T", None), f"结果异常: {result}"

    @pytest.mark.asyncio
    async def test_bet_data(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic
        raw, _ = game_data
        dyn = parse_dynamic(raw)
        bets = dyn.get("bets", {})
        total = bets.get("total", {})
        if total.get("amount"):
            assert total["amount"] > 0, "投注总额应大于 0"


class TestFullPipeline:
    """验证全链路能产出正确的 MarketTick"""

    @pytest.mark.asyncio
    async def test_adapter_produces_tick(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic, detect_result, decode_cards
        from hdt.adapters.leyu_adapter import LeyuAdapter

        raw, ext = game_data
        dyn = parse_dynamic(raw)
        result = detect_result(dyn)
        long_score = (dyn.get("cards", {}).get("banker_total", 0) or 0)
        short_score = (dyn.get("cards", {}).get("player_total", 0) or 0)
        fixed = ext.fixed_info or {}

        tick = LeyuAdapter().create_tick(
            result=result or "N",
            long_score=long_score,
            short_score=short_score,
            table_id=raw.get("urlTableId", 0),
            counter_id=fixed.get("table_id", ""),
            trade_seq=dyn.get("round_id", ""),
            status=raw.get("status", ""),
            countdown=int(raw.get("countdownText", "0") or 0) if raw.get("countdownText") else None,
            table_type_id=raw.get("urlGameType", 0),
            confidence=0.99 if result else 0.0,
            bets=dyn.get("bets"),
            extra_metadata={
                "table_type": fixed.get("gameplay", ""),
                "player_cards": ",".join(decode_cards(raw.get("playerCardValues", []))) if any(v != "-2" for v in raw.get("playerCardValues", [])) else raw.get("player_score_text", ""),
                "banker_cards": ",".join(decode_cards(raw.get("bankerCardValues", []))) if any(v != "-2" for v in raw.get("bankerCardValues", [])) else raw.get("banker_score_text", ""),
                "server_time": raw.get("timeDisplay", ""),
                "dealer": fixed.get("dealer", ""),
                "bet_limit": fixed.get("bet_limit", ""),
                "total_rounds": dyn.get("boot_stats", {}).get("total_rounds", 0),
            },
        )

        assert tick.counter_id, "counter_id 不应为空"
        assert tick.trade_seq, "trade_seq 不应为空"
        assert tick.trade_seq == dyn["round_id"], "trade_seq 应等于 round_id"
        assert tick.side in (
            type(tick.side).LONG,
            type(tick.side).SHORT,
            type(tick.side).FLAT,
        ), f"side 异常: {tick.side}"
        assert 0 <= tick.long_score <= 9, f"long_score 越界: {tick.long_score}"
        assert 0 <= tick.short_score <= 9, f"short_score 越界: {tick.short_score}"
        assert tick.metadata.get("table_no") == raw.get("urlTableId", 0)
        assert tick.metadata.get("table_type_id") == raw.get("urlGameType", 0)
        # player_cards: 有 data-value 时是解码格式 "7S,10H"，否则是原始文本
        pv = raw.get("playerCardValues", [])
        if pv and any(v != "-2" for v in pv):
            expected = ",".join(decode_cards(pv))
            assert tick.metadata.get("player_cards") == expected, f"闲牌解码不一致: {tick.metadata.get('player_cards')} != {expected}"
        else:
            assert tick.metadata.get("player_cards") == raw.get("player_score_text", "")
        bv = raw.get("bankerCardValues", [])
        if bv and any(v != "-2" for v in bv):
            expected = ",".join(decode_cards(bv))
            assert tick.metadata.get("banker_cards") == expected, f"庄牌解码不一致: {tick.metadata.get('banker_cards')} != {expected}"
        else:
            assert tick.metadata.get("banker_cards") == raw.get("banker_score_text", "")

        # 运行时加 -s 查看 MarketTick 完整摘要
        print(f"\n  counter={tick.counter_id}  trade_seq={tick.trade_seq}  side={tick.side.name}")
        print(f"  long_score={tick.long_score}  short_score={tick.short_score}")
        print(f"  status={tick.status}  countdown={tick.countdown}")
        print(f"  table_no={tick.metadata['table_no']}  type_id={tick.metadata['table_type_id']}  table_type={tick.metadata.get('table_type','')}")
        print(f"  total_amt={tick.total_amt}  total_cnt={tick.total_cnt}")
        print(f"  long_amt={tick.long_amt}  long_cnt={tick.long_cnt}")
        print(f"  short_amt={tick.short_amt}  short_cnt={tick.short_cnt}")
        print(f"  flat_amt={tick.flat_amt}  flat_cnt={tick.flat_cnt}")
        print(f"  player_cards={tick.metadata.get('player_cards','')}  banker_cards={tick.metadata.get('banker_cards','')}")
        print(f"  server_time={tick.metadata.get('server_time','')}  dealer={tick.metadata.get('dealer','')}  limit={tick.metadata.get('bet_limit','')}")
        print(f"  total_rounds={tick.metadata.get('total_rounds',0)}")
