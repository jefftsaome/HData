"""StreakHunter 落库层（SQLite，单写入者）。

执行 docs/schema.sql（v2 全量 DDL）+ 本模块追加的 streak 专用表：
  - streak_episodes：一条连胜事件一行（入场→反/删失）
  - streak_rounds：连胜期间每局一行（协变量+结局标签）

用法:
    store = Store("data/streak.db")
    store.upsert_table({...})
    ...
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA_SQL = Path(__file__).parent.parent / "docs" / "schema.sql"

# schema.sql 之外的 streak 专用表（v3 追加）
STREAK_DDL = """
CREATE TABLE IF NOT EXISTS streak_episodes (
    episode_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id        INTEGER NOT NULL,
    table_name      TEXT,                  -- 入场时桌名快照
    game_type_id    INTEGER,
    side            TEXT NOT NULL,         -- 连胜方向：B=庄 P=闲
    detected_via    TEXT,                  -- local_streak / good_roads
    start_length    INTEGER,               -- 入场时已达连胜数
    start_round_id  INTEGER,               -- 入场后第一局 round_id
    start_ts        INTEGER,               -- 入场时间（本地毫秒）
    end_round_id  INTEGER,
    end_ts        INTEGER,
    max_length      INTEGER,               -- 最终达到的最大连胜数
    outcome         TEXT,                  -- broke/censored_boot/censored_disconnect/NULL(进行中)
    account         TEXT                   -- 监控账号
);
CREATE INDEX IF NOT EXISTS idx_ep_table ON streak_episodes(table_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_ep_outcome ON streak_episodes(outcome);

CREATE TABLE IF NOT EXISTS streak_rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id      INTEGER NOT NULL,
    round_id        INTEGER NOT NULL,      -- 平台局ID
    ts_settle       INTEGER,               -- 结算时间（本地毫秒）
    streak_len_before INTEGER,             -- 本局结果前的连胜长度
    result          TEXT,                  -- B/P/T/B6（T=和，不断也不算）
    outcome         TEXT,                  -- continue(同向或T)/broke(反)
    banker_points   INTEGER,
    player_points   INTEGER,
    total_amount    REAL,                  -- 本局总投注（110）
    player_count    INTEGER,               -- 本局下注人数（110）
    online_number   INTEGER,               -- 展示在线人数
    bet_json        TEXT,                  -- 110 jackpotPoolInfos 原文
    payout_json     TEXT,                  -- 107 bootReport 原文
    FOREIGN KEY (episode_id) REFERENCES streak_episodes(episode_id),
    UNIQUE (episode_id, round_id)
);
CREATE INDEX IF NOT EXISTS idx_sr_episode ON streak_rounds(episode_id);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    """SQLite 单写入封装（WAL；采集进程内唯一实例）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.db_path), timeout=10)
        self.con.row_factory = sqlite3.Row
        if str(self.db_path) != ":memory:":
            self.con.execute("PRAGMA journal_mode=WAL")   # 读写并发
        self.con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        self.con.executescript(STREAK_DDL)
        self.con.commit()

    def close(self):
        self.con.close()

    # ── 桌台元数据 ──────────────────────────────────────

    def upsert_table(self, t: dict):
        ts = now_ms()
        self.con.execute(
            """INSERT INTO tables (table_id, table_name, game_type_id,
                   game_type_name, casino_id, casino_name, physics_no,
                   first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(table_id) DO UPDATE SET
                   table_name=excluded.table_name,
                   game_type_id=excluded.game_type_id,
                   game_type_name=excluded.game_type_name,
                   casino_id=excluded.casino_id,
                   casino_name=excluded.casino_name,
                   physics_no=excluded.physics_no,
                   last_seen=excluded.last_seen""",
            (t.get("table_id"), t.get("table_name"), t.get("game_type_id"),
             t.get("game_type_name"), t.get("casino_id"),
             t.get("casino_name"), t.get("physics_no"), ts, ts))
        self.con.commit()

    # ── 大厅采样 ────────────────────────────────────────

    def insert_lobby(self, rows: list[dict]):
        """批量写大厅采样。row 键: table_id/online_number/total_amount/
        game_status/boot_no/road_flat/good_roads(list|str)"""
        data = []
        for r in rows:
            gr = r.get("good_roads")
            if isinstance(gr, list):
                gr = json.dumps(gr, ensure_ascii=False)
            data.append((r.get("ts") or now_ms(), r.get("table_id"),
                         r.get("online_number"), r.get("total_amount"),
                         r.get("game_status"), r.get("boot_no"),
                         r.get("road_flat"), gr))
        self.con.executemany(
            """INSERT INTO lobby_snapshots
               (ts, table_id, online_number, total_amount, game_status,
                boot_no, road_flat, good_roads)
               VALUES (?,?,?,?,?,?,?,?)""", data)
        self.con.commit()

    # ── 牌局主表（round_id 去重，多账号/重进安全） ──────

    def insert_round(self, r: dict):
        """row 键见 schema rounds 表；重复 round_id 直接忽略。"""
        gr = r.get("good_roads")
        if isinstance(gr, list):
            gr = json.dumps(gr, ensure_ascii=False)
        cur = self.con.execute(
            """INSERT OR IGNORE INTO rounds
               (round_id, table_id, game_type_id, round_no, boot_no,
                boot_index, result, banker_points, player_points,
                road_flat_after, good_roads, player_count, total_amount,
                online_number, ts_bet_end, ts_server, ts_settle,
                dealer_name, casino_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("round_id"), r.get("table_id"), r.get("game_type_id"),
             r.get("round_no"), r.get("boot_no"), r.get("boot_index"),
             r.get("result"), r.get("banker_points"), r.get("player_points"),
             r.get("road_flat_after"), gr, r.get("player_count"),
             r.get("total_amount"), r.get("online_number"),
             r.get("ts_bet_end"), r.get("ts_server"), r.get("ts_settle"),
             r.get("dealer_name"), r.get("casino_id")))
        self.con.commit()
        return cur.rowcount > 0

    def insert_bet_points(self, round_id: int,
                          pools: list[dict] | None,
                          boot_report) -> int:
        """110 押注分布 + 107 派彩按 betPointId 合并落库。"""
        bet: dict[int, dict] = {}
        for p in (pools or []):
            bpid = p.get("betPointId")
            if bpid is None:
                continue
            bet[int(bpid)] = {
                "bet_amount": p.get("totalAmount"),
                "bet_persons": p.get("totalPersonCount")}
        win: dict[int, dict] = {}
        items = boot_report.items() if isinstance(boot_report, dict) else []
        if isinstance(boot_report, list):
            items = [(str(x.get("betPointId")), x)
                     for x in boot_report if isinstance(x, dict)]
        for k, v in items:
            try:
                bpid = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict):
                win[bpid] = {"win_count": v.get("winCount"),
                             "win_points": v.get("winPoints")}
        n = 0
        for bpid in set(bet) | set(win):
            b, w = bet.get(bpid, {}), win.get(bpid, {})
            self.con.execute(
                """INSERT OR REPLACE INTO round_bet_points
                   (round_id, bet_point_id, bet_amount, bet_persons,
                    win_count, win_points)
                   VALUES (?,?,?,?,?,?)""",
                (round_id, bpid, b.get("bet_amount"), b.get("bet_persons"),
                 w.get("win_count"), w.get("win_points")))
            n += 1
        self.con.commit()
        return n

    def insert_card(self, round_id: int, side: str, card_index: int,
                    card_number: int):
        """card_number 0~51 按标准扑克映射拆花色/牌面/计点。

        约定（待实测校验）：rank = n//4（0=A..12=K），suit = n%4；
        若后续校验发现映射不符，原始事件在 events_raw 可重推。
        """
        ranks = ["A", "2", "3", "4", "5", "6", "7",
                 "8", "9", "10", "J", "Q", "K"]
        suits = ["S", "H", "D", "C"]
        ri, si = card_number // 4, card_number % 4
        rank = ranks[ri] if 0 <= ri < 13 else str(ri)
        suit = suits[si] if 0 <= si < 4 else str(si)
        points = 0 if ri >= 9 else ri + 1
        self.con.execute(
            """INSERT INTO round_cards
               (round_id, side, card_index, suit, rank, points)
               VALUES (?,?,?,?,?,?)""",
            (round_id, side, card_index, suit, rank, points))

    def commit(self):
        self.con.commit()

    # ── streak 专用 ─────────────────────────────────────

    def open_episode(self, ep: dict) -> int:
        cur = self.con.execute(
            """INSERT INTO streak_episodes
               (table_id, table_name, game_type_id, side, detected_via,
                start_length, start_round_id, start_ts, max_length, account)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ep.get("table_id"), ep.get("table_name"),
             ep.get("game_type_id"), ep.get("side"), ep.get("detected_via"),
             ep.get("start_length"), ep.get("start_round_id"),
             ep.get("start_ts") or now_ms(), ep.get("start_length"),
             ep.get("account")))
        self.con.commit()
        return cur.lastrowid

    def close_episode(self, episode_id: int, outcome: str,
                      end_round_id: int | None = None,
                      max_length: int | None = None):
        self.con.execute(
            """UPDATE streak_episodes
               SET outcome=?, end_round_id=?, end_ts=?,
                   max_length=COALESCE(?, max_length)
               WHERE episode_id=?""",
            (outcome, end_round_id, now_ms(), max_length, episode_id))
        self.con.commit()

    def touch_episode_length(self, episode_id: int, length: int):
        self.con.execute(
            """UPDATE streak_episodes SET max_length=MAX(max_length, ?)
               WHERE episode_id=?""", (length, episode_id))

    def close_stale_episodes(self, outcome: str = "censored_disconnect"
                             ) -> int:
        """启动时清理：上次运行遗留的未完结 episode 全部记删失。

        进程被强杀（taskkill /F、断电）时来不及走优雅退出，这些
        outcome IS NULL 的僵尸 episode 会永远挂"进行中"，污染
        生存分析样本——开机即校正，返回清理条数。
        """
        cur = self.con.execute(
            """UPDATE streak_episodes SET outcome=?, end_ts=?
               WHERE outcome IS NULL""", (outcome, now_ms()))
        self.con.commit()
        return cur.rowcount

    def insert_streak_round(self, sr: dict):
        gr = {k: sr.get(k) for k in (
            "episode_id", "round_id", "ts_settle", "streak_len_before",
            "result", "outcome", "banker_points", "player_points",
            "total_amount", "player_count", "online_number")}
        self.con.execute(
            """INSERT OR IGNORE INTO streak_rounds
               (episode_id, round_id, ts_settle, streak_len_before, result,
                outcome, banker_points, player_points, total_amount,
                player_count, online_number, bet_json, payout_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gr["episode_id"], gr["round_id"], gr["ts_settle"],
             gr["streak_len_before"], gr["result"], gr["outcome"],
             gr["banker_points"], gr["player_points"], gr["total_amount"],
             gr["player_count"], gr["online_number"],
             json.dumps(sr.get("bet_json"), ensure_ascii=False)
             if sr.get("bet_json") is not None else None,
             json.dumps(sr.get("payout_json"), ensure_ascii=False)
             if sr.get("payout_json") is not None else None))
        self.con.commit()

    # ── 原始事件留底 ────────────────────────────────────

    def insert_event(self, table_id: int | None, protocol_id: int,
                     event_type: str, round_id: int | None,
                     payload, account: str = ""):
        self.con.execute(
            """INSERT INTO events_raw
               (ts, table_id, protocol_id, event_type, round_id,
                source_account, payload)
               VALUES (?,?,?,?,?,?,?)""",
            (now_ms(), table_id, protocol_id, event_type, round_id,
             account, json.dumps(payload, ensure_ascii=False)))
        # 高频写，commit 由调用方批量控制

    def purge_raw(self, days: int = 30) -> int:
        """滚动清理 events_raw，返回删除行数。"""
        cutoff = now_ms() - days * 86400_000
        cur = self.con.execute("DELETE FROM events_raw WHERE ts < ?",
                               (cutoff,))
        self.con.commit()
        return cur.rowcount

    # ── 采集运行记录 ────────────────────────────────────

    def start_run(self, account: str, layer: str,
                  tables: list | None = None, note: str = "") -> int:
        cur = self.con.execute(
            """INSERT INTO collect_runs
               (account, layer, tables_json, started_at, note)
               VALUES (?,?,?,?,?)""",
            (account, layer,
             json.dumps(tables or [], ensure_ascii=False), now_ms(), note))
        self.con.commit()
        return cur.lastrowid

    def stop_run(self, run_id: int):
        self.con.execute(
            "UPDATE collect_runs SET stopped_at=? WHERE run_id=?",
            (now_ms(), run_id))
        self.con.commit()
