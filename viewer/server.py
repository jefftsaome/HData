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
    d = {"b_amt": 0.0, "p_amt": 0.0, "t_amt": 0.0, "b_cnt": 0, "p_cnt": 0, "t_cnt": 0}
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
                d["t_amt"] += amt; d["t_cnt"] += cnt
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
        where.append("(coalesce(nullif(e.table_name,''), t.table_name, '') like ? or cast(e.table_id as text) like ?)")
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

    events = []  # (ts, heavy_side, win, round_id)
    for rid, frames in rounds.items():
        parsed = [_side_amounts(p) for _, p, _ in frames]
        parsed = [x for x in parsed if x]
        if len(parsed) < 2:
            continue
        db_ = parsed[-1][0] - parsed[-2][0]
        dp_ = parsed[-1][1] - parsed[-2][1]
        res0 = frames[0][2][0]
        if db_ > threshold and db_ > 2 * dp_:
            events.append((frames[-1][0], "B", res0 == "B", rid))
        elif dp_ > threshold and dp_ > 2 * db_:
            events.append((frames[-1][0], "P", res0 == "P", rid))
    events.sort()

    # ── 追龙鲸：龙局内末帧大跳方向 vs 龙侧 ──
    dragon = {r[0]: (r[1], r[2]) for r in con.execute("""
        select sr.round_id, e.side, sr.streak_len_before
        from streak_rounds sr join streak_episodes e on e.episode_id=sr.episode_id""")}
    chase, fade, by_len = [], [], defaultdict(list)
    for e in events:
        if e[3] in dragon:
            dside, dlen = dragon[e[3]]
            if e[1] == dside:
                chase.append(e)
                by_len[dlen].append(e)
            else:
                fade.append(e)

    def stat(sub):
        k = sum(1 for e in sub if e[2])
        p, lo, hi = _wilson(k, len(sub))
        return {"n": len(sub), "wins": k, "rate": round(p, 4),
                "ci": [round(lo, 4), round(hi, 4)]}

    # 追龙鲸按龙长度分层（断率=1-胜率）
    chase_strata = []
    for L in sorted(by_len):
        s = stat(by_len[L])
        if s["n"] >= 2:
            s["len"] = L
            chase_strata.append(s)

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
        "whale": {"chase": stat(chase), "fade": stat(fade),
                  "chase_strata": chase_strata},
        "curve": curve,
        "events": [{"ts": e[0], "side": e[1], "win": e[2],
                    "dragon": dragon.get(e[3], [None])[0]} for e in events[-50:]],
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
        bc = pc = tc = 0
        for it in d.get("jackpotPoolInfos") or []:
            pid = it.get("betPointId")
            amt = it.get("totalAmount") or 0
            cnt = it.get("totalPersonCount") or 0
            if pid in B_IDS:
                b += amt; bc += cnt
            elif pid in P_IDS:
                p += amt; pc += cnt
            elif pid in T_IDS:
                tt += amt; tc += cnt
        frames.append({"ts": ts, "b_amt": b, "p_amt": p, "t_amt": tt,
                       "b_cnt": bc, "p_cnt": pc, "t_cnt": tc,
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


# ── 分析页聚合接口 ──

def _afilters(q):
    """解析分析页通用筛选：side / game_type / days。"""
    side = q.get("side", ["all"])[0]
    gt = q.get("game_type", [""])[0]
    days = float(q.get("days", ["0"])[0] or 0)
    return side, gt, days


def _ep_where(side, gt, days):
    sql, args = ["1=1"], []
    if side in ("B", "P"):
        sql.append("e.side=?"); args.append(side)
    if gt:
        sql.append("t.game_type_name=?"); args.append(gt)
    if days > 0:
        sql.append("e.start_ts>=?"); args.append(int((time.time() - days * 86400) * 1000))
    return " and ".join(sql), args


def api_analysis_overview(q):
    side, gt, days = _afilters(q)
    where, args = _ep_where(side, gt, days)
    con = db()
    rows = con.execute(f"""
        select e.side, e.outcome, e.max_length, e.start_ts, e.end_ts
        from streak_episodes e left join tables t on t.table_id=e.table_id
        where {where}""", args).fetchall()
    out = {"episodes": len(rows), "broke": 0, "censored": 0,
           "by_side": {"B": {"n": 0, "broke": 0, "lens": []},
                       "P": {"n": 0, "broke": 0, "lens": []}}}
    for r in rows:
        s = r["side"] if r["side"] in ("B", "P") else None
        if r["outcome"] == "broke":
            out["broke"] += 1
        else:
            out["censored"] += 1
        if s:
            d = out["by_side"][s]
            d["n"] += 1
            d["lens"].append(r["max_length"] or 0)
            if r["outcome"] == "broke":
                d["broke"] += 1
    for s in ("B", "P"):
        d = out["by_side"][s]
        ls = d.pop("lens")
        d["avg_len"] = round(sum(ls) / len(ls), 2) if ls else 0
        d["broke_rate"] = round(d["broke"] / d["n"], 4) if d["n"] else 0
    out["game_types"] = [r[0] for r in con.execute(
        "select distinct game_type_name from tables where game_type_name!='' order by 1")]
    return out


def api_analysis_survival(q):
    """条件断龙概率 + KM 存活（按连胜长度），分庄龙/闲龙。左截断于 min 检测长度。"""
    side, gt, days = _afilters(q)
    where, args = _ep_where("all", gt, days)
    con = db()
    rows = con.execute(f"""
        select e.side, e.outcome, e.max_length, e.start_length
        from streak_episodes e left join tables t on t.table_id=e.table_id
        where {where} and e.max_length is not null""", args).fetchall()
    res = {}
    for s in ("B", "P"):
        eps = [(r["max_length"], r["outcome"] == "broke", r["start_length"] or 5)
               for r in rows if r["side"] == s]
        max_l = max((e[0] for e in eps), default=0)
        curve, km = [], 1.0
        for L in range(5, max_l + 1):
            risk = [e for e in eps if e[0] >= L and e[2] <= L]
            broke = sum(1 for e in risk if e[0] == L and e[1])
            n = len(risk)
            if n == 0:
                continue
            h = broke / n
            km *= (1 - h)
            curve.append({"len": L, "risk": n, "broke": broke,
                          "hazard": round(h, 4), "survival": round(km, 4)})
        res[s] = curve
    return res


def _round_metrics(r, side):
    """单局衍生指标。r 含 bet_json/player_count/online_number。"""
    b = parse_bets(r["bet_json"])
    sa = b["b_amt"] if side == "B" else b["p_amt"]   # 连胜方池
    oa = b["p_amt"] if side == "B" else b["b_amt"]   # 反方池
    sc = b["b_cnt"] if side == "B" else b["p_cnt"]
    oc = b["p_cnt"] if side == "B" else b["b_cnt"]
    tot = b["b_amt"] + b["p_amt"] + b["t_amt"]
    return {"total": tot, "s_amt": sa, "o_amt": oa, "t_amt": b["t_amt"],
            "s_cnt": sc, "o_cnt": oc,
            "players": r["player_count"] or 0,
            "online": r["online_number"],
            "s_avg": sa / sc if sc else 0, "o_avg": oa / oc if oc else 0,
            "tie_ratio": b["t_amt"] / tot if tot else 0}


def _pct(new, old):
    return (new - old) / old if old else None


def api_analysis_pairs(q):
    """断龙上下局对比：断龙组(broke) vs 继续组(continue)，衍生指标环比。"""
    side, gt, days = _afilters(q)
    where, args = _ep_where("all", gt, days)
    con = db()
    rows = con.execute(f"""
        select r.episode_id, r.ts_settle, r.streak_len_before, r.outcome,
               r.bet_json, r.player_count, r.online_number, e.side
        from streak_rounds r
        join streak_episodes e on e.episode_id=r.episode_id
        left join tables t on t.table_id=e.table_id
        where {where} and r.streak_len_before>=4
        order by r.episode_id, r.ts_settle""", args).fetchall()
    by_ep = defaultdict(list)
    for r in rows:
        by_ep[r["episode_id"]].append(r)
    pairs = []
    for ep_rows in by_ep.values():
        for i in range(1, len(ep_rows)):
            cur, prev = ep_rows[i], ep_rows[i - 1]
            if cur["outcome"] not in ("broke", "continue"):
                continue
            mc, mp = _round_metrics(cur, cur["side"]), _round_metrics(prev, cur["side"])
            pairs.append({
                "side": cur["side"], "grp": cur["outcome"],
                "len": cur["streak_len_before"],
                "d_total": _pct(mc["total"], mp["total"]),
                "d_players": _pct(mc["players"], mp["players"]),
                "d_s_amt": _pct(mc["s_amt"], mp["s_amt"]),
                "d_o_amt": _pct(mc["o_amt"], mp["o_amt"]),
                "d_s_avg": _pct(mc["s_avg"], mp["s_avg"]),
                "d_o_avg": _pct(mc["o_avg"], mp["o_avg"]),
                "d_tie": mc["tie_ratio"] - mp["tie_ratio"],
                "cur_total": mc["total"], "cur_players": mc["players"]})
    # 聚合：按 side×grp 求均值/中位数
    import statistics as st
    metrics = ["d_total", "d_players", "d_s_amt", "d_o_amt", "d_s_avg", "d_o_avg", "d_tie"]
    agg = {}
    for s in ("B", "P"):
        for g in ("broke", "continue"):
            sub = [p for p in pairs if p["side"] == s and p["grp"] == g]
            a = {"n": len(sub)}
            for m in metrics:
                vals = [p[m] for p in sub if p[m] is not None]
                a[m] = {"mean": round(st.mean(vals), 4) if vals else None,
                        "median": round(st.median(vals), 4) if vals else None,
                        "n": len(vals)}
            agg[f"{s}_{g}"] = a
    # 散点采样（断龙组全量 + 继续组等量采样）
    import random
    broke_pts = [[round(p["d_s_amt"] or 0, 3), round(p["d_o_amt"] or 0, 3), p["side"]]
                 for p in pairs if p["grp"] == "broke"
                 and p["d_s_amt"] is not None and p["d_o_amt"] is not None]
    cont_pts = [[round(p["d_s_amt"] or 0, 3), round(p["d_o_amt"] or 0, 3), p["side"]]
                for p in pairs if p["grp"] == "continue"
                and p["d_s_amt"] is not None and p["d_o_amt"] is not None]
    random.seed(7)
    random.shuffle(cont_pts)
    return {"agg": agg,
            "scatter": {"broke": broke_pts[:1500], "continue": cont_pts[:1500]}}


def api_analysis_heatmap(q):
    """断龙时刻热力：小时 × 桌型 的断龙次数与断龙率。"""
    side, gt, days = _afilters(q)
    where, args = _ep_where(side, gt, days)
    con = db()
    rows = con.execute(f"""
        select r.ts_settle, r.outcome, t.game_type_name g
        from streak_rounds r
        join streak_episodes e on e.episode_id=r.episode_id
        left join tables t on t.table_id=e.table_id
        where {where} and r.outcome in ('broke','continue')""", args).fetchall()
    heat = defaultdict(lambda: [0, 0])   # (g, hour) -> [broke, total]
    types = set()
    for r in rows:
        g = r["g"] or "未知"
        types.add(g)
        h = time.localtime(r["ts_settle"] / 1000).tm_hour
        k = (g, h)
        heat[k][1] += 1
        if r["outcome"] == "broke":
            heat[k][0] += 1
    data = [[g, h, b, n, round(b / n, 3) if n else 0]
            for (g, h), (b, n) in heat.items()]
    return {"types": sorted(types), "data": data}


_WHALES_CACHE = {"ts": 0, "key": None, "data": None}


def api_analysis_whales(q):
    """大户入场事件：帧间分侧增量，新进人均注额超阈值。"""
    hours = min(float(q.get("hours", ["24"])[0] or 24), 72)
    min_amt = float(q.get("min_amt", ["20000"])[0] or 20000)
    key = (hours, min_amt)
    if _WHALES_CACHE["data"] and _WHALES_CACHE["key"] == key and \
            time.time() - _WHALES_CACHE["ts"] < 300:
        return _WHALES_CACHE["data"]
    since = int((time.time() - hours * 3600) * 1000)
    con = db()
    rows = con.execute("""
        select table_id, round_id, ts, payload from events_raw
        where protocol_id=110 and ts>? order by round_id, ts""", (since,)).fetchall()
    events, prev = [], None
    for tid, rid, ts, payload in rows:
        try:
            d = json.loads(payload)
        except Exception:
            continue
        pools = d.get("jackpotPoolInfos")
        if not pools:
            continue
        def side_sums(ids):
            a = sum(x["totalAmount"] for x in pools if x["betPointId"] in ids)
            c = sum(x["totalPersonCount"] for x in pools if x["betPointId"] in ids)
            return a, c
        ba, bc = side_sums(B_IDS); pa, pc = side_sums(P_IDS)
        cur = (rid, ba, bc, pa, pc)
        if prev and prev[0] == rid:
            for gi, (a0, c0, a1, c1) in enumerate((
                    (prev[1], prev[2], ba, bc), (prev[3], prev[4], pa, pc))):
                da, dc = a1 - a0, c1 - c0
                if da >= min_amt:
                    events.append({
                        "ts": ts, "round_id": rid, "table_id": tid,
                        "side": "B" if gi == 0 else "P",
                        "d_amt": round(da), "d_cnt": dc,
                        "per_capita": round(da / dc) if dc > 0 else None,
                        "kind": "入场" if dc > 0 else "加仓"})
        prev = cur
    events.sort(key=lambda e: -e["d_amt"])
    tids = {e["table_id"] for e in events[:200]}
    names = {r[0]: r[1] for r in con.execute(
        f"select table_id, table_name from tables where table_id in ({','.join('?' * len(tids))})",
        tuple(tids))} if tids else {}
    for e in events[:200]:
        e["table_name"] = names.get(e["table_id"], "")
    out = {"events": events[:200], "total": len(events)}
    _WHALES_CACHE.update(ts=time.time(), key=key, data=out)
    return out


def api_analysis_conditions(q):
    """七条件续/反/和概率常驻监测（顺方金额下降组合）。"""
    side, gt, days = _afilters(q)
    where, args = _ep_where("all", gt, days)
    con = db()
    rows = con.execute(f"""
        select r.episode_id, r.result, r.bet_json, e.side, rc.boot_index bi
        from streak_rounds r
        join streak_episodes e on e.episode_id=r.episode_id
        left join tables t on t.table_id=e.table_id
        left join rounds rc on rc.round_id=r.round_id
        where {where}
        order by r.episode_id, r.ts_settle""", args).fetchall()
    by_ep = defaultdict(list)
    for r in rows:
        by_ep[r["episode_id"]].append(r)

    def cmp_dir(prev, cur):
        if prev == 0 or prev is None or cur is None:
            return None
        if cur < prev:
            return "dn"
        if cur > prev:
            return "up"
        return "eq"

    pairs = []
    for ep_rows in by_ep.values():
        if len(ep_rows) < 2:
            continue
        for i in range(1, len(ep_rows)):
            prev, cur = ep_rows[i - 1], ep_rows[i]
            if prev["result"] == "T":
                continue
            # boot_index 连续性：两者都有值且差不为1 → 中间漏局，剔除
            if prev["bi"] is not None and cur["bi"] is not None \
                    and cur["bi"] - prev["bi"] != 1:
                continue
            s = cur["side"]
            pb, cb = parse_bets(prev["bet_json"]), parse_bets(cur["bet_json"])
            if s == "B":
                sa = (pb["b_amt"], cb["b_amt"]); oa = (pb["p_amt"], cb["p_amt"])
                sc = (pb["b_cnt"], cb["b_cnt"]); oc = (pb["p_cnt"], cb["p_cnt"])
            else:
                sa = (pb["p_amt"], cb["p_amt"]); oa = (pb["b_amt"], cb["b_amt"])
                sc = (pb["p_cnt"], cb["p_cnt"]); oc = (pb["b_cnt"], cb["b_cnt"])
            if cur["result"] == "T":
                res = "和"
            elif (s == "B" and cur["result"] in ("B", "B6")) or (s == "P" and cur["result"] == "P"):
                res = "续"
            else:
                res = "反"
            pairs.append({"res": res, "side": s,
                          "sa": cmp_dir(*sa), "oa": cmp_dir(*oa),
                          "sc": cmp_dir(*sc), "oc": cmp_dir(*oc)})
    if side in ("B", "P"):
        pairs = [p for p in pairs if p["side"] == side]
    base = [p for p in pairs if None not in (p["sa"], p["oa"], p["sc"], p["oc"])]

    def stat(sub):
        n = len(sub)
        out = {"n": n}
        for r in ("续", "反", "和"):
            k = sum(1 for p in sub if p["res"] == r)
            p_, lo, hi = _wilson(k, n)
            out[r] = {"rate": round(p_, 4), "ci": [round(lo, 4), round(hi, 4)]}
        return out

    conds = [
        ("基准(全部)", lambda p: True),
        ("1 顺方金额降", lambda p: p["sa"] == "dn"),
        ("2 顺方金额降+反方金额降", lambda p: p["sa"] == "dn" and p["oa"] == "dn"),
        ("3 顺方金额降+反方金额升", lambda p: p["sa"] == "dn" and p["oa"] == "up"),
        ("4 顺方金额降+反方金额降+顺方人数升", lambda p: p["sa"] == "dn" and p["oa"] == "dn" and p["sc"] == "up"),
        ("5 顺方金额降+反方金额降+顺方人数降", lambda p: p["sa"] == "dn" and p["oa"] == "dn" and p["sc"] == "dn"),
        ("6 顺方金额降+反方金额降+顺方人数升+反方人数升", lambda p: p["sa"] == "dn" and p["oa"] == "dn" and p["sc"] == "up" and p["oc"] == "up"),
        ("7 顺方金额降+反方金额降+顺方人数升+反方人数降", lambda p: p["sa"] == "dn" and p["oa"] == "dn" and p["sc"] == "up" and p["oc"] == "dn"),
    ]
    return {"total_pairs": len(base),
            "conds": [{"label": lb, **stat([p for p in base if f(p)])} for lb, f in conds]}


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
            elif u.path.startswith("/vendor/"):
                f = (ROOT / u.path.lstrip("/")).resolve()
                if f.is_file() and f.parent.parent == ROOT.resolve():
                    self._send(f.read_bytes(),
                               ctype="application/javascript; charset=utf-8")
                else:
                    self._send({"error": "not found"}, 404)
            elif u.path == "/analysis":
                self._send((ROOT / "analysis.html").read_bytes(),
                           ctype="text/html; charset=utf-8")
            elif u.path.startswith("/api/analysis/"):
                name = u.path.rsplit("/", 1)[1]
                q = parse_qs(u.query)
                fn = {"overview": api_analysis_overview,
                      "survival": api_analysis_survival,
                      "pairs": api_analysis_pairs,
                      "heatmap": api_analysis_heatmap,
                      "conditions": api_analysis_conditions,
                      "whales": api_analysis_whales}.get(name)
                if fn:
                    self._send(fn(q))
                else:
                    self._send({"error": "not found"}, 404)
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
