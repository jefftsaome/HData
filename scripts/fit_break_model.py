"""强行拟合断龙模型 + 强拟合时间区间特征分析（只读）。

方法（用户 2026-07-22 指定「强行拟合」路线）：
  1. 数据：全部长龙 episode 内的相邻非和局对（上一局非和、注池完整），
     目标 = 当前局开 反/顺（和局只计数不进胜率）。
  2. 强拟合：束搜索（beam search）枚举条件组合（≤4 个条件），
     条件池 = 4 项方向、金额/人数倍率、龙长、小时、龙向、桌型、
     在线变化、人均变化、顺方份额；目标函数 = 子集胜率（多数类），
     约束 n≥MIN_N。输出在样本内胜率最高的规则。
  3. 强拟合区间：最强规则按小时拆胜率，≥0.70 且 n≥8 的小时即为
     「强拟合时间区间」。
  4. 区间特征：强区间内的牌局 vs 其余牌局，资金/人数/人均/在线/龙长
     中位数对比。
  5. 诚实性：规则另在 ≤07-21 上重新拟合、07-22 上样本外评估；
     样本内数字单独标注「过拟合产物，不可直接引用」。

用法：uv run python scripts/fit_break_model.py [db_path]
"""
from __future__ import annotations

import sqlite3
import sys
import time
from collections import Counter

sys.path.insert(0, ".")
from scripts.analyze_chain3 import load_windows, wilson   # noqa: E402
from hdata.client import _GAME_TYPE_NAMES                 # noqa: E402

DB = sys.argv[1] if len(sys.argv) > 1 else "data/streak.db"
MIN_N = 40
BEAM_W = 25
MAX_DEPTH = 4
STRONG_P, STRONG_N = 0.70, 8

GAME_NAMES = _GAME_TYPE_NAMES


def hr(ts):
    return int(time.strftime("%H", time.localtime(ts / 1000)))


def day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts / 1000))


def ratio(a, b):
    return (a / b) if b else (float("inf") if a > 0 else 1.0)


def build_samples():
    q1, _, stats = load_windows()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    gt = {eid: g for eid, g in con.execute(
        "SELECT episode_id, game_type_id FROM streak_episodes")}
    con.close()
    samples = []
    for w in q1:
        r1, r2 = w["r1"], w["r2"]
        if r2["res"] not in ("same", "broke"):
            continue
        s = {
            "ep": w["ep"], "side": w["side"], "gt": gt.get(w["ep"]),
            "broke": r2["res"] == "broke",
            "hour": hr(r2["ts"]), "day": day(r2["ts"]),
            "ln": r2["len"] or 0,
            "o_amt_up": r2["o"]["amt"] > r1["o"]["amt"],
            "s_amt_up": r2["s"]["amt"] > r1["s"]["amt"],
            "o_cnt_up": r2["o"]["cnt"] > r1["o"]["cnt"],
            "s_cnt_up": r2["s"]["cnt"] > r1["s"]["cnt"],
            "o_amt_r": ratio(r2["o"]["amt"], r1["o"]["amt"]),
            "s_amt_r": ratio(r2["s"]["amt"], r1["s"]["amt"]),
            "o_cnt_r": ratio(r2["o"]["cnt"], r1["o"]["cnt"]),
            "s_cnt_r": ratio(r2["s"]["cnt"], r1["s"]["cnt"]),
            "o_amt": r2["o"]["amt"], "s_amt": r2["s"]["amt"],
            "o_cnt": r2["o"]["cnt"], "s_cnt": r2["s"]["cnt"],
            "online": r2["online"] or 0,
            "online_up": (r2["online"] or 0) > (r1["online"] or 0),
        }
        s["s_pc"] = s["s_amt"] / max(s["s_cnt"], 1)
        s["o_pc"] = s["o_amt"] / max(s["o_cnt"], 1)
        s["s_pc_up"] = s["s_pc"] > (r1["s"]["amt"] / max(r1["s"]["cnt"], 1))
        s["o_pc_up"] = s["o_pc"] > (r1["o"]["amt"] / max(r1["o"]["cnt"], 1))
        tot = s["s_amt"] + s["o_amt"]
        s["s_share"] = s["s_amt"] / tot if tot > 0 else 0.5
        s["pool"] = tot
        samples.append(s)
    return samples, stats


# ── 条件池 ──────────────────────────────────────────────

def make_conditions(samples):
    conds = []

    def add(group, text, fn):
        conds.append((group, text, fn))

    add("o_amt_dir", "反额↑", lambda s: s["o_amt_up"])
    add("o_amt_dir", "反额↓", lambda s: not s["o_amt_up"])
    add("s_amt_dir", "顺额↑", lambda s: s["s_amt_up"])
    add("s_amt_dir", "顺额↓", lambda s: not s["s_amt_up"])
    add("o_cnt_dir", "反人↑", lambda s: s["o_cnt_up"])
    add("o_cnt_dir", "反人↓", lambda s: not s["o_cnt_up"])
    add("s_cnt_dir", "顺人↑", lambda s: s["s_cnt_up"])
    add("s_cnt_dir", "顺人↓", lambda s: not s["s_cnt_up"])
    for key, zh in (("o_amt_r", "反额比"), ("s_amt_r", "顺额比"),
                    ("o_cnt_r", "反人比"), ("s_cnt_r", "顺人比")):
        for t in (1.1, 1.25, 1.5, 2.0, 3.0):
            add(key, f"{zh}>{t}", lambda s, k=key, t=t: s[k] > t)
        for t in (0.9, 0.75, 0.5, 0.33):
            add(key, f"{zh}<{t}", lambda s, k=key, t=t: s[k] < t)
    for t in (6, 7, 8, 9, 10, 12):
        add("ln", f"龙长≥{t}", lambda s, t=t: s["ln"] >= t)
    for h in range(24):
        add("hour", f"{h:02d}时", lambda s, h=h: s["hour"] == h)
    add("side", "庄龙", lambda s: s["side"] == "B")
    add("side", "闲龙", lambda s: s["side"] == "P")
    for gtid in sorted({s["gt"] for s in samples if s["gt"]}):
        name = GAME_NAMES.get(gtid, str(gtid))
        add("gt", f"桌型={name}", lambda s, g=gtid: s["gt"] == g)
    add("online_dir", "在线↑", lambda s: s["online_up"])
    add("online_dir", "在线↓", lambda s: not s["online_up"])
    add("s_pc_dir", "顺人均↑", lambda s: s["s_pc_up"])
    add("s_pc_dir", "顺人均↓", lambda s: not s["s_pc_up"])
    add("o_pc_dir", "反人均↑", lambda s: s["o_pc_up"])
    add("o_pc_dir", "反人均↓", lambda s: not s["o_pc_up"])
    for t in (0.55, 0.6, 0.65, 0.7):
        add("s_share", f"顺份额>{t}", lambda s, t=t: s["s_share"] > t)
    return conds


def score(sub):
    """子集胜率（多数类）、预测类、n。"""
    n = len(sub)
    if n == 0:
        return None
    k = sum(1 for s in sub if s["broke"])
    p_break = k / n
    if p_break >= 0.5:
        return (p_break, "反", n)
    return (1 - p_break, "顺", n)


def beam_search(samples, conds, min_n=MIN_N, width=BEAM_W, depth=MAX_DEPTH):
    """返回 [(winrate, pred, n, rule_text, subset)] 按胜率排序。"""
    root = (score(samples), (), samples)
    beam = [root]
    best = []
    for _ in range(depth):
        cand = []
        for sc, rule, sub in beam:
            if sc is None or sc[2] < min_n:
                continue
            used = {g for g, _ in rule}
            for g, text, fn in conds:
                if g in used:
                    continue
                sub2 = [s for s in sub if fn(s)]
                sc2 = score(sub2)
                if sc2 is None or sc2[2] < min_n:
                    continue
                cand.append((sc2, rule + ((g, text),), sub2))
        if not cand:
            break
        cand.sort(key=lambda x: (-x[0][0], -x[0][2]))
        best.extend(cand[:width])
        beam = cand[:width]
    uniq, seen = [], set()
    for sc, rule, sub in best:
        key = tuple(sorted(t for _, t in rule))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sc, rule, sub))
    uniq.sort(key=lambda x: (-x[0][0], -x[0][2]))
    return uniq


def med(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def fmt_rule(rule):
    return " ∧ ".join(t for _, t in rule)


def main():
    samples, stats = build_samples()
    n = len(samples)
    k = sum(1 for s in samples if s["broke"])
    print(f"数据集：非和局对 {n}（反 {k}，基准反率 {k/n:.3f}）")
    conds = make_conditions(samples)
    print(f"条件池 {len(conds)} 个 | 束宽 {BEAM_W} 深 {MAX_DEPTH} | min_n={MIN_N}")

    print("\n## 一、强行拟合 Top 规则（样本内！过拟合产物）")
    print("| # | 规则 | 预测 | n | 胜率 | Wilson CI |")
    print("|---|---|---|---|---|---|")
    top = beam_search(samples, conds)
    for i, (sc, rule, sub) in enumerate(top[:15], 1):
        p, pred, nn = sc
        lo, hi = wilson(int(p * nn + 0.5), nn)
        print(f"| {i} | {fmt_rule(rule)} | {pred} | {nn} | {p:.3f} | "
              f"[{lo:.3f},{hi:.3f}] |")

    if not top:
        print("无满足 min_n 的规则。")
        return

    # ── 一b、禁用小时条件重搜（min_n=100，求稳）──
    conds_nh = [c for c in conds if c[0] != "hour"]
    print("\n## 一b、禁用小时条件重搜（min_n=100，规则不含时间）")
    print("| # | 规则 | 预测 | n | 胜率 | Wilson CI |")
    print("|---|---|---|---|---|---|")
    top_nh = beam_search(samples, conds_nh, min_n=100)
    for i, (sc, rule, sub) in enumerate(top_nh[:10], 1):
        p, pred, nn = sc
        lo, hi = wilson(int(p * nn + 0.5), nn)
        print(f"| {i} | {fmt_rule(rule)} | {pred} | {nn} | {p:.3f} | "
              f"[{lo:.3f},{hi:.3f}] |")
    if not top_nh:
        print("无满足 min_n=100 的无时间规则，无法做区间分析。")
        return

    # ── 二、最强无时间规则的强拟合时间区间 ──
    sc, rule, sub = top_nh[0]
    p, pred, nn = sc
    print(f"\n## 二、最强无时间规则的逐小时胜率（找强拟合区间）")
    print(f"规则：{fmt_rule(rule)} → 预测「{pred}」（样本内 {nn} 样本 "
          f"胜率 {p:.3f}）")
    win_fn = (lambda s: s["broke"]) if pred == "反" else (lambda s: not s["broke"])
    print("| 小时 | n | 中 | 胜率 | CI |")
    print("|---|---|---|---|---|")
    strong_hours = []
    for h in range(24):
        sel = [s for s in sub if s["hour"] == h]
        if len(sel) < 3:
            continue
        kk = sum(1 for s in sel if win_fn(s))
        lo, hi = wilson(kk, len(sel))
        mark = ""
        if kk / len(sel) >= STRONG_P and len(sel) >= STRONG_N:
            mark = " ◄◄强区间"
            strong_hours.append(h)
        print(f"| {h:02d} | {len(sel)} | {kk} | {kk/len(sel):.3f} | "
              f"[{lo:.3f},{hi:.3f}]{mark} |")
    print(f"\n强拟合小时：{[f'{h:02d}' for h in strong_hours] or '无'}")

    # ── 三、强拟合区间的牌局特征 ──
    if strong_hours:
        strong = [s for s in sub if s["hour"] in strong_hours]
        weak = [s for s in sub if s["hour"] not in strong_hours]
        print(f"\n## 三、强拟合区间牌局特征（规则命中的样本：强区间 "
              f"{len(strong)} vs 其余 {len(weak)}，中位数）")
        print("| 指标 | 强区间 | 其余 |")
        print("|---|---|---|")
        for name, fn in [
            ("顺方金额", lambda s: s["s_amt"]),
            ("反方金额", lambda s: s["o_amt"]),
            ("总池", lambda s: s["pool"]),
            ("顺方人数", lambda s: s["s_cnt"]),
            ("反方人数", lambda s: s["o_cnt"]),
            ("在线人数", lambda s: s["online"]),
            ("顺方人均", lambda s: s["s_pc"]),
            ("反方人均", lambda s: s["o_pc"]),
            ("顺方份额", lambda s: s["s_share"]),
            ("龙长", lambda s: s["ln"]),
            ("顺额比(上局→本局)", lambda s: s["s_amt_r"]),
            ("反额比", lambda s: s["o_amt_r"]),
            ("顺人比", lambda s: s["s_cnt_r"]),
            ("反人比", lambda s: s["o_cnt_r"]),
        ]:
            m1, m2 = med([fn(s) for s in strong]), med([fn(s) for s in weak])
            f = (lambda v: f"{v:,.0f}") if abs(m1) > 100 or abs(m2) > 100 \
                else (lambda v: f"{v:.2f}")
            print(f"| {name} | {f(m1)} | {f(m2)} |")
        for name, fn in [("龙向", lambda s: "庄龙" if s["side"] == "B" else "闲龙"),
                         ("桌型", lambda s: GAME_NAMES.get(s["gt"], str(s["gt"])))]:
            c1, c2 = Counter(fn(s) for s in strong), Counter(fn(s) for s in weak)
            print(f"| {name}分布 | {dict(c1.most_common(3))} | "
                  f"{dict(c2.most_common(3))} |")
    else:
        print("\n## 三、无强拟合小时，跳过区间特征。")

    # ── 四、样本外验证（≤07-21 拟合 → 07-22 评估）──
    print("\n## 四、样本外验证（规则在 07-20/21 拟合，07-22 评估）")
    fit_set = [s for s in samples if s["day"] < "2026-07-22"]
    test_set = [s for s in samples if s["day"] >= "2026-07-22"]
    print(f"拟合集 {len(fit_set)}，验证集 {len(test_set)}")
    if fit_set and test_set:
        for tag, pool, mn in (("全条件池", conds, MIN_N),
                              ("禁小时池", conds_nh, 100)):
            oos = beam_search(fit_set, pool, min_n=mn)
            print(f"\n### {tag}（拟合 min_n={mn}）")
            print("| # | 规则(拟合集选出) | 预测 | 拟合n/胜率 | 验证n/胜率 | 验证CI |")
            print("|---|---|---|---|---|---|")
            for i, (sc2, rule2, _sub2) in enumerate(oos[:8], 1):
                p2, pred2, n2 = sc2
                sel = [s for s in test_set
                       if all(next(fn for g, t, fn in conds if t == txt
                                   and g == grp)(s)
                              for grp, txt in rule2)]
                if len(sel) < 5:
                    continue
                win2 = (lambda s: s["broke"]) if pred2 == "反" \
                    else (lambda s: not s["broke"])
                kk = sum(1 for s in sel if win2(s))
                lo, hi = wilson(kk, len(sel))
                print(f"| {i} | {fmt_rule(rule2)} | {pred2} | {n2}/{p2:.3f} | "
                      f"{len(sel)}/{kk/len(sel):.3f} | [{lo:.3f},{hi:.3f}] |")

    print("\n> ⚠ 第一部分是强行过拟合的样本内结果，仅用于生成假设；"
          "只有第四部分样本外数字才允许外推。")


if __name__ == "__main__":
    main()
