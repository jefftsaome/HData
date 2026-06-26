"""游戏页面全链路测试 — 需要 Chrome 已在乐鱼游戏页面

使用方式:
  1. 启动 Chrome 并进入游戏桌台:
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
       --remote-debugging-port=9222 \\
       --user-data-dir=/tmp/chrome_debug
  2. 在 Chrome 中登录乐鱼并进入任意桌台
  3. 运行测试:
     uv run pytest tests/test_game_pipeline.py -v

  若 Chrome 端口不是 9222，用 CDP_PORT 环境变量指定:
     CDP_PORT=9333 uv run pytest tests/test_game_pipeline.py -v
"""

import os
import asyncio
import pytest

CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))


async def _can_connect(port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=2,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
        return False


async def _resolve_ws_url(port: int) -> str:
    import aiohttp
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
    return f"ws://127.0.0.1:{port}/devtools/browser"


@pytest.fixture(scope="function")
async def cdp():
    """建立 CDP 连接。若端口不通则 skip。"""
    ready = await _can_connect(CDP_PORT)
    if not ready:
        pytest.skip(
            f"端口 {CDP_PORT} 不可连。请先启动 Chrome:\n"
            f'  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\\n'
            f"    --remote-debugging-port={CDP_PORT} \\\n"
            f"    --user-data-dir=/tmp/chrome_debug"
        )
    from hdt.capture.cdp_bridge import CDPSession
    ws_url = await _resolve_ws_url(CDP_PORT)
    session = CDPSession(ws_url)
    ok = await session.connect()
    if not ok:
        pytest.skip(f"CDP 连接失败: {ws_url}")
    yield session
    await session.disconnect()


@pytest.fixture(scope="function")
async def game_data(cdp):
    """从游戏页面提取原始 DOM 数据。若不在游戏页面则 skip。"""
    from hdt.capture.dom_extractor import DOMExtractor
    ext = DOMExtractor(cdp)
    raw = await ext.extract_dynamic()
    if not raw or not raw.get("roundId"):
        # 先试 extract_fixed_info 看是否有 tableName
        fixed = await ext.extract_fixed_info()
        table = fixed.get("game_name", "") if fixed else ""
        pytest.skip(
            f"未检测到游戏数据 (tableName={table!r})。\n"
            "请确认 Chrome 当前标签页在乐鱼游戏桌台页面。"
        )
    return raw, ext


class TestDOMExtraction:
    """验证从游戏页面能正确提取原始 DOM 数据"""

    @pytest.mark.asyncio
    async def test_round_id_is_present(self, game_data):
        raw, _ = game_data
        rid = raw.get("roundId", "")
        assert rid, "roundId 不应为空"
        assert len(rid) > 5, f"roundId 格式异常: {rid}"
        print(f"\n  牌局号: {rid}")

    @pytest.mark.asyncio
    async def test_table_name_is_present(self, game_data):
        raw, ext = game_data
        # 固定信息中的 tableName
        fixed = ext.extract_fixed_info() if hasattr(ext, '_fixed_info') and ext._fixed_info is None else ext.fixed_info
        # 动态数据中的 tableName
        name = raw.get("tableName", "")
        assert name, "tableName 不应为空"
        print(f"\n  桌台: {name}")

    @pytest.mark.asyncio
    async def test_player_banker_cards(self, game_data):
        raw, _ = game_data
        player = raw.get("playerCards", "")
        banker = raw.get("bankerCards", "")
        # 下注中时可能是占位符，但不应为空
        assert player != "", "playerCards 不应为空"
        assert banker != "", "bankerCards 不应为空"
        print(f"\n  闲牌: {player!r}  庄牌: {banker!r}")

    @pytest.mark.asyncio
    async def test_status_and_countdown(self, game_data):
        raw, _ = game_data
        status = raw.get("status", "")
        ct = raw.get("countdownText", "")
        assert status != "", "status 不应为空"
        print(f"\n  状态: {status}  倒计时: {ct}")


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
        print(f"\n  闲点数: {pt}  庄点数: {bt}")

    @pytest.mark.asyncio
    async def test_detect_result(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic, detect_result
        raw, _ = game_data
        dyn = parse_dynamic(raw)
        result = detect_result(dyn)
        assert result in ("B", "P", "T", None), f"结果异常: {result}"
        if result:
            print(f"\n  结果: {result}")

    @pytest.mark.asyncio
    async def test_bet_data(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic
        raw, _ = game_data
        dyn = parse_dynamic(raw)
        bets = dyn.get("bets", {})
        total = bets.get("total", {})
        if total.get("amount"):
            assert total["amount"] > 0, "投注总额应大于 0"
            print(f"\n  总投注: {total['amount']} ({total.get('count', 0)} 笔)")


class TestFullPipeline:
    """验证全链路能产出正确的 MarketTick"""

    @pytest.mark.asyncio
    async def test_adapter_produces_tick(self, game_data):
        from hdt.capture.dom_parser import parse_dynamic, detect_result, make_fingerprint
        from hdt.adapters.leyu_adapter import LeyuAdapter

        raw, ext = game_data
        dyn = parse_dynamic(raw)

        # 按 _poll_loop 的逻辑走一遍
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
                "player_cards": raw.get("playerCards", ""),
                "banker_cards": raw.get("bankerCards", ""),
                "server_time": raw.get("timeDisplay", ""),
                "dealer": fixed.get("dealer", ""),
                "bet_limit": fixed.get("bet_limit", ""),
                "total_rounds": dyn.get("boot_stats", {}).get("total_rounds", 0),
                "streaks": raw.get("streaks", []),
            },
        )

        # 验证关键字段
        assert tick.counter_id, f"counter_id 不应为空"
        assert tick.trade_seq, f"trade_seq 不应为空"
        assert tick.trade_seq == dyn["round_id"], "trade_seq 应等于 round_id"
        assert tick.side in (
            type(tick.side).LONG,
            type(tick.side).SHORT,
            type(tick.side).FLAT,
        ), f"side 异常: {tick.side}"
        assert 0 <= tick.long_score <= 9, f"long_score 越界: {tick.long_score}"
        assert 0 <= tick.short_score <= 9, f"short_score 越界: {tick.short_score}"
        assert tick.metadata.get("table_no") == raw.get("urlTableId", 0), "table_no 应等于 urlTableId"
        assert tick.metadata.get("table_type_id") == raw.get("urlGameType", 0), "table_type_id 应等于 urlGameType"
        assert tick.metadata.get("player_cards") == raw.get("playerCards", ""), "player_cards 应传递"
        assert tick.metadata.get("banker_cards") == raw.get("bankerCards", ""), "banker_cards 应传递"

        print(f"\n  ✅ counter_id={tick.counter_id}  trade_seq={tick.trade_seq}")
        print(f"  ✅ side={tick.side.name}  long={tick.long_score}  short={tick.short_score}")
        print(f"  ✅ status={tick.status}  countdown={tick.countdown}")
        print(f"  ✅ table_no={tick.metadata['table_no']}  type_id={tick.metadata['table_type_id']}")
        print(f"  ✅ 投注: total={tick.total_amt}  long={tick.long_amt}  short={tick.short_amt}")
