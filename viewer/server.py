"""长龙浏览器后端 — 零依赖（stdlib only），只读 data/streak.db。

用法:
    python server.py [--host 127.0.0.1] [--port 7100] [--db ../data/streak.db]

接口:
    GET /api/stats                     汇总指标
    GET /api/episodes?side=P&outcome=broke&min_len=5&game_type=&q=&limit=50&offset=0
    GET /api/episodes/{id}             单条龙详情（逐局+路纸尾）
    GET /api/rounds/{round_id}         单局详情（局内110帧资金曲线+在线人数）
    GET /api/lastjump?threshold=20000  "封盘前最后一跳"信号收敛面板
"""
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT.parent / "data" / "streak.db"

B_IDS = (3001, 3013)
P_IDS = (3002,)
T_IDS = (3003,)


def db():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def parse_bets(bet_json):
    d = {"b_amt": 0.0, "p_amt": 0.0, "t_amt": 0.0, "b_cnt": 0, "p_cnt": 0}
    if not bet_json:
        return d
    try:
        for it in json.loads(bet_json):
            pid = it.get("betPointId")
            amt = it.get("totalAmount") or 0
            cnt = it.get("totalPersonCount") or 0
            if pid in B_IDS:
                d["b_amt"] += amt; d["b_cnt"] += cnt
            elif pid in P_IDS:
                d["p_amt"] += amt; d["p_cnt"] += cnt
            elif pid in T_IDS:
                d["t_amt"] += amt
    except Exception:
        pass
    return d


def api_stats():
    con = db()
    row = con.execute("""
        select count(*) total,
               sum(case when outcome='broke' then 1 else 0 end) broke,
               sum(case when outcome like 'censored%' then 1 else 0 end) censored,
               sum(case when outcome is null then 1 else 0 end) open
        from streak_episodes""").fetchone()
    surv = con.execute("""
        select avg(max_length-start_length) avg_survive,
               avg(end_ts-start_ts)/1000.0 avg_secs
        from streak_episodes where outcome='broke'""").fetchone()
    rng = con.execute("select min(start_ts), max(end_ts) from streak_episodes").fetchone()
    return {
        "total": row["total"], "broke": row["broke"] or 0,
        "censored": row["censored"] or 0, "open": row["open"] or 0,
        "avg_survive": round(surv[0] or 0, 2),
        "avg_secs": round(surv[1] or 0, 1),
        "range": [rng[0], rng[1]],
        "now": int(time.time() * 1000),
    }


def api_episodes(q):
    side = q.get("side", [""])[0]
    outcome = q.get("outcome", [""])[0]
    game_type = q.get("game_type", [""])[0]
    text = q.get("q", [""])[0].strip()
    min_len = int(q.get("min_len", ["0"])[0] or 0)
    limit = min(int(q.get("limit", ["50"])[0] or 50), 200)
    offset = int(q.get("offset", ["0"])[0] or 0)

    where, args = ["1=1"], []
    if side in ("B", "P"):
        where.append("e.side=?"); args.append(side)
    if outcome == "open":
        where.append("e.outcome is null")
    elif outcome == "censored":
        where.append("e.outcome like 'censored%'")
    elif outcome:
        where.append("e.outcome=?"); args.append(outcome)
    if game_type:
        where.append("t.game_type_name=?"); args.append(game_type)
    if min_len:
        where.append("e.max_length>=?"); args.append(min_len)
    if text:
        where.append("(e.table_name like ? or cast(e.table_id as text) like ?)")
        args += [f"%{text}%", f"%{text}%"]
    w = " and ".join(where)

    con = db()
    total = con.execute(
        f"select count(*) from streak_episodes e left join tables t on t.table_id=e.table_id where {w}",
        args).fetchone()[0]
    rows = con.execute(f"""
        select e.episode_id, e.table_id, coalesce(nullif(e.table_name,''), t.table_name, '') table_name,
               coalesce(t.game_type_name,'') game_type_name,
               e.side, e.start_length, e.max_length, e.start_ts, e.end_ts, e.outcome
        from streak_episodes e left join tables t on t.table_id=e.table_id
        where {w} order by e.start_ts desc limit ? offset ?""",
        args + [limit, offset]).fetchall()
    gtypes = [r[0] for r in con.execute(
        "select distinct game_type_name from tables where game_type_name like '%百家乐%' order by 1")]
    return {
        "total": total,
        "game_types": gtypes,
        "items": [dict(r) for r in rows],
    }


def api_episode(eid):
    con = db()
    ep = con.execute("""
        select e.*, coalesce(t.game_type_name,'') game_type_name
        from streak_episodes e left join tables t on t.table_id=e.table_id
        where e.episode_id=?""", (eid,)).fetchone()
    if not ep:
        return None
    ep = dict(ep)
    rows = con.execute("""
        select r.round_id, r.ts_settle, r.streak_len_before, r.result, r.outcome,
               r.banker_points, r.player_points, r.bet_json, r.online_number,
               ro.road_flat_after
        from streak_rounds r
        left join rounds ro on ro.round_id = r.round_id
        where r.episode_id=? order by r.ts_settle""", (eid,)).fetchall()
    rounds = []
    for r in rows:
        d = dict(r)
        d.pop("bet_json", None)
        d.update(parse_bets(r["bet_json"]))
        road = d.pop("road_flat_after", None) or ""
        d["road_tail"] = road[-30:]
        rounds.append(d)
    ep["rounds"] = rounds
    return ep


# ── "封盘前最后一跳"信号面板 ──

_LASTJUMP_CACHE = {"ts": 0, "data": None}
_LASTJUMP_TTL = 60  # 秒


def _wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def _side_amounts(payload):
    try:
        d = json.loads(payload)
    except Exception:
        return None
    b = p = 0.0
    for it in d.get("jackpotPoolInfos") or []:
        pid = it.get("betPointId")
        if pid in B_IDS:
            b += it.get("totalAmount") or 0
        elif pid in P_IDS:
            p += it.get("totalAmount") or 0
    return b, p


def api_lastjump(threshold=20000):
    if time.time() - _LASTJUMP_CACHE["ts"] < _LASTJUMP_TTL and _LASTJUMP_CACHE["data"]:
        d = _LASTJUMP_CACHE["data"]
        if d.get("threshold") == threshold:
            return d
    con = db()
    evs = con.execute("""
        select e.round_id, e.ts, e.payload,
               (select r.result from rounds r where r.round_id=e.round_id) result
        from events_raw e
        where e.protocol_id=110 and e.round_id is not null
        order by e.round_id, e.ts""").fetchall()
    rounds = defaultdict(list)
    for rid, ts, payload, res in evs:
        if res and res[0] in "BP":
            rounds[rid].append((ts, payload, res))

    events = []  # (ts, heavy_side, win)
    for rid, frames in rounds.items():
        parsed = [_side_amounts(p) for _, p, _ in frames]
        parsed = [x for x in parsed if x]
        if len(parsed) < 2:
            continue
        db_ = parsed[-1][0] - parsed[-2][0]
        dp_ = parsed[-1][1] - parsed[-2][1]
        res0 = frames[0][2][0]
        if db_ > threshold and db_ > 2 * dp_:
            events.append((frames[-1][0], "B", res0 == "B"))
        elif dp_ > threshold and dp_ > 2 * db_:
            events.append((frames[-1][0], "P", res0 == "P"))
    events.sort()

    def stat(sub):
        k = sum(1 for e in sub if e[2])
        p, lo, hi = _wilson(k, len(sub))
        return {"n": len(sub), "wins": k, "rate": round(p, 4),
                "ci": [round(lo, 4), round(hi, 4)]}

    # 累计胜率曲线（按事件顺序）
    curve, w = [], 0
    for i, e in enumerate(events, 1):
        w += e[2]
        curve.append([e[0], round(w / i, 4)])
    data = {
        "threshold": threshold,
        "computed_at": int(time.time() * 1000),
        "total": stat(events),
        "side_B": stat([e for e in events if e[1] == "B"]),
        "side_P": stat([e for e in events if e[1] == "P"]),
        "curve": curve,
        "events": [{"ts": e[0], "side": e[1], "win": e[2]} for e in events[-50:]],
    }
    _LASTJUMP_CACHE.update({"ts": time.time(), "data": data})
    return data


def api_round(round_id):
    con = db()
    r = con.execute("""
        select r.*, coalesce(t.table_name,'') table_name, t.game_type_name
        from rounds r left join tables t on t.table_id=r.table_id
        where r.round_id=?""", (round_id,)).fetchone()
    if not r:
        return None
    info = dict(r)
    # 局内 110 帧
    evs = con.execute("""
        select ts, payload from events_raw
        where protocol_id=110 and round_id=? order by ts""", (round_id,)).fetchall()
    frames = []
    for ts, payload in evs:
        try:
            d = json.loads(payload)
        except Exception:
            continue
        b = p = tt = 0.0
        bc = pc = 0
        for it in d.get("jackpotPoolInfos") or []:
            pid = it.get("betPointId")
            amt = it.get("totalAmount") or 0
            cnt = it.get("totalPersonCount") or 0
            if pid in B_IDS:
                b += amt; bc += cnt
            elif pid in P_IDS:
                p += amt; pc += cnt
            elif pid in T_IDS:
                tt += amt
        frames.append({"ts": ts, "b_amt": b, "p_amt": p, "t_amt": tt,
                       "b_cnt": bc, "p_cnt": pc,
                       "players": d.get("currentRoundPlayerCount")})
    # 该局时间窗内的在线人数（大厅快照）
    t0 = frames[0]["ts"] if frames else (info.get("ts_settle") or 0) - 60000
    t1 = info.get("ts_settle") or (frames[-1]["ts"] if frames else t0 + 60000)
    online = [dict(ts=row[0], online=row[1], amount=row[2])
              for row in con.execute("""
                  select ts, online_number, total_amount from lobby_snapshots
                  where table_id=? and ts between ? and ? order by ts""",
                  (info["table_id"], t0 - 15000, t1 + 5000))]
    # 牌面
    cards = [dict(side=row[0], idx=row[1], suit=row[2], rank=row[3], points=row[4])
             for row in con.execute(
        "select side, card_index, suit, rank, points from round_cards where round_id=? order by side, card_index",
        (round_id,))]
    info.pop("road_flat_after", None)
    return {"info": info, "frames": frames, "online": online, "cards": cards}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(
            obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/" or u.path == "/index.html":
                self._send((ROOT / "index.html").read_bytes(),
                           ctype="text/html; charset=utf-8")
            elif u.path == "/api/stats":
                self._send(api_stats())
            elif u.path == "/api/episodes":
                self._send(api_episodes(parse_qs(u.query)))
            elif u.path == "/api/lastjump":
                q = parse_qs(u.query)
                thr = int(q.get("threshold", ["20000"])[0] or 20000)
                self._send(api_lastjump(thr))
            elif u.path.startswith("/api/rounds/"):
                rid = int(u.path.rsplit("/", 1)[1])
                rd = api_round(rid)
                if rd is None:
                    self._send({"error": "not found"}, 404)
                else:
                    self._send(rd)
            elif u.path.startswith("/api/episodes/"):
                eid = int(u.path.rsplit("/", 1)[1])
                ep = api_episode(eid)
                if ep is None:
                    self._send({"error": "not found"}, 404)
                else:
                    self._send(ep)
            else:
                self._send({"error": "not found"}, 404)
        except Exception as e:
            self._send({"error": str(e)}, 500)


if __name__ == "__main__":
    host, port = "127.0.0.1", 7100
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif a == "--db" and i + 1 < len(args):
            DB_PATH = Path(args[i + 1]).resolve()
    print(f"长龙浏览器: http://{host}:{port}/  (db={DB_PATH})")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
