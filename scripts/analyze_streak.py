"""连胜断点因素分析（只读，不影响采集程序）。

分析框架（v1，2026-07-20）：

  方向 A 基线独立性检验：各连胜长度下 P(断|非和) 与百家乐独立随机
      基准（B龙≈0.493 / 闲龙≈0.507，混合≈0.50）对比。≈基准=无记忆
      随机游走；显著>基准=断龙倾向（平台杀龙信号）；显著<基准=续龙倾向。
  方向 B 下注面信号（杀大赔小/引诱假设）：断龙局 vs 续龙局（同长度
      分层）对比 连胜方注额占比 / 总注额 / 下注人数 的分布差异。
  方向 C 维度切分：桌台类型 / 庄龙vs闲龙 / 好路标记来源 / 时段 的
      断龙率差异。

口径：
  - 和局(T)不算断也不算续，独立性检验以非和局为条件；
  - 断=P(下一非和局出反方)，Wilson 95% 置信区间；
  - 样本不足 30 的层只展示不结论。

用法：uv run python scripts/analyze_streak.py [db_path]
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else "data/streak.db"
BP_BANKER, BP_PLAYER = 3001, 3002          # 庄/闲下注点（schema.sql 注释）
# 8 副牌百家乐独立基准（条件和）：P(B)=0.5068, P(P)=0.4932
BASE_BREAK = {"B": 0.4932, "P": 0.5068}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def load(db: str):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    eps = con.execute("SELECT * FROM streak_episodes").fetchall()
    srs = con.execute("SELECT * FROM streak_rounds").fetchall()
    con.close()
    ep_side = {e["episode_id"]: e["side"] for e in eps}
    ep_meta = {e["episode_id"]: dict(e) for e in eps}
    rows = []
    for r in srs:
        side = ep_side.get(r["episode_id"], "")
        res = r["result"] or ""
        same = (side == "B" and res in ("B", "B6")) or (side == "P" and res == "P")
        tie = res == "T"
        # 解析下注分布
        b_amt = p_amt = 0.0
        try:
            pools = json.loads(r["bet_json"] or "[]")
            for p in pools:
                if p.get("betPointId") == BP_BANKER:
                    b_amt += p.get("totalAmount") or 0
                elif p.get("betPointId") == BP_PLAYER:
                    p_amt += p.get("totalAmount") or 0
        except Exception:
            pass
        side_amt = b_amt if side == "B" else p_amt
        bp_total = b_amt + p_amt
        rows.append({
            "episode_id": r["episode_id"], "side": side,
            "len": r["streak_len_before"], "result": res,
            "tie": tie, "same": same, "broke": r["outcome"] == "broke",
            "total_amount": r["total_amount"],
            "player_count": r["player_count"],
            "online_number": r["online_number"],
            "side_share": (side_amt / bp_total) if bp_total > 0 else None,
            "ts": r["ts_settle"],
            "game_type_id": ep_meta.get(r["episode_id"], {}).get("game_type_id"),
            "via": ep_meta.get(r["episode_id"], {}).get("detected_via"),
        })
    return rows, eps


def dir_a(rows):
    """方向 A：各连胜长度 P(断|非和) vs 独立基准。"""
    out = ["## 方向 A：基线独立性检验（P(断|非和) vs 独立随机基准 ≈0.50）",
           "",
           "| 连胜长度 | 非和局数 | 断 | 断率 | Wilson 95% CI | 与基准 |",
           "|---|---|---|---|---|---|"]
    for ln in sorted({r["len"] for r in rows}):
        nt = [r for r in rows if r["len"] == ln and not r["tie"]]
        k = sum(1 for r in nt if r["broke"])
        n = len(nt)
        if n == 0:
            continue
        lo, hi = wilson(k, n)
        p = k / n
        verdict = ("≈基准" if lo <= 0.50 <= hi else
                   ("显著>基准(断龙倾向)" if lo > 0.50 else "显著<基准(续龙倾向)"))
        flag = "" if n >= 30 else " ⚠️样本<30"
        out.append(f"| {ln} | {n} | {k} | {p:.3f} | [{lo:.3f}, {hi:.3f}] | {verdict}{flag} |")
    # 庄龙/闲龙分开
    out += ["", "### 按龙向拆分（B龙基准 0.493 / 闲龙基准 0.507）", "",
            "| 龙向 | 非和局数 | 断 | 断率 | Wilson 95% CI | 基准 |",
            "|---|---|---|---|---|---|"]
    for side in ("B", "P"):
        nt = [r for r in rows if r["side"] == side and not r["tie"]]
        k = sum(1 for r in nt if r["broke"])
        n = len(nt)
        lo, hi = wilson(k, n)
        base = BASE_BREAK[side]
        out.append(f"| {'庄龙' if side == 'B' else '闲龙'} | {n} | {k} | "
                   f"{k/n:.3f} | [{lo:.3f}, {hi:.3f}] | {base:.3f} |")
    return out


def dir_b(rows):
    """方向 B：断龙局 vs 续龙局（非和）的下注面分布对比。"""
    out = ["## 方向 B：下注面信号（断龙局 vs 续龙局）", "",
           "连胜方注额占比 = 连胜方下注点金额 / (庄+闲总额)；",
           "挤向连胜方（占比高）后下一局断，是'引诱-杀龙'的核心信号。", "",
           "| 指标(中位数) | 续龙局 | 断龙局 | 样本(续/断) |",
           "|---|---|---|---|---|"]
    cont = [r for r in rows if not r["broke"] and not r["tie"]]
    brk = [r for r in rows if r["broke"]]
    for name, key in [("连胜方注额占比", "side_share"),
                      ("当局总注额", "total_amount"),
                      ("当局下注人数", "player_count"),
                      ("在线人数", "online_number")]:
        mc, mb = med([r[key] for r in cont]), med([r[key] for r in brk])
        nc = sum(1 for r in cont if r[key] is not None)
        nb = sum(1 for r in brk if r[key] is not None)
        fmt = (lambda v: f"{v:.3f}" if v is not None else "—") \
            if key == "side_share" else (lambda v: f"{v:.0f}" if v is not None else "—")
        out.append(f"| {name} | {fmt(mc)} | {fmt(mb)} | {nc}/{nb} |")
    # 分层：长度 5-6 与 7+ 分别看占比
    out += ["", "### 分长度层看连胜方注额占比（中位数）", "",
            "| 层 | 续龙局 | 断龙局 |", "|---|---|---|---|"]
    for label, lo, hi in [("长度5-6", 5, 6), ("长度7+", 7, 99)]:
        c = [r["side_share"] for r in cont if lo <= r["len"] <= hi]
        b = [r["side_share"] for r in brk if lo <= r["len"] <= hi]
        f = lambda v: f"{v:.3f}" if v is not None else "—"
        out.append(f"| {label} | {f(med(c))} | {f(med(b))} |")
    return out


def dir_c(rows, eps):
    """方向 C：维度切分断龙率。"""
    out = ["## 方向 C：维度切分（P(断|非和)）", ""]
    nt = [r for r in rows if not r["tie"]]

    def block(title, keyfn, namefn):
        out.append(f"### {title}\n")
        out.append("| 类别 | 非和局数 | 断 | 断率 | Wilson 95% CI |")
        out.append("|---|---|---|---|---|")
        groups = {}
        for r in nt:
            groups.setdefault(keyfn(r), []).append(r)
        for g in sorted(groups, key=str):
            rs = groups[g]
            k = sum(1 for r in rs if r["broke"])
            n = len(rs)
            lo, hi2 = wilson(k, n)
            flag = "" if n >= 30 else " ⚠️样本<30"
            out.append(f"| {namefn(g)} | {n} | {k} | {k/n:.3f} | "
                       f"[{lo:.3f}, {hi2:.3f}]{flag} |")
        out.append("")

    from hdata.client import _GAME_TYPE_NAMES
    block("按桌台类型", lambda r: r["game_type_id"],
          lambda g: _GAME_TYPE_NAMES.get(g, str(g)) if g else "?")
    block("按发现来源", lambda r: r["via"] or "?",
          lambda g: {"good_roads": "平台好路标记", "local_streak": "本地连胜检测"}.get(g, g))
    block("按小时", lambda r: time.strftime("%H:00", time.localtime(r["ts"] / 1000)) if r["ts"] else "?",
          str)
    # episode 级：断龙峰值长度 vs 入场来源
    out.append("### episode 级：完结(broke) episode 的龙长分布\n")
    out.append("| 峰值龙长 | episode 数 | 占比 |")
    out.append("|---|---|---|")
    br_eps = [e for e in eps if e["outcome"] == "broke"]
    tot = len(br_eps) or 1
    from collections import Counter
    for ln, c in sorted(Counter(e["max_length"] for e in br_eps).items()):
        out.append(f"| {ln} | {c} | {c/tot:.1%} |")
    return out


def main():
    rows, eps = load(DB)
    nt = [r for r in rows if not r["tie"]]
    k = sum(1 for r in nt if r["broke"])
    lo, hi = wilson(k, len(nt))
    hdr = [
        "# 连胜断点因素分析报告（v1）",
        "",
        f"- 数据库：`{DB}`（只读）",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 样本：episode {len(eps)} 条（broke "
        f"{sum(1 for e in eps if e['outcome'] == 'broke')}），"
        f"连胜局记录 {len(rows)} 条（非和 {len(nt)}）",
        f"- 总断龙率：{k}/{len(nt)} = {k/len(nt):.3f}，"
        f"Wilson 95% CI [{lo:.3f}, {hi:.3f}]（独立基准 ≈0.50）",
        "",
        "> 注意：样本仅 ~1 小时采集量，所有结论为**初步信号**，"
        "> 需多日数据复核；episode 入场阈值 min=5，分析只覆盖龙长≥5。",
        "",
    ]
    parts = hdr + dir_a(rows) + [""] + dir_b(rows) + [""] + dir_c(rows, eps)
    text = "\n".join(parts)
    out = Path("docs/连胜断点分析-20260720.md")
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
