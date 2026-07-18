"""最终验证 — 通过公开 API (monitor_tables, kick_policy="stay") 跑完整踢出周期。

预期: 连续未下注 3 局收到 notice 事件(123预警) → 满 5 局收到 kick 事件
(102推送, leaveTableType=2) → 自动重进 → 重进后继续收到局事件 ≥2 局。

日志: logs/kick_live_verify.log
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from hdata.client import GameClient

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"
ACCOUNT, PASSWORD = "lidongsen1", "lds19830413"

LOG = Path("logs/kick_live_verify.log")
MAX_SECONDS = 12 * 60


def log(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def main() -> int:
    LOG.unlink(missing_ok=True)
    log(f"最终验证开始 account={ACCOUNT} kick_policy=stay 上限={MAX_SECONDS}s")
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    await client.login(ACCOUNT, PASSWORD)
    tables = await client.get_tables()
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    if not bacc:
        log("VERDICT: INCONCLUSIVE 无2001桌台")
        return 1
    target = bacc[0]
    log(f"监控桌 {target['table_id']}")

    t0 = time.time()
    kicked = False
    post_kick_rounds = 0
    last_round = ""
    verdict = "INCONCLUSIVE"
    async with await client.monitor_tables([target], kick_policy="stay") as mon:
        it = mon.events().__aiter__()
        try:
            while time.time() - t0 < MAX_SECONDS:
                try:
                    ev = await asyncio.wait_for(it.__anext__(), timeout=30)
                except asyncio.TimeoutError:
                    log(f"心跳 t={time.time()-t0:.0f}s kicked={kicked} "
                        f"重进后局数={post_kick_rounds}")
                    continue
                except StopAsyncIteration:
                    verdict = "ERROR 事件流结束"
                    break
                now = time.time() - t0
                if ev["type"] == "notice":
                    log(f"notice t={now:.0f}s "
                        f"{json.dumps(ev['data'], ensure_ascii=False)[:120]}")
                elif ev["type"] == "kick":
                    kicked = True
                    d = ev.get("data") or {}
                    raw = d.get("raw") or {}
                    log(f"KICK t={now:.0f}s table={ev['table_id']} "
                        f"dropped={d.get('dropped')} "
                        f"leaveTableType={raw.get('leaveTableType')} "
                        f"msg={raw.get('msg')}")
                elif ev["type"] == "round" and kicked:
                    d = ev.get("data") or {}
                    rn = str(d.get("roundNo") or "")
                    if rn and rn != last_round:
                        last_round = rn
                        post_kick_rounds += 1
                        log(f"重进后第{post_kick_rounds}局 roundNo={rn} "
                            f"t={now:.0f}s")
                        if post_kick_rounds >= 2:
                            verdict = ("PASS 预警→被踢→自动重进→恢复监控 "
                                       "全链路正常")
                            break
        except Exception as e:
            verdict = f"ERROR {type(e).__name__}: {e}"
    log(f"VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
