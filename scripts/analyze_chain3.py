"""长龙连续三局链式分析（只读，不影响采集）。

场景（用户 2026-07-22 指定）：
  取长龙中连续 3 局（r1/r2/r3，为 episode 内相邻记录），r1、r2 不能为和：
  Q1: r2 对 r1 满足 4 条件【反方金额↓、顺方金额↑、反方人数↑、顺方人数↑】
      时，r2 开牌结果 顺/反 概率（和已被前置条件排除）。
  Q2: 在 Q1 的 4 条件成立且 r2=顺 的子集里，r3 对 r2 的 4 项指标
      （反方金额/顺方金额/反方人数/顺方人数，升/降 → 16 格）达到什么组合时
      P(r3=反) ≥ 70%；并附加我方判定因素（金额倍率、龙长、人均、在线变化），
      再按时段块切分检验是否与时间段有关。

口径：
  - 顺方 = 长龙方向一方（episode.side），反方 = 另一方；
  - 庄方金额/人数 = betPointId 3001+3013（庄+庄免佣两个点合并），闲方 = 3002；
  - 升 = 严格大于，降 = 严格小于，任一指标持平则该窗不入 16 格（单独计数）；
  - P(反) 以非和为条件（和=不输不赢，决策无关），同时给出原始计数；
  - 置信区间 Wilson 95%；多重比较警示：16 格×附加因素×时段 = 大量检验，
    偶发 ≥70% 不足为凭，需留出样本外复核（脚本按日期对半切分做验证）。

用法：uv run python scripts/analyze_chain3.py [db_path]
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from collections import defaultdict

DB = sys.argv[1] if len(sys.argv) > 1 else "data/streak.db"
B_IDS = (3001, 3013)
P_IDS = (3002,)
HOUR_BLOCKS = [(0, 6), (6, 12), (12, 18), (18, 24)]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def parse_pools(bet_json: str) -> dict | None:
    """从 bet_json 聚合 庄/闲 双方金额与人数。"""
    try:
        b_amt = b_cnt = p_amt = p_cnt = 0
        for it in json.loads(bet_json or "[]"):
            pid, amt, cnt = (it.get("betPointId"),
                             it.get("totalAmount") or 0,
                             it.get("totalPersonCount") or 0)
            if pid in B_IDS:
                b_amt += amt; b_cnt += cnt
            elif pid in P_IDS:
                p_amt += amt; p_cnt += cnt
        return {"b_amt": b_amt, "b_cnt": b_cnt,
                "p_amt": p_amt, "p_cnt": p_cnt}
    except Exception:
        return None


def hour_block(ts_ms: int) -> str:
    h = int(time.strftime("%H", time.localtime(ts_ms / 1000)))
    for lo, hi in HOUR_BLOCKS:
        if lo <= h < hi:
            return f"{lo:02d}-{hi:02d}"
    return "?"


def load_windows():
    """构造连续三局窗：返回 (q1_windows, q2_windows)。

    q1_windows: r1,r2 非和、双方注池数据完整的相邻两局（含 r2=顺/反）。
    q2_windows: 在 q1 的 4 条件成立且 r2=顺 的前提下存在 r3 的窗。
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    eps = {e["episode_id"]: dict(e) for e in
           con.execute("SELECT * FROM streak_episodes")}
    rounds: dict[int, list] = defaultdict(list)
    for r in con.execute("SELECT * FROM streak_rounds ORDER BY episode_id, id"):
        rounds[r["episode_id"]].append(dict(r))
    con.close()

    q1, q2 = [], []
    stats = {"episodes": len(eps), "rounds": sum(map(len, rounds.values())),
             "adj_pairs": 0, "no_tie_pairs": 0, "bet_ok_pairs": 0}
    for ep_id, rs in rounds.items():
        side = eps.get(ep_id, {}).get("side", "")
        if side not in ("B", "P") or len(rs) < 2:
            continue
        # 逐局整理
        seq = []
        for r in rs:
            res = r["result"] or ""
            tie = res == "T"
            same = ((side == "B" and res in ("B", "B6"))
                    or (side == "P" and res == "P"))
            pools = parse_pools(r["bet_json"])
            if pools is None:
                s = o = None
            else:
                s = ({"amt": pools["b_amt"], "cnt": pools["b_cnt"]}
                     if side == "B" else
                     {"amt": pools["p_amt"], "cnt": pools["p_cnt"]})
                o = ({"amt": pools["p_amt"], "cnt": pools["p_cnt"]}
                     if side == "B" else
                     {"amt": pools["b_amt"], "cnt": pools["b_cnt"]})
            seq.append({"res": ("tie" if tie else ("same" if same else "broke")),
                        "s": s, "o": o, "len": r["streak_len_before"],
                        "online": r["online_number"], "ts": r["ts_settle"]})
        for i in range(len(seq) - 1):
            r1, r2 = seq[i], seq[i + 1]
            stats["adj_pairs"] += 1
            if r1["res"] == "tie" or r2["res"] == "tie":
                continue
            stats["no_tie_pairs"] += 1
            if not r1["s"] or not r2["s"]:
                continue
            stats["bet_ok_pairs"] += 1
            r3 = seq[i + 2] if i + 2 < len(seq) else None
            q1.append({"ep": ep_id, "side": side, "r1": r1, "r2": r2,
                       "r3": r3})
    return q1, q2, stats


def cond4(prev, cur):
    """4 条件：反方金额↓、顺方金额↑、反方人数↑、顺方人数↑。"""
    return (cur["o"]["amt"] < prev["o"]["amt"]
            and cur["s"]["amt"] > prev["s"]["amt"]
            and cur["o"]["cnt"] > prev["o"]["cnt"]
            and cur["s"]["cnt"] > prev["s"]["cnt"])


def dir4(prev, cur):
    """4 项指标升/降方向（升=True 降=False），任一持平返回 None。"""
    out = []
    for key in ("o_amt", "s_amt", "o_cnt", "s_cnt"):
        side, field = key.split("_")
        a, b = prev[side][field], cur[side][field]
        if a == b:
            return None
        out.append(b > a)
    return tuple(out)  # (反额, 顺额, 反人, 顺人) True=升


def main():
    q1, _, stats = load_windows()
    print(f"样本：episode {stats['episodes']}，局 {stats['rounds']}，"
          f"相邻局对 {stats['adj_pairs']}，非和对 {stats['no_tie_pairs']}，"
          f"注池完整对 {stats['bet_ok_pairs']}")

    # ── Q1：4 条件成立时 r2 的顺/反 ──
    hit = [w for w in q1 if cond4(w["r1"], w["r2"])]
    n_all = len(q1)
    base_k = sum(1 for w in q1 if w["r2"]["res"] == "broke")
    blo, bhi = wilson(base_k, n_all)
    k2 = sum(1 for w in hit if w["r2"]["res"] == "broke")
    n2 = len(hit)
    lo2, hi2 = wilson(k2, n2)
    print(f"\n## Q1  r2 满足4条件（反额↓顺额↑反人↑顺人↑）时 r2 的结果")
    print(f"全体非和对基准：P(r2=反) = {base_k}/{n_all} = {base_k/n_all:.3f} "
          f"CI[{blo:.3f},{bhi:.3f}]")
    print(f"4条件窗：{n2} 个（占 {n2/n_all:.1%}）")
    if n2:
        print(f"P(r2=反|4条件) = {k2}/{n2} = {k2/n2:.3f} "
              f"CI[{lo2:.3f},{hi2:.3f}]   P(顺) = {1-k2/n2:.3f}")

    # ── Q2：4条件 + r2=顺 → r3 方向16格 ──
    sub = [w for w in hit if w["r2"]["res"] == "same"
           and w["r3"] is not None and w["r3"]["s"]]
    print(f"\n## Q2  4条件成立且 r2=顺、有 r3 的窗：{len(sub)} 个")
    if not sub:
        print("样本为 0，无法分析。")
        return
    kb = sum(1 for w in sub if w["r3"]["res"] == "broke")
    nt3 = [w for w in sub if w["r3"]["res"] != "tie"]
    k3 = sum(1 for w in nt3 if w["r3"]["res"] == "broke")
    lo3, hi3 = wilson(k3, len(nt3))
    print(f"子集基准：P(r3=反|非和) = {k3}/{len(nt3)} = "
          f"{k3/len(nt3):.3f} CI[{lo3:.3f},{hi3:.3f}]"
          f"（另含和局 {len(sub)-len(nt3)}）")

    cells = defaultdict(list)
    flat = 0
    for w in sub:
        d = dir4(w["r2"], w["r3"])
        if d is None:
            flat += 1
            continue
        cells[d].append(w)
    print(f"任一指标持平（不入格）：{flat} 窗")
    names = ["反额", "顺额", "反人", "顺人"]
    print(f"\n| 格（{'/'.join(names)}，↑=升 ↓=降） | n | 反 | 和 | "
          f"P(反|非和) | Wilson CI |")
    print("|---|---|---|---|---|---|")
    cand = []
    for d in sorted(cells, key=lambda d: -len(cells[d])):
        ws = cells[d]
        nt = [w for w in ws if w["r3"]["res"] != "tie"]
        k = sum(1 for w in nt if w["r3"]["res"] == "broke")
        ntie = sum(1 for w in ws if w["r3"]["res"] == "tie")
        if not nt:
            continue
        p = k / len(nt)
        lo, hi = wilson(k, len(nt))
        label = "".join("↑" if b else "↓" for b in d)
        mark = " ◄◄" if p >= 0.70 and len(nt) >= 10 else ""
        print(f"| {label} | {len(nt)} | {k} | {ntie} | {p:.3f} | "
              f"[{lo:.3f},{hi:.3f}]{mark} |")
        if p >= 0.70 and len(nt) >= 10:
            cand.append((label, d, ws))

    # ── 附加判定因素扫描（在每格基础上再切一刀）──
    print("\n## 附加判定因素（对 ≥70% 候选格与最大格加切）")
    factors = {
        "反额降幅>50%": lambda w: w["r3"]["o"]["amt"]
            < 0.5 * w["r2"]["o"]["amt"],
        "顺额升幅>2倍": lambda w: w["r3"]["s"]["amt"]
            > 2 * w["r2"]["s"]["amt"],
        "龙长7+": lambda w: (w["r3"]["len"] or 0) >= 7,
        "在线人数降": lambda w: (w["r3"]["online"] or 0)
            < (w["r2"]["online"] or 0),
        "顺人均升": lambda w: (w["r3"]["s"]["amt"] / max(w["r3"]["s"]["cnt"], 1))
            > (w["r2"]["s"]["amt"] / max(w["r2"]["s"]["cnt"], 1)),
    }
    top = cand[:3]
    if not top:  # 没有 ≥70% 候选则取样本最大的 3 格
        top = sorted(((("".join("↑" if b else "↓" for b in d)), d, ws)
                      for d, ws in cells.items()),
                     key=lambda x: -len(x[2]))[:3]
    for label, d, ws in top:
        nt = [w for w in ws if w["r3"]["res"] != "tie"]
        print(f"\n### 格 {label}（n={len(nt)}）")
        print("| 附加因素 | 方向 | n | 反 | P(反) | CI |")
        print("|---|---|---|---|---|---|")
        for fname, fn in factors.items():
            for want in (True, False):
                sel = [w for w in nt if fn(w) == want]
                if len(sel) < 5:
                    continue
                k = sum(1 for w in sel if w["r3"]["res"] == "broke")
                lo, hi = wilson(k, len(sel))
                mark = " ◄" if k / len(sel) >= 0.70 else ""
                print(f"| {fname} | {'是' if want else '否'} | {len(sel)} | "
                      f"{k} | {k/len(sel):.3f} | [{lo:.3f},{hi:.3f}]{mark} |")

    # ── 时段块 ──
    print("\n## 时段块检验（r3 结算时刻）")
    print("\n### 全体窗的 4 条件命中率 & Q2 基准反率 按时段")
    print("| 时段 | 非和对 | 4条件窗 | 命中率 | Q2子集 | 反 | P(反|非和) | CI |")
    print("|---|---|---|---|---|---|---|---|")
    for lo_, hi_ in HOUR_BLOCKS:
        blk = f"{lo_:02d}-{hi_:02d}"
        allp = [w for w in q1 if hour_block(w["r2"]["ts"]) == blk]
        hitp = [w for w in allp if cond4(w["r1"], w["r2"])]
        subp = [w for w in hitp if w["r2"]["res"] == "same"
                and w["r3"] is not None and w["r3"]["s"]
                and w["r3"]["res"] != "tie"]
        k = sum(1 for w in subp if w["r3"]["res"] == "broke")
        if not allp:
            continue
        p3 = f"{k/len(subp):.3f}" if subp else "—"
        ci = ""
        if subp:
            l_, h_ = wilson(k, len(subp))
            ci = f"[{l_:.3f},{h_:.3f}]"
        print(f"| {blk} | {len(allp)} | {len(hitp)} | "
              f"{len(hitp)/len(allp):.1%} | {len(subp)} | {k} | {p3} | {ci} |")

    if cand:
        print("\n### ≥70% 候选格 × 时段")
        print("| 格 | 时段 | n | 反 | P(反) | CI |")
        print("|---|---|---|---|---|---|")
        for label, d, ws in cand:
            for lo_, hi_ in HOUR_BLOCKS:
                blk = f"{lo_:02d}-{hi_:02d}"
                sel = [w for w in ws if w["r3"]["res"] != "tie"
                       and hour_block(w["r3"]["ts"]) == blk]
                if not sel:
                    continue
                k = sum(1 for w in sel if w["r3"]["res"] == "broke")
                l_, h_ = wilson(k, len(sel))
                print(f"| {label} | {blk} | {len(sel)} | {k} | "
                      f"{k/len(sel):.3f} | [{l_:.3f},{h_:.3f}] |")

    # ── 逐小时检验 ──
    def hr(ts):
        return int(time.strftime("%H", time.localtime(ts / 1000)))

    print("\n## 逐小时检验")
    print("\n### Q1：4 条件命中率与 r2 反率 按小时（r2 结算时刻）")
    print("| 小时 | 非和对 | 4条件窗 | 命中率 | 反 | P(反|4条件) | CI |")
    print("|---|---|---|---|---|---|---|")
    for h in range(24):
        allp = [w for w in q1 if hr(w["r2"]["ts"]) == h]
        if not allp:
            continue
        hitp = [w for w in allp if cond4(w["r1"], w["r2"])]
        k = sum(1 for w in hitp if w["r2"]["res"] == "broke")
        if hitp:
            p = k / len(hitp)
            l_, h_ = wilson(k, len(hitp))
            print(f"| {h:02d} | {len(allp)} | {len(hitp)} | "
                  f"{len(hitp)/len(allp):.1%} | {k} | {p:.3f} | "
                  f"[{l_:.3f},{h_:.3f}] |")
        else:
            print(f"| {h:02d} | {len(allp)} | 0 | — | — | — | — |")

    print("\n### Q2：子集反率 按小时（r3 结算时刻）")
    print("| 小时 | n | 反 | 和 | P(反|非和) | CI |")
    print("|---|---|---|---|---|---|")
    for h in range(24):
        sel = [w for w in sub if hr(w["r3"]["ts"]) == h]
        if not sel:
            continue
        nt = [w for w in sel if w["r3"]["res"] != "tie"]
        k = sum(1 for w in nt if w["r3"]["res"] == "broke")
        ntie = len(sel) - len(nt)
        if not nt:
            continue
        p = k / len(nt)
        l_, h_ = wilson(k, len(nt))
        mark = " ◄" if p >= 0.70 and len(nt) >= 10 else ""
        print(f"| {h:02d} | {len(nt)} | {k} | {ntie} | {p:.3f} | "
              f"[{l_:.3f},{h_:.3f}]{mark} |")

    print("\n### ↑↑↑↑ 格（最大格）× 小时")
    dom = cells.get((True, True, True, True), [])
    print("| 小时 | n | 反 | P(反|非和) | CI |")
    print("|---|---|---|---|---|")
    for h in range(24):
        sel = [w for w in dom if w["r3"]["res"] != "tie"
               and hr(w["r3"]["ts"]) == h]
        if len(sel) < 5:
            continue
        k = sum(1 for w in sel if w["r3"]["res"] == "broke")
        p = k / len(sel)
        l_, h_ = wilson(k, len(sel))
        mark = " ◄" if p >= 0.70 and len(sel) >= 10 else ""
        print(f"| {h:02d} | {len(sel)} | {k} | {p:.3f} | "
              f"[{l_:.3f},{h_:.3f}]{mark} |")

    # ── 样本外复核（按日期对半切）──
    print("\n## 样本外复核（按日期对半：前半找候选，后半验证）")
    dates = sorted({time.strftime("%Y-%m-%d", time.localtime(w["r2"]["ts"] / 1000))
                    for w in sub})
    if len(dates) >= 2:
        mid = dates[len(dates) // 2]
        half1 = [w for w in sub if time.strftime(
            "%Y-%m-%d", time.localtime(w["r2"]["ts"] / 1000)) < mid]
        half2 = [w for w in sub if time.strftime(
            "%Y-%m-%d", time.localtime(w["r2"]["ts"] / 1000)) >= mid]
        print(f"前半 <{mid}：{len(half1)} 窗；后半 ≥{mid}：{len(half2)} 窗")
        for tag, part in (("前半", half1), ("后半", half2)):
            c2 = defaultdict(list)
            for w in part:
                d = dir4(w["r2"], w["r3"])
                if d:
                    c2[d].append(w)
            best = None
            for d, ws in c2.items():
                nt = [w for w in ws if w["r3"]["res"] != "tie"]
                k = sum(1 for w in nt if w["r3"]["res"] == "broke")
                if len(nt) >= 5:
                    p = k / len(nt)
                    if best is None or p > best[2]:
                        best = (d, len(nt), p, k)
            if best:
                label = "".join("↑" if b else "↓" for b in best[0])
                print(f"  {tag}最强格 {label}: {best[3]}/{best[1]} = {best[2]:.3f}")
    else:
        print("数据跨日期不足 2 天，无法对半验证。")


if __name__ == "__main__":
    main()
