"""CDPSource — 通过 Chrome CDP 连接游戏页面，从 DOM 实时采集行情"""

import asyncio
import time
from typing import AsyncIterator, Callable

from htools.interfaces import DataSource, SourceStatus
from htools.types import MarketTick, SourceStatusEvent
from htools.utils.logger import get_logger, setup_logging
from hdt.adapters.leyu_adapter import LeyuAdapter
from hdt.auth.chrome_manager import ChromeManager
from hdt.capture.cdp_bridge import CDPSession
from hdt.capture.dom_extractor import DOMExtractor
from hdt.capture.dom_parser import parse_dynamic, detect_result, make_fingerprint

logger = get_logger(__name__)


class CDPSource(DataSource):
    """通过 Chrome CDP 连接游戏页面，从 DOM 实时采集行情。

    两种使用模式:
      1. 自动模式 (默认) — 自动启动 Headless Chrome 并管理生命周期
      2. 附加模式 — 传入 cdp_url 连接到用户已启动的 Chrome

    采集流程:
      1. 启动/连接 Chrome（ChromeManager）
      2. 连接 CDP（CDPSession）
      3. 周期注入 JS 提取 DOM 数据（每 50ms）
      4. 解析为结构化数据 → 检测结果 → MarketTick
      5. 断线自动重连（指数退避 1s→2s→4s→...→30s）

    Usage:
        # 自动模式
        src = CDPSource()
        async for tick in src.start(): ...

        # 附加模式（用户已启动 Chrome）
        src = CDPSource(cdp_url="ws://127.0.0.1:9222/devtools/browser")
        async for tick in src.start(): ...
    """

    def __init__(
        self,
        cdp_url: str = "",
        chrome_path: str | None = None,
        poll_interval_ms: int = 50,
    ):
        self._chrome_path = chrome_path
        self._interval = max(0.02, poll_interval_ms / 1000)
        self._adapter: LeyuAdapter = LeyuAdapter()

        # attach 模式：用户提供了 CDP URL
        self._chrome: ChromeManager | None = None
        if cdp_url:
            self._chrome = ChromeManager.from_url(cdp_url)
        self._cdp: CDPSession | None = None
        self._extractor: DOMExtractor | None = None

        # 状态管理
        self._status: SourceStatus = "idle"
        self._on_status_change: Callable[[SourceStatusEvent], None] | None = None
        self._last_fingerprint: str = ""
        self._prev_round_id: str | None = None
        self._fixed_gameinfo_init: bool = False
        self._poll_count: int = 0  # 轮询计数，用于定期健康检查

    # ── DataSource 接口 ──────────────────────────────────

    @property
    def id(self) -> str:
        return "cdp_source"

    @property
    def name(self) -> str:
        return "CDP Source"

    @property
    def status(self) -> SourceStatus:
        return self._status

    def set_on_status_change(self, callback: Callable[[SourceStatusEvent], None]):
        self._on_status_change = callback

    def _set_status(self, status: SourceStatus):
        self._status = status
        if self._on_status_change:
            self._on_status_change({"source_id": self.id, "status": status})

    # ── 启动 / 停止 ──────────────────────────────────────

    async def start(self) -> AsyncIterator[MarketTick]:
        """启动采集，进入 CDP DOM 轮询循环。

        自动管理 Chrome 进程和 CDP 连接的生命周期，
        断线后自动重连，无限运行直到 stop() 被调用。
        """
        setup_logging()
        self._set_status("running")
        reconnect_delay = 1.0

        while self._status == "running":
            try:
                # ── 连接阶段 ──
                if self._cdp is None:
                    yield await self._ensure_connected(reconnect_delay)
                    reconnect_delay = 1.0
                    continue  # 连上后进入采集循环

                # ── 定期健康检查（每 30 轮）──
                self._poll_count += 1
                if self._poll_count % 30 == 0 and self._chrome:
                    hc = await self._chrome.health_check()
                    if hc == "dead":
                        logger.warning("Chrome health check: dead, reconnecting...")
                        self._cdp = None
                        self._extractor = None
                        self._fixed_gameinfo_init = False
                        continue  # 主循环会调用 _ensure_connected 重连

                # ── 采集阶段 ──
                async for tick in self._poll_loop():
                    yield tick
                    break  # poll_loop 退出表示断线，触发重连

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("CDPSource error: {}", e)
                self._set_status("error")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)
                self._cdp = None
                self._extractor = None
                self._fixed_gameinfo_init = False

        logger.info("CDPSource main loop ended")

    async def stop(self):
        """停止采集，释放资源。"""
        self._set_status("stopped")
        if self._cdp:
            await self._cdp.disconnect()
            self._cdp = None
        if self._chrome:
            await self._chrome.stop()
            self._chrome = None
        self._extractor = None
        logger.info("CDPSource stopped")

    # ── 内部方法 ─────────────────────────────────────────

    async def _ensure_connected(self, delay: float) -> MarketTick:
        """确保已连接到 Chrome CDP，未连则尝试建立连接。"""
        # auto 模式：启动 Chrome（如果还未启动）
        if self._chrome is None:
            self._chrome = ChromeManager(chrome_path=self._chrome_path)

        if not self._chrome.is_attached:
            await self._chrome.start()

        # 连接 CDP
        try:
            self._cdp = CDPSession(self._chrome.cdp_url)
            ok = await self._cdp.connect()
            if ok:
                self._extractor = DOMExtractor(self._cdp)
                self._fixed_gameinfo_init = False
                self._last_fingerprint = ""
                logger.info("CDP connected: {}", self._chrome.cdp_url)
            else:
                logger.warning("CDP connect failed, retrying in {:.0f}s", delay)
                self._cdp = None
                await asyncio.sleep(delay)
        except Exception as e:
            logger.warning("CDP connect error: {}", e)
            self._cdp = None
            await asyncio.sleep(delay)

        # 返回占位 tick（连接成功但还没数据）
        return self._adapter.create_tick(
            result="N", score=0, table_id=0,
            confidence=0,
        )

    async def _poll_loop(self) -> AsyncIterator[MarketTick]:
        """单次 CDP 轮询循环：DOM 提取 → 解析 → 产出 MarketTick。
        返回时表示连接中断，由主循环触发重连。
        """
        if not self._cdp or not self._extractor:
            return

        # 首次或换台后提取固定信息
        if not self._fixed_gameinfo_init:
            fixed = await self._extractor.extract_fixed_info()
            if fixed:
                self._fixed_gameinfo_init = True
                logger.info("Fixed info extracted: {}", fixed.get("game_name", ""))

        t0 = time.monotonic()

        try:
            # 提取动态 DOM 数据
            raw = await self._extractor.extract_dynamic()
            if not raw:
                await asyncio.sleep(self._interval)
                return

            # 解析动态数据
            dyn = parse_dynamic(raw)

            # 检测结果
            result = detect_result(dyn)
            long_score = 0
            short_score = 0
            if dyn.get("cards"):
                long_score = dyn["cards"].get("banker_total", 0) or 0
                short_score = dyn["cards"].get("player_total", 0) or 0

            # 倒计时转 int | None
            countdown_raw = raw.get("countdownText", "")
            countdown: int | None = None
            if countdown_raw:
                try:
                    countdown = int(countdown_raw)
                except (ValueError, TypeError):
                    pass

            # 指纹去重
            fp = make_fingerprint(dyn, result)
            if fp == self._last_fingerprint:
                wait = max(0, self._interval - (time.monotonic() - t0))
                await asyncio.sleep(wait)
                return

            self._last_fingerprint = fp

            # 换台检测：tableName 变化时重置固定信息
            new_table_name = raw.get("tableName", "")
            old_table_name = ""
            if self._extractor.fixed_info:
                old_table_name = self._extractor.fixed_info.get("game_name", "")
            if new_table_name and new_table_name != old_table_name:
                logger.info("Table switch detected: '{}' → '{}'", old_table_name, new_table_name)
                self._extractor.reset_fixed_info()
                self._fixed_gameinfo_init = False
                return

            # 新局检测
            rid = dyn.get("round_id", "")
            if rid and rid != "-" and rid != self._prev_round_id:
                self._prev_round_id = rid
                logger.info("New round: {} | table={}", rid,
                            raw.get("urlTableId", "?"))

            # 提取路纸序列（从 raw 中取，暂时留空）
            road_seq = []

            # 构建 CDP 原始数据 metadata（未显式展开的补充数据）
            fixed = self._extractor.fixed_info or {}
            boot = dyn.get("boot_stats", {})
            cdp_meta = {
                "table_type": fixed.get("gameplay", ""),
                "player_cards": raw.get("playerCards", ""),
                "banker_cards": raw.get("bankerCards", ""),
                "server_time": raw.get("timeDisplay", ""),
                "dealer": fixed.get("dealer", ""),
                "bet_limit": fixed.get("bet_limit", ""),
                "total_rounds": boot.get("total_rounds", 0),
                "streaks": raw.get("streaks", []),
            }

            # 通过 Adapter 产出 MarketTick
            tick = self._adapter.create_tick(
                result=result or "N",
                long_score=long_score,
                short_score=short_score,
                table_id=raw.get("urlTableId", 0),
                counter_id=fixed.get("table_id", ""),
                trade_seq=rid,
                status=raw.get("status", ""),
                countdown=countdown,
                table_type_id=raw.get("urlGameType", 0),
                road_sequence=road_seq,
                confidence=0.99 if result else 0.0,
                bets=dyn.get("bets"),
                extra_metadata=cdp_meta,
            )
            yield tick

        except Exception as e:
            logger.warning("DOM poll error: {}", e)
            # WebSocket 断开由外层捕获
            raise

        # 控制轮询间隔
        elapsed = time.monotonic() - t0
        wait = max(0, self._interval - elapsed)
        await asyncio.sleep(wait)
