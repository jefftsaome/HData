"""events_raw 月度归档工具：SQLite 热窗 + gzip NDJSON 冷存。

背景：采集量约 300-500 MB/天，其中 95% 是 events_raw（高频原始事件）。
分析只依赖派生表（streak_rounds / streak_episodes / lobby_snapshots），
events_raw 只用于回溯重推。策略：
  - SQLite 只保留最近 N 天（热窗，默认 7 天）的 events_raw；
  - 超窗数据按月导出为 gzip 压缩的 NDJSON（每行一个 JSON），永久保存；
  - 导出行数与库内行数校验一致后才允许删除库内对应行；
  - 派生表永不清、不导出。

用法（默认干跑，只报告不写不删）：
    uv run python hsys/crawl-bot/archive.py --db hsys/crawl-bot/data/streak.db
    ... --export            # 只导出，不删库
    ... --export --delete   # 导出+校验后删除库内超窗行
    ... --export --delete --vacuum   # 删除后收缩库文件（需先停采集进程）

NDJSON 字段：id/ts/table_id/protocol_id/event_type/round_id/
source_account/payload（payload 为原始 JSON 字符串，未解析，保真）。
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

COLUMNS = ("id", "ts", "table_id", "protocol_id", "event_type",
           "round_id", "source_account", "payload")


def month_bounds(year: int, month: int) -> tuple[int, int]:
    """本地时区月份边界 [起, 止)，毫秒。"""
    start = datetime(year, month, 1)
    end = (datetime(year + 1, 1, 1) if month == 12
           else datetime(year, month + 1, 1))
    return (int(time.mktime(start.timetuple()) * 1000),
            int(time.mktime(end.timetuple()) * 1000))


def gz_line_count(path: Path) -> int:
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for _ in f:
            n += 1
    return n


def export_month(con: sqlite3.Connection, ym: str, lo: int, hi: int,
                 cutoff: int, out_path: Path) -> int:
    """流式导出某月超窗行，返回写出行数。"""
    cur = con.execute(
        f"SELECT {','.join(COLUMNS)} FROM events_raw "
        "WHERE ts >= ? AND ts < ? AND ts < ? ORDER BY id",
        (lo, hi, cutoff))
    n = 0
    with gzip.open(out_path, "at", encoding="utf-8") as f:
        while True:
            rows = cur.fetchmany(10000)
            if not rows:
                break
            for r in rows:
                f.write(json.dumps(dict(zip(COLUMNS, r)),
                                   ensure_ascii=False,
                                   separators=(",", ":")) + "\n")
            n += len(rows)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="events_raw 月度归档")
    ap.add_argument("--db", default="hsys/crawl-bot/data/streak.db")
    ap.add_argument("--hot-days", type=int, default=7,
                    help="库内热窗天数，超窗才归档（默认 7）")
    ap.add_argument("--out", default="hsys/crawl-bot/data/archive",
                    help="归档输出目录")
    ap.add_argument("--export", action="store_true", help="执行导出")
    ap.add_argument("--delete", action="store_true",
                    help="导出校验一致后删除库内对应行（隐含 --export）")
    ap.add_argument("--vacuum", action="store_true",
                    help="删除后 VACUUM 收缩库文件（须先停采集进程）")
    args = ap.parse_args()
    do_export = args.export or args.delete

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    if not db_path.exists():
        sys.exit(f"库不存在: {db_path}")

    cutoff = int(time.time() * 1000) - args.hot_days * 86400_000
    con = sqlite3.connect(str(db_path), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")

    total_rows = con.execute("SELECT COUNT(*) FROM events_raw").fetchone()[0]
    print(f"库: {db_path}")
    print(f"events_raw 总行数 {total_rows:,} | 热窗 {args.hot_days} 天 "
          f"(cutoff={datetime.fromtimestamp(cutoff/1000):%Y-%m-%d %H:%M})")

    months = con.execute(
        "SELECT strftime('%Y-%m', ts/1000, 'unixepoch', 'localtime') ym,"
        "       COUNT(*), SUM(LENGTH(payload)) "
        "FROM events_raw WHERE ts < ? GROUP BY ym ORDER BY ym",
        (cutoff,)).fetchall()
    if not months:
        print("没有超窗数据，无需归档。")
        con.close()
        return

    print(f"{'月份':<9}{'超窗行数':>12}{'原始payload':>14}  动作")
    plan: list[tuple[str, int, int, int, int]] = []  # ym, rows, bytes, lo, hi
    for ym, rows, payload_bytes in months:
        y, m = int(ym[:4]), int(ym[5:7])
        lo, hi = month_bounds(y, m)
        plan.append((ym, rows, payload_bytes or 0, lo, hi))
        out_path = out_dir / f"events_raw-{ym}.jsonl.gz"
        exists = f"已存在({gz_line_count(out_path):,}行)" \
            if out_path.exists() else "新建"
        print(f"{ym:<9}{rows:>12,}{payload_bytes/1e6:>12.1f}MB  "
              f"→ {out_path.name} [{exists}]")

    if not do_export:
        print("\n干跑结束（未写未删）。加 --export 导出，"
              "--export --delete 导出并清理库内超窗行。")
        con.close()
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    for ym, rows, _pb, lo, hi in plan:
        out_path = out_dir / f"events_raw-{ym}.jsonl.gz"
        pre = gz_line_count(out_path) if out_path.exists() else 0
        db_count = con.execute(
            "SELECT COUNT(*) FROM events_raw "
            "WHERE ts >= ? AND ts < ? AND ts < ?", (lo, hi, cutoff)
        ).fetchone()[0]
        if pre >= db_count:
            print(f"[{ym}] 归档文件已有 {pre:,} 行 ≥ 库内 {db_count:,} 行，"
                  "跳过导出")
            written = 0
        else:
            written = export_month(con, ym, lo, hi, cutoff, out_path)
            print(f"[{ym}] 导出 {written:,} 行 → {out_path.name}")
        # 校验：文件总行数必须 ≥ 库内该月超窗行数，才允许删除
        final = gz_line_count(out_path)
        if final < db_count:
            print(f"[{ym}] 校验失败：文件 {final:,} 行 < 库内 {db_count:,} 行，"
                  "不删除！请检查磁盘/权限后重跑。")
            continue
        if args.delete:
            cur = con.execute(
                "DELETE FROM events_raw WHERE ts >= ? AND ts < ? AND ts < ?",
                (lo, hi, cutoff))
            con.commit()
            print(f"[{ym}] 已删除库内 {cur.rowcount:,} 行")

    if args.vacuum:
        print("VACUUM 中（采集进程必须先停，否则会锁库失败）…")
        con.execute("VACUUM")
        print("VACUUM 完成。")
    con.close()
    print("归档完成。")


if __name__ == "__main__":
    main()
