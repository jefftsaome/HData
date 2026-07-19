"""抓取真实数据样本 — 登录→桌台列表→进桌快照→各类型事件→路纸解码。

产物: docs/数据样本.md（真实抓包样本 + 字段说明，过长字符串截断标注）
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from hdata.client import GameClient, road_streak

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"
ACCOUNT, PASSWORD = "lidongsen1", "lds19830413"

OUT = Path("docs/数据样本.md")
COLLECT_SECONDS = 100          # 事件采集窗口
WANT_TYPES = {"round", "card", "bet", "road", "status", "lobby", "leave",
              "notice", "other"}


def abbr(obj, limit=60):
    """递归截断过长字符串，保留结构。"""
    if isinstance(obj, str) and len(obj) > limit:
        return f"{obj[:40]}…({len(obj)}字符)"
    if isinstance(obj, dict):
        return {k: abbr(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [abbr(v, limit) for v in obj[:5]] + (
            [f"…(共{len(obj)}项)"] if len(obj) > 5 else [])
    return obj


def js(obj) -> str:
    return json.dumps(abbr(obj), ensure_ascii=False, indent=2)


async def main() -> int:
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    s = await client.login(ACCOUNT, PASSWORD)
    session_show = {k: v for k, v in s.items()
                    if k in ("player_id", "domain", "backend",
                             "game_player_id", "game_exp")}

    tables = await client.get_tables()
    by_type: dict[str, int] = {}
    for t in tables:
        by_type[t["game_type_name"]] = by_type.get(t["game_type_name"], 0) + 1
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    target = bacc[0]
    table_info_full = dataclasses.asdict(target) \
        if dataclasses.is_dataclass(target) else dict(target)

    samples: dict[str, dict] = {}
    road_pids: dict[int, int] = {}
    snapshot = {}
    async with await client.enter_table(target["table_id"], 2001) as ts:
        snapshot = ts.snapshot
        t0 = time.time()
        async for ev in ts.events():
            tp = ev["type"]
            if ev["protocol_id"] in (116, 160, 161):
                road_pids[ev["protocol_id"]] = road_pids.get(ev["protocol_id"], 0) + 1
            if tp not in samples:
                samples[tp] = {"protocol_id": ev["protocol_id"],
                               "table_id": ev["table_id"], "data": ev["data"]}
            if time.time() - t0 > COLLECT_SECONDS \
                    or len(samples) >= 7:
                break
        # 事件循环后再读：116 重置累积 + 107 逐局追加后，路纸才完整
        road_flat_str = ts.road_flat()
        streak = road_streak(road_flat_str)

    lines: list[str] = []
    A = lines.append
    A("# 数据样本（真实抓包）\n")
    A(f"> 采集时间 {time.strftime('%Y-%m-%d %H:%M:%S')}，账号 {ACCOUNT}，"
      f"桌台 {target['table_id']}。过长字符串已截断标注。\n")

    A("\n## 1. 登录后会话信息（`client.login()` 返回节选）\n")
    A("```json\n" + js(session_show) + "\n```\n")

    A(f"\n## 2. 大厅桌台列表（`client.get_tables()`，共 {len(tables)} 张）\n")
    A("按类型分布：\n")
    A("```json\n" + js(by_type) + "\n```\n")
    A("\n单张桌台（TableInfo）完整字段：\n")
    A("```json\n" + js(table_info_full) + "\n```\n")

    A(f"\n## 3. 进桌快照（`ts.snapshot`，即 401 响应的 gameTableInfo）\n")
    A("```json\n" + js(snapshot) + "\n```\n")

    A("\n## 4. 路纸解码结果\n")
    A(f"- 珠盘 flat（`ts.road_flat()`，116 重置+107 逐局累积，共 {len(road_flat_str)} 局）："
      f"`{road_flat_str}`\n")
    A(f"- 连胜计算（`road_streak`）：`{streak[0] or '无'}` 方 "
      f"`{streak[1]}` 连胜\n")
    A(f"""
> 牌路相关事件四种形态（实测，本次采集窗口内路纸协议出现次数 {road_pids or '{}'}）：
>
> | protocolId | 形态 | 说明 |
> |---:|---|---|
> | 116 | **全长路纸** | 进桌时推一次，含整靴数十局；`road_flat()` 以此重置累积 |
> | 107 | **单局结果** | 每局结算推一次，`roundResult="{{庄点}};{{闲点}}"`（庄在前），<br>可直接推出该局 B/P/T/B6；`road_flat()` 以此**逐局追加** |
> | 160 | 无 roadPaper | 仅 `{{tableId, roundId}}` 等，无牌路内容 |
> | 161 | 增量短串 | 长度 0~5 个 token 不等且语义不可靠（曾出现空串），<br>**不参与累积**，仅作原始样本留档 |
>
> 另：401 进桌快照的 `roadPaper.beatPlateRoad` 在进桌瞬间通常为**空**，
> 完整牌路以第一条 116 为准；大厅 `get_tables()` 的 `road_flat` 字段
> 本身就是全长的，可作为进桌前的初值。
""")

    A(f"\n## 5. 桌内实时事件（`ts.events()`，采集 {COLLECT_SECONDS}s 窗口）\n")
    A("每个事件的公共外壳：`{\"type\", \"protocol_id\", \"table_id\", \"data\"}`，"
      "以下按 type 各给一条真实样本：\n")
    for tp, ev in samples.items():
        A(f"\n### type=`{tp}`（protocolId={ev['protocol_id']}）\n")
        A("```json\n" + js(ev["data"]) + "\n```\n")

    OUT.write_text("".join(lines), encoding="utf-8")
    print(f"OK 写出 {OUT}，样本类型: {sorted(samples)}")
    print(f"road_flat({len(road_flat_str)}局): {road_flat_str[-30:]}")
    print(f"streak: {streak}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
