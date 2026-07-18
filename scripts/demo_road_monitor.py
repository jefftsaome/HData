"""Demo — 路纸筛选 + 多桌监控 + 决策示例。

典型使用场景:
  1. 登录后在大厅一次性拿到所有桌台的 ID 和路纸（无需逐桌进入）
  2. 按自己的路纸策略筛选出目标桌台（本 demo 用"长龙"策略举例）
  3. 同时进入多张桌台持续监控事件流
  4. 事件触发时做自己的决策（本 demo 只打印信号，真实使用时替换 decide()）

运行:
    .venv/Scripts/python.exe scripts/demo_road_monitor.py
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from hdata.client import GameClient

ENTRY_URL = "https://leyu.com"
ACCOUNT = "lidongsen1"          # 有缓存的账号，免打码
PASSWORD = "lds19830413"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"

MAX_TABLES = 3                  # demo 只进前 3 张符合条件的桌
MIN_STREAK = 3                  # 连龙阈值（实际策略可调高，如 4~5）
MIN_ROUNDS = 3                  # 本靴最少已开局数（路纸太短没有参考价值）


# ── 第 2 步：路纸筛选策略（替换成你自己的逻辑） ──

def streak(road: str) -> tuple[str, int]:
    """返回路纸末尾的连龙方向与长度，如 ('B', 5) 表示末尾连续 5 个庄。"""
    if not road:
        return ("", 0)
    last = road[-1]
    n = 0
    for ch in reversed(road):
        if ch == last:
            n += 1
        else:
            break
    return (last, n)


def select_tables(tables: list[dict]) -> list[dict]:
    """筛选条件（示例）：
      - 经典百家乐（game_type_id=2001）
      - 本靴已开 ≥10 局（路纸有参考价值）
      - 末尾出现 ≥MIN_STREAK 连龙（长庄或长闲）
    """
    picked = []
    for t in tables:
        if t["game_type_id"] != 2001:
            continue
        if t["road_count"] < MIN_ROUNDS:
            continue
        side, length = streak(t["road_flat"])
        if length >= MIN_STREAK:
            t = dict(t)
            t["streak_side"] = "庄" if side == "B" else "闲"
            t["streak_len"] = length
            picked.append(t)
    # 龙越长越优先
    picked.sort(key=lambda x: -x["streak_len"])
    return picked


# ── 第 4 步：决策函数（替换成你自己的逻辑） ──

def decide(table_id: int, event: dict):
    """每个桌内事件都会调到这里。demo 只打印，真实场景在这里下单/报警。"""
    if event["type"] == "round":
        d = event["data"]
        print(f"  [桌{table_id}] 局事件: {list(d)[:6]}")
    elif event["type"] == "road":
        print(f"  [桌{table_id}] 路纸更新 → 重新评估是否继续跟龙")
    elif event["type"] == "kick":
        print(f"  [桌{table_id}] 被踢（5局未投注），已自动重进")


# ── 主流程 ──

async def monitor_table(client: GameClient, table: dict, stop: asyncio.Event):
    """监控一张桌：进桌 → 持续收事件 → 交给 decide()。被踢自动重进。"""
    tid = table["table_id"]
    try:
        async with await client.enter_table(tid, table["game_type_id"]) as ts:
            print(f"▶ 已进桌 {tid}「{ts.snapshot.get('tableName')}」"
                  f"当前路纸: {ts.road_flat()}")
            async for ev in ts.events():
                if stop.is_set():
                    break
                decide(tid, ev)
    except Exception as e:
        print(f"✗ 桌{tid} 监控结束: {e}")


async def main():
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)

    # 第 1 步：登录 + 大厅拿全部桌台和路纸
    await client.login(ACCOUNT, PASSWORD)
    tables = await client.get_tables()
    print(f"大厅共 {len(tables)} 张桌\n全部经典百家乐路纸:")
    for t in tables:
        if t["game_type_id"] == 2001:
            print(f"  桌{t['table_id']:>5}  {t['road_flat']}")

    # 第 2 步：路纸筛选
    picked = select_tables(tables)[:MAX_TABLES]
    print(f"\n筛选结果（≥{MIN_STREAK} 连龙）: {len(picked)} 张")
    for t in picked:
        print(f"  桌{t['table_id']}  {t['streak_side']}龙×{t['streak_len']}"
              f"  路纸={t['road_flat']}")
    if not picked:
        print("当前没有符合条件的桌台，退出")
        return

    # 第 3 步：同时进多桌监控（每张桌一个协程）
    print(f"\n开始监控 {len(picked)} 张桌（30 秒后自动停止 demo）...")
    stop = asyncio.Event()

    async def auto_stop():
        await asyncio.sleep(30)
        stop.set()

    await asyncio.gather(
        auto_stop(),
        *(monitor_table(client, t, stop) for t in picked),
    )
    print("demo 结束")


if __name__ == "__main__":
    asyncio.run(main())
