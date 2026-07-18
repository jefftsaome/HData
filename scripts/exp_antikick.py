"""防踢实验（第2轮）— 干净对照，测真实踢出局数 + 主动重发能否防踢。

改进（相对第1轮）:
  - 进桌前对目标桌 + 上轮实验桌(2612) 先发 102 离桌，清掉崩溃残留座位；
  - 换一张本轮未进过的桌（bacc[1]）；
  - 收到 kick 不中断，继续观察（stay 模式自动重进），测量踢出间隔；
  - 记录 123 的原始负载（第1轮 table_id=0 需查清结构）。

判定:
  - 最后一次重进/进桌后连续 7 局无 kick → PROACTIVE_WORKS
  - 观察到 kick → 报告踢出发生在进桌后第几局、负载结构
  - 18 分钟未收敛 → INCONCLUSIVE

日志: logs/antikick_exp2.log
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from hdata.client import (
    GameClient,
    MultiTableSession,
    _WSConnection,
    _QS_OUT_GAME,
)
from hdata.protocol.codec import OT_GAME, build_message

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"
ACCOUNT, PASSWORD = "lidongsen1", "lds19830413"

LOG = Path("logs/antikick_exp2.log")
MAX_SECONDS = 18 * 60
TARGET_CLEAN_ROUNDS = 7     # 最后一次（重）进桌后连续无kick局数目标
STALE_TABLES = [2612]       # 第1轮实验桌，清残留


def log(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def main() -> int:
    LOG.unlink(missing_ok=True)
    log(f"实验2开始 account={ACCOUNT} 目标=连续{TARGET_CLEAN_ROUNDS}局无kick "
        f"上限={MAX_SECONDS}s")
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    s = await client.login(ACCOUNT, PASSWORD)
    log(f"登录OK player_id={s['player_id']} domain={s['domain']}")

    tables = await client.get_tables()
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    if len(bacc) < 2:
        log("VERDICT: INCONCLUSIVE 2001桌台不足2张")
        return 1
    target = bacc[1]     # 换一张本轮没进过的桌
    tid = int(target["table_id"])
    log(f"目标桌 table_id={tid}（跳过 bacc[0] 避开上轮残留）")

    # ── 手动接管连接：先清残留座位再进桌 ──
    conn = _WSConnection(client._require_session(),
                         on_before_connect=client._make_refresh_cb())
    await conn.__aenter__()
    for stale in set(STALE_TABLES + [tid]):
        await conn.send(build_message(
            _QS_OUT_GAME, {}, player_id=conn._player_id,
            game_type_id=2001, table_id=stale, service_type_id=OT_GAME))
        log(f"预离桌 102 -> table {stale}")
    await asyncio.sleep(2)

    mts = MultiTableSession(conn, [target], kick_policy="stay")
    for t in mts._tables:
        await mts._enter_one(t)
    t0 = time.time()
    log(f"已进桌 {tid} t=0s")

    verdict = "INCONCLUSIVE 未知原因结束"
    rounds: list[str] = []        # 当前观察段内的局号（kick后清零重计）
    kicks = 0
    seg_start = t0
    try:
        it = mts.events().__aiter__()
        while True:
            now = time.time() - t0
            if now > MAX_SECONDS:
                verdict = (f"INCONCLUSIVE 段内{len(rounds)}局/"
                           f"{MAX_SECONDS}s kicks={kicks}")
                break
            try:
                ev = await asyncio.wait_for(it.__anext__(), timeout=30)
            except asyncio.TimeoutError:
                log(f"心跳 t={now:.0f}s 段内{len(rounds)}局 kicks={kicks}")
                continue
            except StopAsyncIteration:
                verdict = f"ERROR 事件流意外结束（段内{len(rounds)}局）"
                break

            if ev["type"] == "round":
                d = ev.get("data") or {}
                rn = str(d.get("roundNo") or "")
                if rn and (not rounds or rounds[-1] != rn):
                    rounds.append(rn)
                    log(f"段内第{len(rounds)}局 roundNo={rn} "
                        f"t={now:.0f}s（段起点+{now - (seg_start - t0):.0f}s）")
                    if len(rounds) >= TARGET_CLEAN_ROUNDS:
                        verdict = (f"PROACTIVE_WORKS 进桌后连续{len(rounds)}局"
                                   f"无kick kicks={kicks}")
                        break
            elif ev["type"] == "kick":
                kicks += 1
                raw = (ev.get("data") or {}).get("raw")
                log(f"KICK#{kicks} 段内第{len(rounds)}局后 t={now:.0f}s "
                    f"table_id={ev.get('table_id')} "
                    f"raw={json.dumps(raw, ensure_ascii=False)}")
                rounds = []           # 自动重进后重新计段
                seg_start = time.time()
                if kicks >= 3:
                    verdict = (f"KICKED x{kicks} 主动重发/重进均被踢，"
                               f"详见日志（兜底重进可用）")
                    break
    except Exception as e:
        verdict = f"ERROR {type(e).__name__}: {e}（段内{len(rounds)}局）"
    finally:
        try:
            for t in mts._tables:
                await mts._leave_one(t)
            await conn.__aexit__()
        except Exception:
            pass
    log(f"VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
