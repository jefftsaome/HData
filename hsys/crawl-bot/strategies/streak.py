"""streak 策略 — 长庄/长闲"反龙因素"定向采集（crawl-bot 策略之一）。

架构（多账号分层，单进程 asyncio）:

    发现层  账号[0]：大厅常驻连接订阅 10052，逐桌跟踪路纸，
            末尾连胜 >= min_streak 即产生候选（实时流，非定时刷新）
    监控层  账号[1:]：启动即登录（与发现层并行，不等首个候选，
            消除登录耗时造成的入场竞速窗口），TableMonitor 动态
            进桌（add_table）；进桌前以发现层最新路纸复核——
            已断跳过、长度变了重对齐、已反转按新方向入场；
            每局 107 结算时判定 延续/反/和，直到反了或删失即离桌
    落库层  SQLite（crawl-bot/schema.sql v2 + streak 专用表）

结局标签:
    broke              反了（首个反向非和局）
    censored_boot      靴结束/换靴（删失）
    censored_disconnect 掉线兜底（删失；未细化原因或进程强杀遗留）
    censored_network   掉线后探测重进成功 → 我方网络问题（删失）
    censored_kick      掉线后探测重进失败 → 疑似平台踢出且封锁（删失，重点信号）
    censored_manual    人为 Ctrl+C 优雅退出（删失）

本模块由 crawl-bot/main.py 以 --strategy streak 调用，配置来自
config.json（accounts/entry_url/geepass_token/jfbym_token/db_path 等），
不读取任何 scripts/ 模块。
"""
from __future__ import annotations

import asyncio
import json
import time

from loguru import logger

import hdata.client as hc
from hdata.client import GameClient, road_streak, round_result_token
from hdata.protocol.codec import build_message, extract_param
from hdata.protocol.roadpaper import decode_bead_plate
from hdata.proxy import ProxyPool
from store import Store, now_ms

# GameClient 连接配置键（从 cfg 提取后 ** 展开传给 GameClient）
CONN_KEYS = ("entry_url", "geepass_token", "jfbym_token")

# ── 配置 ────────────────────────────────────────────────

BACCARAT_IDS = {2001, 2002, 2003, 2004, 2005, 2014, 2016,
                2027, 2030, 2034, 2038}          # 只监控百家乐系
READD_COOLDOWN_S = 120      # 收场后同桌冷却期（秒），防抖动重进
PROBE_TIMEOUT_S = 12        # 掉线探测重进等 401 快照的总窗口（秒）
LOBBY_WRITE_INTERVAL_S = 60 # 同桌大厅采样最短间隔（路纸无变化时）
STATUS_EVERY_S = 60         # 运行状态打印间隔


# ── 发现层：大厅常驻订阅 ────────────────────────────────


class LobbyWatcher:
    """账号[0] 的大厅订阅：跟踪每桌路纸，发现连胜桌。"""

    def __init__(self, cred: dict, store: Store,
                 candidates: asyncio.Queue, min_streak: int,
                 conn_cfg: dict):
        self._cred = cred
        self._store = store
        self._out = candidates
        self._min = min_streak
        self._conn_cfg = conn_cfg      # entry_url/geepass_token/jfbym_token
        self._flat: dict[int, str] = {}          # 桌→最新路纸
        self.good_roads: dict[int, list] = {}    # 桌→平台好路标记（共享给落库）
        self.meta: dict[int, dict] = {}          # 桌→10053 元数据
        self.online: dict[int, dict] = {}        # 桌→最新 tableOnline（在线人数/总注额）
        self._last_write: dict[int, float] = {}
        self.active: set[int] = set()            # 监控中（由主循环维护）
        self.cooldown: dict[int, float] = {}     # 桌→冷却截止 ts

    async def run(self):
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[发现] 连接异常 {e}，5s 后重连")
                await asyncio.sleep(5)

    async def _run_once(self):
        client = GameClient(proxy=self._cred.get("proxy"),
                            **self._conn_cfg)
        await client.login(self._cred["account"], self._cred["password"])
        logger.info(f"[发现] {self._cred['account']} 登录成功，订阅大厅")
        async with hc._WSConnection(client._session,
                                    on_before_connect=client._refresh_cb) as conn:
            # 先补一次桌名元数据（10089→10053），给 tables 表
            try:
                self.meta = await conn.fetch_table_meta()
                for tid, m in self.meta.items():
                    self._store.upsert_table({
                        "table_id": tid, "table_name": m.get("tableName"),
                        "game_type_id": m.get("gameTypeId"),
                        "game_type_name": m.get("gameTypeName"),
                        "casino_id": m.get("gameCasinoId"),
                        "casino_name": m.get("gameCasinoName"),
                        "physics_no": m.get("physicsTableNo")})
                logger.info(f"[发现] 桌台元数据 {len(self.meta)} 条已入库")
            except Exception as e:
                logger.warning(f"[发现] 元数据补拉失败（不影响监控）: {e}")
            # 订阅 10052 持续推送
            await conn.send(build_message(
                hc._QS_TABLE_LIST_ALL, {"labelTypeId": 1},
                player_id=conn._player_id, game_type_id=2013,
                service_type_id=hc.OT_HALL))
            while True:
                frame = await conn.recv()
                if not frame or frame.get("protocolId") != 10052:
                    continue
                info = extract_param(frame) or {}
                data = info.get("param") or info.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except Exception:
                        continue
                gtm = (data or {}).get("gameTableMap") or {}
                self._handle_snapshot(gtm)

    def _handle_snapshot(self, gtm: dict):
        rows = []
        for tid_s, t in gtm.items():
            try:
                tid = int(tid_s)
            except (TypeError, ValueError):
                continue
            gt = t.get("gameTypeId")
            if gt not in BACCARAT_IDS:
                continue
            rp = t.get("roadPaper") or {}
            b64 = rp.get("beatPlateRoad")
            flat = ""
            if b64:
                try:
                    flat = "".join(decode_bead_plate(b64)["flat"])
                except Exception:
                    pass
            gr = [hc.GOOD_ROAD_NAMES.get(p.get("goodRoadType"),
                                         str(p.get("goodRoadType")))
                  for p in (t.get("goodRoadPoints") or [])
                  if isinstance(p, dict) and p.get("goodRoadFlag")]
            self.good_roads[tid] = gr
            ton = t.get("tableOnline") or {}
            if ton:
                self.online[tid] = ton         # 共享给监控侧落库
            changed = flat != self._flat.get(tid, "")
            if changed:
                self._flat[tid] = flat
            # 大厅采样落库：路纸变化 或 间隔超 60s
            last_w = self._last_write.get(tid, 0)
            if changed or time.time() - last_w > LOBBY_WRITE_INTERVAL_S:
                rows.append({
                    "table_id": tid,
                    "online_number": ton.get("onlineNumber"),
                    "total_amount": ton.get("totalAmount"),
                    "game_status": t.get("gameStatus"),
                    "boot_no": t.get("bootNo"),
                    "road_flat": flat, "good_roads": gr})
                self._last_write[tid] = time.time()
            # 连胜检测
            if not changed or tid in self.active:
                continue
            if time.time() < self.cooldown.get(tid, 0):
                continue
            side, n = road_streak(flat)
            if side and n >= self._min:
                m = self.meta.get(tid) or {}
                logger.info(f"[发现] 连胜桌 {tid} "
                            f"{m.get('tableName', '?')} {side}×{n} "
                            f"标记={gr}")
                self._out.put_nowait({
                    "table_id": tid, "game_type_id": gt,
                    "table_name": m.get("tableName", ""),
                    "side": side, "length": n,
                    "via": "good_roads" if
                    (("长庄" if side == "B" else "长闲") in gr)
                    else "local_streak"})
        if rows:
            try:
                self._store.insert_lobby(rows)
            except Exception as e:
                logger.warning(f"[发现] 大厅采样落库失败: {e}")


# ── 监控层：多账号 TableMonitor + 连胜状态机 ─────────────


class Episode:
    """一条进行中的连胜事件。"""

    __slots__ = ("id", "table_id", "side", "length", "start_length",
                 "boot_index_max", "account", "game_type_id")

    def __init__(self, episode_id, table_id, side, length, account,
                 game_type_id=None):
        self.id = episode_id
        self.table_id = table_id
        self.side = side            # "B" / "P"
        self.length = length        # 当前连胜数（随 107 更新）
        self.start_length = length
        self.boot_index_max = 0     # 靴内局序峰值（换靴检测用）
        self.account = account
        self.game_type_id = game_type_id  # 掉线探测重进要用


class StreakMonitor:
    def __init__(self, creds: list[dict], store: Store,
                 watcher: LobbyWatcher, min_streak: int,
                 conn_cfg: dict | None = None):
        self._creds = creds
        self._store = store
        self._watcher = watcher
        self._min = min_streak
        self._conn_cfg = conn_cfg or {}
        self._client: GameClient | None = None
        self.mon = None                      # TableMonitor（懒创建）
        self.episodes: dict[int, Episode] = {}
        self._last_bet: dict[int, dict] = {}     # 桌→最近一次 110
        self._last_round: dict[int, dict] = {}   # 桌→最近一次 104
        self._raw_buf = 0
        self._pending_probe = []        # 掉线待探测 [(tid, ep_id, game_type_id)]
        self.stats = {"rounds": 0, "broke": 0, "censored": 0}
        self.run_id = 0

    async def ensure_monitor(self):
        """启动即创建监控（与发现层并行登录全部监控账号）。

        不在乎打码成本的前提下，提前登录把候选入场延迟从 ~60s
        （打码登录）压到 1~2s（纯进桌指令），竞速窗口基本消除。
        依赖 hdata 空表建分片能力：monitor_tables([]) 为每个账号
        各建一条连接分片，后续 add_table 自动按负载均衡分配。
        """
        if self.mon is not None:
            return
        logger.info(f"[监控] 启动即登录 {len(self._creds)} "
                    "个监控账号（首次需打码，约1分钟）…")
        first, rest = self._creds[0], self._creds[1:]
        self._client = GameClient(proxy=first.get("proxy"),
                                  **self._conn_cfg)
        await self._client.login(first["account"], first["password"])
        self.mon = await self._client.monitor_tables(
            [], accounts=rest, kick_policy="stay")
        await self.mon.__aenter__()
        self.run_id = self._store.start_run(
            "+".join(c["account"] for c in self._creds), "L2进桌",
            note=f"streak>={self._min}")
        logger.info(f"[监控] TableMonitor 就绪"
                    f"（{len(self.mon._shards)} 个账号分片）")

    async def _open_episode(self, cand: dict):
        """进桌 + 开 episode（候选桌 → 在监状态的唯一入口）。

        入场复核：候选产生到进桌之间牌局可能已推进（尤其监控账号
        尚未就绪时队列里积压的候选），一律以发现层最新路纸为准——
        连胜已断则跳过、长度变了则重对齐、已反转则按新方向入场。
        """
        tid = cand["table_id"]
        if tid in self.episodes:
            return
        flat = self._watcher._flat.get(tid, "")
        side, n = road_streak(flat)
        if not side or n < self._min:
            logger.info(f"[监控] 入场复核未过 {tid} "
                        f"{cand.get('table_name')}（候选 "
                        f"{cand['side']}×{cand['length']}，最新 "
                        f"{side or '—'}×{n}），跳过")
            return
        if (side, n) != (cand["side"], cand["length"]):
            logger.info(f"[监控] 入场复核对齐 {tid}：候选 "
                        f"{cand['side']}×{cand['length']} → "
                        f"最新 {side}×{n}")
        gr = self._watcher.good_roads.get(tid, [])
        via = ("good_roads" if
               (("长庄" if side == "B" else "长闲") in gr)
               else "local_streak")
        await self.mon.add_table(
            {"table_id": tid, "game_type_id": cand["game_type_id"]})
        ep_id = self._store.open_episode({
            "table_id": tid, "table_name": cand.get("table_name"),
            "game_type_id": cand.get("game_type_id"),
            "side": side, "detected_via": via,
            "start_length": n,
            "account": "+".join(c["account"] for c in self._creds)})
        self.episodes[tid] = Episode(
            ep_id, tid, side, n, self._creds[0]["account"],
            cand.get("game_type_id"))
        self._watcher.active.add(tid)
        logger.info(f"[监控] 进桌 {tid} {cand.get('table_name')} "
                    f"{side}×{n} "
                    f"(episode#{ep_id}，在监 {len(self.episodes)} 桌)")

    # ── 主循环：候选消费 + 事件消费双任务并发 ──

    async def _serve(self, candidates: asyncio.Queue):
        """监控建立后的服务循环；事件流异常即抛出，由外层重建。"""
        cand_t = asyncio.create_task(self._cand_loop(candidates))
        ev_t = asyncio.create_task(self._ev_loop())
        # 掉线探测与事件消费并发：401 快照只在事件流被消费时填充
        probe_t = (asyncio.create_task(self._probe_pending())
                   if self._pending_probe else None)
        tasks = {cand_t, ev_t} | ({probe_t} if probe_t else set())
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            for t in done:
                exc = t.exception()
                if exc:
                    raise exc
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def _cand_loop(self, candidates: asyncio.Queue):
        """持续消费发现层候选（独立任务，不与事件流互相阻塞）。"""
        while True:
            cand = await candidates.get()
            try:
                await self._open_episode(cand)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"[监控] 进桌失败 {cand.get('table_id')}: {e}")

    async def _ev_loop(self):
        """持续消费事件流；分片掉线（error 事件或流意外结束）即抛异常。"""
        mon = self.mon
        async for ev in mon.events():
            if ev.get("type") == "error":
                raise RuntimeError(
                    f"分片事件流错误: {(ev.get('data') or {}).get('error')}")
            self._dispatch(ev)
        if not mon._closed:
            raise RuntimeError("事件流意外结束（疑似分片掉线）")

    async def _close(self, tid: int, outcome: str,
                     round_id: int | None = None):
        ep = self.episodes.pop(tid, None)
        if not ep:
            return
        self._store.close_episode(ep.id, outcome, round_id, ep.length)
        self._watcher.active.discard(tid)
        self._watcher.cooldown[tid] = time.time() + READD_COOLDOWN_S
        self._last_bet.pop(tid, None)
        self._last_round.pop(tid, None)
        try:
            await self.mon.leave_table(tid)
        except Exception:
            pass
        key = "broke" if outcome == "broke" else "censored"
        self.stats[key] += 1
        logger.info(f"[监控] 离桌 {tid} episode#{ep.id} {outcome} "
                    f"峰值{ep.length} (broke={self.stats['broke']})")

    async def _probe_pending(self):
        """掉线重连后逐桌探测性重进，细化删失原因（censored_disconnect 兜底
        → censored_network / censored_kick）。

        判据：add_table 只发指令不等确认，能否真进桌以 401 快照落地为准
        （PROBE_TIMEOUT_S 总窗口）。能进 → 桌台本身可进，是我方网络问题；
        进不去 → 疑似平台踢出且封锁该桌，重点信号。

        探测进桌后统一离场，交还发现层按常规流程重新发现（断线期间
        牌局已缺失，不续接旧 episode）；若发现层已抢先重新入场则
        跳过离场，不拆新台。未跑完被打断的桌保留兜底标签。
        """
        probes, self._pending_probe = self._pending_probe, []
        if not probes:
            return
        logger.info(f"[监控] 探测性重进 {len(probes)} 张掉线桌台…")
        sent = []                       # (tid, ep_id) 指令已发出
        for tid, ep_id, gtid in probes:
            if not gtid:
                continue                # 缺 game_type_id 无法探测，保留兜底
            try:
                await self.mon.add_table(
                    {"table_id": tid, "game_type_id": gtid})
                sent.append((tid, ep_id))
            except Exception:
                self._store.update_episode_outcome(ep_id, "censored_kick")
                logger.warning(f"[监控] 探测重进拒绝 桌{tid} "
                               f"episode#{ep_id} → censored_kick"
                               "（疑似被踢且封锁）")
        remaining = dict(sent)
        deadline = time.monotonic() + PROBE_TIMEOUT_S
        while remaining and self.mon is not None \
                and time.monotonic() < deadline:
            for tid in list(remaining):
                if tid in self.mon.snapshots:
                    ep_id = remaining.pop(tid)
                    self._store.update_episode_outcome(
                        ep_id, "censored_network")
                    logger.info(f"[监控] 探测重进成功 桌{tid} "
                                f"episode#{ep_id} → censored_network"
                                "（我方网络问题）")
            if remaining:
                await asyncio.sleep(0.5)
        for tid, ep_id in remaining.items():
            self._store.update_episode_outcome(ep_id, "censored_kick")
            logger.warning(f"[监控] 探测重进超时 桌{tid} "
                           f"episode#{ep_id} → censored_kick"
                           "（疑似被踢且封锁）")
        for tid, _ in sent:
            if tid in self.episodes:
                continue                # 发现层已重新入场，别拆新台
            try:
                await self.mon.leave_table(tid)
            except Exception:
                pass

    # ── 事件处理 ──

    def _on_104(self, tid: int, d: dict):
        prev = self._last_round.get(tid)
        self._last_round[tid] = d
        bi = d.get("bootIndex")
        ep = self.episodes.get(tid)
        if ep and isinstance(bi, int):
            if ep.boot_index_max >= 3 and bi == 1:
                # 换靴：局序从高位归 1 → 删失收场
                asyncio.create_task(self._close(tid, "censored_boot",
                                                d.get("roundId")))
            else:
                ep.boot_index_max = max(ep.boot_index_max, bi)

    def _on_106(self, tid: int, d: dict):
        rid = d.get("roundId")
        infos = d.get("currentRoundExtInfos") or []
        if not infos and d.get("cardNumber") is not None:
            infos = [d]
        for c in infos:
            cn = c.get("cardNumber")
            if rid and cn is not None:
                try:
                    self._store.insert_card(
                        rid, str(c.get("cardOwner", "?")),
                        int(c.get("ownerIndex") or c.get("cardIndex") or 0),
                        int(cn))
                except Exception:
                    pass
        self._store.commit()

    def _on_110(self, tid: int, d: dict):
        self._last_bet[tid] = d

    def _on_107(self, tid: int, d: dict):
        rid = d.get("roundId")
        rr = d.get("roundResult", "")
        token = round_result_token(rr)
        if not rid or not token:
            return
        try:
            b_pt, p_pt = (int(x) for x in rr.split(";", 1))
        except Exception:
            b_pt = p_pt = None
        bet = self._last_bet.get(tid) or {}
        pools = bet.get("jackpotPoolInfos") or []
        boot = d.get("bootReport")
        snap = self.mon.snapshots.get(tid) or {}
        last104 = self._last_round.get(tid) or {}
        road_after = self.mon.road_flat(tid)
        # 在线人数：401 快照无 tableOnline，用大厅 10052 的最新值（1.4s 帧率）
        online_no = ((self._watcher.online.get(tid) or {}).get("onlineNumber")
                     or (snap.get("tableOnline") or {}).get("onlineNumber"))
        # 主表 rounds（round_id 去重，重进/多账号安全）
        is_new = self._store.insert_round({
            "round_id": rid, "table_id": tid,
            "game_type_id": snap.get("gameTypeId"),
            "round_no": d.get("roundNo") or last104.get("roundNo"),
            "boot_no": snap.get("bootNo"),
            "boot_index": last104.get("bootIndex"),
            "result": token, "banker_points": b_pt, "player_points": p_pt,
            "road_flat_after": road_after,
            "good_roads": self._watcher.good_roads.get(tid, []),
            "player_count": bet.get("currentRoundPlayerCount"),
            "total_amount": bet.get("currentRoundPlayerAmountCount"),
            "online_number": online_no,
            "ts_bet_end": last104.get("countdownEndTime"),
            "ts_server": d.get("serverTime"),
            "ts_settle": now_ms(),
            "dealer_name": snap.get("dealerName"),
            "casino_id": snap.get("gameCasinoId")})
        if is_new:
            self.stats["rounds"] += 1
        self._store.insert_bet_points(rid, pools, boot,
                                      d.get("winPoints"))
        # 连胜状态机
        ep = self.episodes.get(tid)
        if not ep:
            return
        same = (ep.side == "B" and token in ("B", "B6")) or \
               (ep.side == "P" and token == "P")
        len_before = ep.length                    # 本局结果前的连胜长度
        if token == "T":
            outcome = "continue"                  # 和：不断也不算
        elif same:
            ep.length += 1
            self._store.touch_episode_length(ep.id, ep.length)
            outcome = "continue"
        else:
            outcome = "broke"
        self._store.insert_streak_round({
            "episode_id": ep.id, "round_id": rid, "ts_settle": now_ms(),
            "streak_len_before": len_before,
            "result": token, "outcome": outcome,
            "banker_points": b_pt, "player_points": p_pt,
            "total_amount": bet.get("currentRoundPlayerAmountCount"),
            "player_count": bet.get("currentRoundPlayerCount"),
            "online_number": online_no,
            "bet_json": pools, "payout_json": boot})
        if outcome == "broke":
            asyncio.create_task(self._close(tid, "broke", rid))

    def _dispatch(self, ev: dict):
        """单条事件：原始留底 + 按协议路由。"""
        raw_types = {104, 106, 107, 110, 116, 171, 102, 123}
        tid, pid = ev.get("table_id"), ev.get("protocol_id")
        d = ev.get("data") or {}
        if pid in raw_types:
            self._store.insert_event(
                tid, pid, ev.get("type", ""), d.get("roundId"),
                d, self._creds[0]["account"])
            self._raw_buf += 1
            if self._raw_buf >= 50:
                self._store.commit()
                self._raw_buf = 0
        if pid == 104:
            self._on_104(tid, d)
        elif pid == 106:
            self._on_106(tid, d)
        elif pid == 110:
            self._on_110(tid, d)
        elif pid == 107:
            self._on_107(tid, d)
        # kick 事件：stay 策略自动重进，无需处理；
        # follow_system 的 dropped 事件本程序不使用

    async def run(self, candidates: asyncio.Queue):
        """外层循环：启动即建监控（与发现层并行）→ 服务；
        事件流断则全删失重建。候选在监控就绪前积压在队列里，
        就绪后由 _cand_loop 逐一消费（入场复核兜底过期候选）。"""
        while True:
            if self.mon is None:
                try:
                    await self.ensure_monitor()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[监控] 初始化失败: {e}，60s 后重试")
                    try:                        # 防半建连泄漏
                        if self.mon:
                            await self.mon.aclose()
                    except Exception:
                        pass
                    self.mon = None
                    await asyncio.sleep(60)
                    continue
            try:
                await self._serve(candidates)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[监控] 事件流中断: {e}，10s 后重建监控")
            # 掉线：先快照待探测列表，再全部记删失兜底；
            # 监控重建成功后由 _probe_pending 细化删失原因
            self._pending_probe = [
                (tid, ep.id, ep.game_type_id)
                for tid, ep in self.episodes.items()]
            for tid in list(self.episodes):
                await self._close(tid, "censored_disconnect")
            try:
                if self.mon:
                    await self.mon.aclose()
            except Exception:
                pass
            self.mon = None
            await asyncio.sleep(10)

    async def shutdown(self):
        """优雅退出：逐桌收场离场（单桌异常不阻断）、断连、收尾 run。"""
        for tid in list(self.episodes):
            try:
                await self._close(tid, "censored_manual")
            except Exception:
                pass
        try:
            if self.mon:
                await self.mon.aclose()
        except Exception:
            pass
        if self.run_id:
            try:
                self._store.stop_run(self.run_id)
            except Exception:
                pass


# ── 主入口 ──────────────────────────────────────────────


async def _graceful_shutdown(tasks: list[asyncio.Task],
                             monitor: "StreakMonitor", store: Store,
                             started: float):
    """Ctrl+C 优雅退出：停任务 → 离桌断连 → 关库，全程尽量不被打断。

    顺序讲究：先取消任务并**等它们收尾**（归还连接、停止写库），
    再关监控（逐桌离场 + 断连），最后提交关库——避免任务半空中
    写已关闭的库。二次 Ctrl+C 只会跳过离桌等可放弃的动作，
    关库一定执行（下次启动另有 close_stale_episodes 兜底）。
    """
    logger.info("[退出] 收到退出信号，开始清理（几秒，请勿重复 Ctrl+C）…")
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), 10)
    except (Exception, KeyboardInterrupt):
        pass
    try:
        await asyncio.wait_for(monitor.shutdown(), 30)
    except KeyboardInterrupt:
        logger.warning("[退出] 清理被二次 Ctrl+C 打断，直接关库")
    except Exception as e:
        logger.warning(f"[退出] 监控清理异常"
                       f"（{type(e).__name__}: {e}），继续关库")
    try:
        store.commit()
        store.close()
    except Exception as e:
        logger.warning(f"[退出] 关库异常: {e}")
    mins = (time.time() - started) / 60
    logger.info(
        f"[退出] 清理完成：运行 {mins:.1f} 分钟 | "
        f"已采局 {monitor.stats['rounds']} | 反 {monitor.stats['broke']} | "
        f"删失 {monitor.stats['censored']} | "
        "未完结 episode 已记 censored_manual")


def _mask_proxy(url: str) -> str:
    """日志用代理脱敏（隐藏账密，只留 host:port）。"""
    return url.split("@")[-1] if "@" in url else url


async def run_strategy(cfg: dict):
    """streak 策略主入口（配置驱动）。

    cfg 需要的键：accounts / entry_url / geepass_token / jfbym_token /
    db_path；可选：min_streak(4) / max_accounts(0=全部) / proxies /
    proxy_cap(10) / purge_raw_days(7，0=永不清理)。
    """
    min_streak = int(cfg.get("min_streak", 4))
    max_accounts = int(cfg.get("max_accounts", 0))
    proxies_path = cfg.get("proxies") or ""
    proxy_cap = int(cfg.get("proxy_cap", 10))
    purge_days = int(cfg.get("purge_raw_days", 7))
    conn_cfg = {k: cfg[k] for k in CONN_KEYS if cfg.get(k)}

    started = time.time()
    store = Store(cfg["db_path"])
    if purge_days > 0:
        store.purge_raw(purge_days)
    stale = store.close_stale_episodes()
    if stale:
        logger.info(f"[启动] 清理上次遗留未完结 episode {stale} 条"
                    "（记 censored_disconnect）")

    # ── 账号准备（拷贝，避免污染配置对象）──
    creds = [dict(a) for a in cfg["accounts"]]

    # ── 代理分配：提供了代理就只用代理（不混用本机直连）──
    if proxies_path:
        pool = ProxyPool.from_file(proxies_path, cap_per_proxy=proxy_cap)
        await pool.health_check()
        if not pool.alive:
            logger.error("[代理] 全部出口探测失败；规则为'提供了代理就只用"
                         "代理'，不回落直连，退出")
            return
        wanted = [c["account"] for c in creds]      # [0] 发现层优先分配
        mapping = pool.assign(wanted)
        used: list[dict] = []
        for c in creds:
            p = mapping.get(c["account"])
            if p:
                c["proxy"] = p
                used.append(c)
            else:
                logger.warning(f"[代理] 出口容量不足，账号弃用: "
                               f"{c['account']}（可加代理或调大 --proxy-cap）")
        if not mapping.get(creds[0]["account"]):
            logger.error("[代理] 发现层账号未分到出口，退出")
            return
        creds = used
        for c in creds:
            logger.info(f"[代理] {c['account']} → {_mask_proxy(c['proxy'])}")
    else:
        logger.info("[代理] 未配置，全部账号走本机直连出口")

    candidates: asyncio.Queue = asyncio.Queue()
    watcher = LobbyWatcher(creds[0], store, candidates, min_streak, conn_cfg)
    mon_creds = (creds[1:1 + max_accounts] if max_accounts > 0
                 else creds[1:])
    monitor = StreakMonitor(mon_creds, store, watcher, min_streak, conn_cfg)
    watch_task = asyncio.create_task(watcher.run())
    mon_task = asyncio.create_task(monitor.run(candidates))

    async def status_loop():
        while True:
            await asyncio.sleep(STATUS_EVERY_S)
            logger.info(
                f"[状态] 在监 {len(monitor.episodes)} 桌 | "
                f"已采局 {monitor.stats['rounds']} | "
                f"反 {monitor.stats['broke']} | "
                f"删失 {monitor.stats['censored']} | "
                f"大厅路纸 {len(watcher._flat)} 桌")

    st_task = asyncio.create_task(status_loop())
    try:
        await asyncio.gather(watch_task, mon_task, st_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await _graceful_shutdown(
            [watch_task, mon_task, st_task], monitor, store, started)


# ── crawl-bot 策略接口 ────────────────────────────────────

STRATEGY_INFO = "长龙采集：大厅发现连胜桌→多账号进桌监控到断龙/删失"


def register_args(ap) -> None:
    """streak 策略的命令行覆盖参数（config.json 未覆盖全时用）。"""
    ap.add_argument("--min", type=int, default=0, dest="min_streak",
                    help="覆盖配置里的连胜入场阈值")
    ap.add_argument("--max-accounts", type=int, default=None,
                    help="覆盖配置里的监控账号上限（0=全部）")
    ap.add_argument("--proxies", default=None,
                    help="覆盖配置里的代理列表 JSON 文件路径")
    ap.add_argument("--proxy-cap", type=int, default=None,
                    help="覆盖配置里的每代理出口连接预算")


def run(cfg: dict, args) -> None:
    """策略同步入口：应用命令行覆盖后跑 asyncio 主循环。"""
    for key in ("min_streak", "max_accounts", "proxies", "proxy_cap"):
        v = getattr(args, key, None)
        if v:
            cfg[key] = v
    try:
        asyncio.run(run_strategy(cfg))
    except KeyboardInterrupt:
        pass
