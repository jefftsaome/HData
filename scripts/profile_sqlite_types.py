# -*- coding: utf-8 -*-
"""画像 data/streak.db 实际数据，供 PG 字段类型优化参考（只读，一次性探针）。"""
import sqlite3

con = sqlite3.connect("file:data/streak.db?mode=ro", uri=True)


def q1(sql):
    return con.execute(sql).fetchone()


print("=== 金额小数位检查（Python 侧统计）===")
for t, c in [("rounds", "total_amount"), ("round_bet_points", "bet_amount"),
             ("streak_rounds", "total_amount"), ("round_bet_points", "win_points")]:
    max_dec = 0
    over2 = 0
    for (v,) in con.execute(f"SELECT {c} FROM {t} WHERE {c} IS NOT NULL"):
        s = repr(float(v))
        if "." in s and "e" not in s and "E" not in s:
            d = len(s.split(".")[1])
        else:
            d = 0
        if d > max_dec:
            max_dec = d
        if d > 2:
            over2 += 1
    print(f"{t}.{c}: 超2位小数行数={over2}, 最长小数位={max_dec}")

print("\n=== 关键列 NULL 率 ===")
pairs = [
    ("rounds", "dealer_name"), ("rounds", "good_roads"), ("rounds", "ts_bet_end"),
    ("rounds", "boot_index"), ("rounds", "round_no"), ("rounds", "player_count"),
    ("rounds", "online_number"), ("rounds", "game_type_id"),
    ("round_bet_points", "win_points"), ("events_raw", "round_id"),
    ("events_raw", "table_id"), ("streak_episodes", "end_ts"),
    ("streak_rounds", "bet_json"), ("streak_rounds", "payout_json"),
    ("lobby_snapshots", "road_flat"), ("lobby_snapshots", "good_roads"),
    ("lobby_snapshots", "boot_no"), ("lobby_snapshots", "online_number"),
]
for t, c in pairs:
    total, nn = q1(f"SELECT COUNT(*), COUNT({c}) FROM {t}")
    print(f"{t}.{c}: 非NULL {nn}/{total} ({100.0 * nn / total:.1f}%)")

print("\n=== ts 列位数（应13位=毫秒）===")
for t, c in [("rounds", "ts_settle"), ("rounds", "ts_bet_end"), ("events_raw", "ts"),
             ("tables", "first_seen"), ("lobby_snapshots", "ts")]:
    mn, mx = q1(f"SELECT MIN(LENGTH(CAST({c} AS TEXT))), MAX(LENGTH(CAST({c} AS TEXT))) FROM {t}")
    print(f"{t}.{c}: 位数 {mn}~{mx}")

print("\n=== 全0/全NULL 死列确认 ===")
print("lobby_snapshots.total_amount 非0行:",
      q1("SELECT COUNT(*) FROM lobby_snapshots WHERE total_amount IS NOT NULL AND total_amount != 0")[0])
print("rounds.ts_server 非NULL行:",
      q1("SELECT COUNT(*) FROM rounds WHERE ts_server IS NOT NULL")[0])
print("round_bet_points.win_count 非NULL行:",
      q1("SELECT COUNT(*) FROM round_bet_points WHERE win_count IS NOT NULL")[0])

print("\n=== 其他边界 ===")
print("streak_episodes.account 样本:",
      [r[0] for r in con.execute("SELECT DISTINCT account FROM streak_episodes LIMIT 3")])
print("collect_runs.account 样本:",
      [r[0] for r in con.execute("SELECT DISTINCT account FROM collect_runs LIMIT 3")])
print("rounds.result 含 B6 比例:",
      q1("SELECT ROUND(100.0*SUM(result='B6')/COUNT(*),2) FROM rounds")[0], "%")
print("bet_point_id 全集合:",
      sorted(r[0] for r in con.execute("SELECT DISTINCT bet_point_id FROM round_bet_points")))
