"""长龙浏览器后端 — 零依赖（stdlib only），只读 data/streak.db。

用法:
    python server.py [--host 127.0.0.1] [--port 7100] [--db ../data/streak.db]

接口:
    GET /api/stats                     汇总指标
    GET /api/episodes?side=P&outcome=broke&min_len=5&game_type=&q=&limit=50&offset=0
    GET /api/episodes/{id}             单条龙详情（逐局+路纸尾）
"""
import json
import sqlite3
import sys
import time
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
