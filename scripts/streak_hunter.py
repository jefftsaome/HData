"""StreakHunter — 长庄/长闲"反龙因素"定向采集程序。

架构（多账号分层，单进程 asyncio）:

    发现层  账号[0]：大厅常驻连接订阅 10052，逐桌跟踪路纸，
            末尾连胜 >= STREAK_MIN 即产生候选（实时流，非定时刷新）
    监控层  账号[1:]：TableMonitor 动态进桌（add_table），
            每局 107 结算时判定 延续/反/和，直到反了或删失即离桌
    落库层  SQLite（docs/schema.sql v2 + streak 专用表）

结局标签:
    broke              反了（首个反向非和局）
    censored_boot      靴结束/换靴（删失）
    censored_disconnect 掉线/程序退出（删失）

运行:
    uv run python scripts/streak_hunter.py           # 前台运行
    uv run python scripts/streak_hunter.py --min 5   # 连胜阈值 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

import hdata.client as hc
from hdata.client import GameClient, road_streak, round_result_token
from hdata.protocol.codec import build_message, extract_param
from hdata.protocol.roadpaper import decode_bead_plate
from scripts.streak_store import Store, now_ms

# ── 配置 ────────────────────────────────────────────────

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"

ACCOUNTS = [
    {"account": "lshaoxia1", "password": "lshaoxia2"},   # [0] 发现层
    {"account": "linbing1", "password": "linbing2"},
    {"account": "lds001", "password": "lds19830413"},
    {"account": "lds002", "password": "lds19830413"},
    {"account": "lds003", "password": "lds19830413"},
    {"account": "lds004", "password": "lds19830413"},
    {"account": "lds005", "password": "lds19830413"},
    {"account": "lds006", "password": "lds19830413"},
    {"account": "lds007", "password": "lds19830413"},
    {"account": "lds008", "password": "lds19830413"},
    {"account": "sushizhen1", "password": "sushizhen2"},
]

DB_PATH = "data/streak.db"
BACCARAT_IDS = {2001, 2002, 2003, 2004, 2005, 2014, 2016,
                2027, 2030, 2034, 2038}          # 只监控百家乐系
READD_COOLDOWN_S = 120      # 收场后同桌冷却期（秒），防抖动重进
LOBBY_WRITE_INTERVAL_S = 60 # 同桌大厅采样最短间隔（路纸无变化时）
STATUS_EVERY_S = 60         # 运行状态打印间隔


# ── 发现层：大厅常驻订阅 ────────────────────────────────


class LobbyWatcher:
    """账号[0] 的大厅订阅：跟踪每桌路纸，发现连胜桌。"""

    def __init__(self, cred: dict, store: Store,
                 candidates: asyncio.Queue, min_streak: int):
        self._cred = cred
        self._store = store
        self._out = candidates
        self._min = min_streak
        self._flat: dict[int, str] = {}          # 桌→最新路纸
        self.good_roads: dict[int, list] = {}    # 桌→平台好路标记（共享给落库）
        self.meta: dict[int, dict] = {}          # 桌→10053 元数据
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
        client = GameClient(entry_url=ENTRY_URL,
                            geepass_token=GEEPASS, jfbym_token=JFBYM)
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
                 "boot_index_max", "account")

    def __init__(self, episode_id, table_id, side, length, account):
        self.id = episode_id
        self.table_id = table_id
        self.side = side            # "B" / "P"
        self.length = length        # 当前连胜数（随 107 更新）
        self.start_length = length
        self.boot_index_max = 0     # 靴内局序峰值（换靴检测用）
        self.account = account


class StreakMonitor:
    def __init__(self, creds: list[dict], store: Store,
                 watcher: LobbyWatcher, min_streak: int):
        self._creds = creds
        self._store = store
        self._watcher = watcher
        self._min = min_streak
        self._client: GameClient | None = None
        self.mon = None                      # TableMonitor（懒创建）
        self.episodes: dict[int, Episode] = {}
        self._last_bet: dict[int, dict] = {}     # 桌→最近一次 110
        self._last_round: dict[int, dict] = {}   # 桌→最近一次 104
        self._raw_buf = 0
        self.stats = {"rounds": 0, "broke": 0, "censored": 0}
        self.run_id = 0

    async def ensure_monitor(self):
        """首个候选到来时创建监控（此时才登录全部监控账号）。

        依赖 hdata 空表建分片能力：monitor_tables([]) 为每个账号
        各建一条连接分片，后续 add_table 自动按负载均衡分配。
        """
        if self.mon is not None:
            return
        logger.info(f"[监控] 首个候选到达，登录 {len(self._creds)} "
                    "个监控账号（首次需打码，约1分钟）…")
        self._client = GameClient(entry_url=ENTRY_URL,
                                  geepass_token=GEEPASS, jfbym_token=JFBYM)
        first, rest = self._creds[0], self._creds[1:]
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
        """进桌 + 开 episode（候选桌 → 在监状态的唯一入口）。"""
        tid = cand["table_id"]
        if tid in self.episodes:
            return
        await self.mon.add_table(
            {"table_id": tid, "game_type_id": cand["game_type_id"]})
        ep_id = self._store.open_episode({
            "table_id": tid, "table_name": cand.get("table_name"),
            "game_type_id": cand.get("game_type_id"),
            "side": cand["side"], "detected_via": cand["via"],
            "start_length": cand["length"],
            "account": "+".join(c["account"] for c in self._creds)})
        self.episodes[tid] = Episode(
            ep_id, tid, cand["side"], cand["length"],
            self._creds[0]["account"])
        self._watcher.active.add(tid)
        logger.info(f"[监控] 进桌 {tid} {cand.get('table_name')} "
                    f"{cand['side']}×{cand['length']} "
                    f"(episode#{ep_id}，在监 {len(self.episodes)} 桌)")

    # ── 主循环：候选消费 + 事件消费双任务并发 ──

    async def _serve(self, candidates: asyncio.Queue):
        """监控建立后的服务循环；事件流异常即抛出，由外层重建。"""
        cand_t = asyncio.create_task(self._cand_loop(candidates))
        ev_t = asyncio.create_task(self._ev_loop())
        try:
            done, pending = await asyncio.wait(
                {cand_t, ev_t}, return_when=asyncio.FIRST_EXCEPTION)
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
            for t in (cand_t, ev_t):
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
            "online_number": (snap.get("tableOnline") or {})
            .get("onlineNumber"),
            "ts_bet_end": last104.get("countdownEndTime"),
            "ts_server": d.get("serverTime"),
            "ts_settle": now_ms(),
            "dealer_name": snap.get("dealerName"),
            "casino_id": snap.get("gameCasinoId")})
        if is_new:
            self.stats["rounds"] += 1
        self._store.insert_bet_points(rid, pools, boot)
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
            "online_number": (snap.get("tableOnline") or {})
            .get("onlineNumber"),
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
        """外层循环：等候选 → 建监控 → 服务；事件流断则全删失重建。"""
        while True:
            cand = await candidates.get()          # 阻塞等首个候选
            try:
                await self.ensure_monitor()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[监控] 初始化失败: {e}，60s 后重试")
                try:                            # 防半建连泄漏
                    if self.mon:
                        await self.mon.aclose()
                except Exception:
                    pass
                self.mon = None
                candidates.put_nowait(cand)        # 候选放回，避免丢失
                await asyncio.sleep(60)
                continue
            try:
                await self._open_episode(cand)
            except Exception as e:
                logger.warning(f"[监控] 首个候选进桌失败: {e}")
            try:
                await self._serve(candidates)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[监控] 事件流中断: {e}，10s 后重建监控")
            # 掉线：全部 episode 记删失，等发现层重新发现
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
        for tid in list(self.episodes):
            await self._close(tid, "censored_disconnect")
        if self.mon:
            await self.mon.aclose()
        if self.run_id:
            self._store.stop_run(self.run_id)


# ── 主入口 ──────────────────────────────────────────────


async def amain(min_streak: int, db_path: str, max_accounts: int = 0):
    store = Store(db_path)
    store.purge_raw(30)
    stale = store.close_stale_episodes()
    if stale:
        logger.info(f"[启动] 清理上次遗留未完结 episode {stale} 条"
                    "（记 censored_disconnect）")
    candidates: asyncio.Queue = asyncio.Queue()
    watcher = LobbyWatcher(ACCOUNTS[0], store, candidates, min_streak)
    mon_creds = (ACCOUNTS[1:1 + max_accounts] if max_accounts > 0
                 else ACCOUNTS[1:])
    monitor = StreakMonitor(mon_creds, store, watcher, min_streak)
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
    # 把候选从 watcher 队列喂给 monitor（同步 active 集合）
    try:
        await asyncio.gather(watch_task, mon_task, st_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in (watch_task, mon_task, st_task):
            t.cancel()
        await monitor.shutdown()
        store.commit()
        store.close()
        logger.info("[退出] 已清理，未完结 episode 记 censored_disconnect")


def main():
    ap = argparse.ArgumentParser(description="StreakHunter 反龙因素采集")
    ap.add_argument("--min", type=int, default=4, dest="min_streak",
                    help="连胜入场阈值（默认 4）")
    ap.add_argument("--db", default=DB_PATH, help="SQLite 路径")
    ap.add_argument("--max-accounts", type=int, default=0,
                    help="监控账号使用上限（默认 0=全部）；同 IP 有 WS "
                         "并发上限，遇到 403 时调小（如 5）")
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | {message}")
    logger.add("data/streak_hunter.log", rotation="50 MB", level="DEBUG")
    n_mon = (min(args.max_accounts, len(ACCOUNTS) - 1)
             if args.max_accounts > 0 else len(ACCOUNTS) - 1)
    logger.info(f"StreakHunter 启动：连胜阈值 {args.min_streak}，"
                f"发现账号 {ACCOUNTS[0]['account']}，"
                f"监控账号 {n_mon} 个，库 {args.db}")
    try:
        asyncio.run(amain(args.min_streak, args.db, args.max_accounts))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
