"""防踢实验（第4轮）— 抓 102 踢出负载值 + 验证踢后重进。

流程（全程一条连接）:
  A. 清残留 -> 进桌，纯被动等 5 局；
  B. 收到服务器 102 推送时记录完整负载值
     （leaveTableType / msg / noticeId —— 区分"被踢"与"主动离桌"的关键）；
  C. 立即重发 401 进桌，观察能否恢复接收该桌数据（≥2 局）；
  D. 主动发 102 离桌，观察服务器是否也推 102 及其 leaveTableType 值。

日志: logs/antikick_exp4.log
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from hdata.client import GameClient, _WSConnection, _QS_OUT_GAME, _QS_NOTICE
from hdata.protocol.codec import (
    OT_GAME,
    DEVICE_TYPE_PC,
    build_message,
    extract_param,
)

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"
ACCOUNT, PASSWORD = "lidongsen1", "lds19830413"

LOG = Path("logs/antikick_exp4.log")
MAX_SECONDS = 14 * 60
_HT_SEAT = 1
_PT_BASE = 2
STALE_TABLES = [2612, 2666, 2555]


def log(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def dumps(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return repr(obj)


async def recv_frame(conn, timeout=30):
    try:
        return await asyncio.wait_for(conn.recv(), timeout=timeout)
    except asyncio.TimeoutError:
        return "TIMEOUT"


def payload_of(frame):
    info = extract_param(frame) or {}
    p = info.get("param") or info.get("data")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            pass
    return p


async def enter_401(conn, tid):
    await conn.send(build_message(
        401, {"tableId": tid, "gameTypeId": 2001, "identity": _HT_SEAT,
              "joinTableMode": _PT_BASE, "gameCasinoId": 0,
              "deviceType": DEVICE_TYPE_PC, "deviceId": conn.device_id},
        player_id=conn._player_id, game_type_id=2001, table_id=tid,
        service_type_id=OT_GAME))


async def main() -> int:
    LOG.unlink(missing_ok=True)
    log(f"实验4开始 account={ACCOUNT} 上限={MAX_SECONDS}s")
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    await client.login(ACCOUNT, PASSWORD)
    tables = await client.get_tables()
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    if not bacc:
        log("VERDICT: INCONCLUSIVE 无2001桌台")
        return 1
    tid = int(bacc[0]["table_id"])

    conn = _WSConnection(client._require_session(),
                         on_before_connect=client._make_refresh_cb())
    await conn.__aenter__()
    for stale in set(STALE_TABLES + [tid]):
        await conn.send(build_message(
            _QS_OUT_GAME, {}, player_id=conn._player_id,
            game_type_id=2001, table_id=stale, service_type_id=OT_GAME))
    await asyncio.sleep(2)

    t0 = time.time()
    phase = "A_等踢"
    kick_payload = None
    reenter_rounds = 0
    last_round = ""
    n_rounds = 0
    log(f"A: 进桌 {tid} t=0s")
    await enter_401(conn, tid)

    try:
        while time.time() - t0 < MAX_SECONDS:
            frame = await recv_frame(conn, timeout=30)
            now = time.time() - t0
            if frame == "TIMEOUT":
                log(f"心跳 t={now:.0f}s phase={phase} 局数={n_rounds} "
                    f"重进后局数={reenter_rounds}")
                if phase == "C_重进验证" and reenter_rounds >= 2:
                    phase = "D_主动离桌"
                    log(f"D: 主动发102离桌 {tid} t={now:.0f}s")
                    await conn.send(build_message(
                        _QS_OUT_GAME, {}, player_id=conn._player_id,
                        game_type_id=2001, table_id=tid,
                        service_type_id=OT_GAME))
                continue
            if not frame:
                continue
            pid = frame.get("protocolId")
            p = payload_of(frame)

            if pid == 104 and isinstance(p, dict):
                rn = str(p.get("roundNo") or "")
                if rn and rn != last_round:
                    last_round = rn
                    if phase == "C_重进验证":
                        reenter_rounds += 1
                        log(f"C: 重进后第{reenter_rounds}局 roundNo={rn} "
                            f"t={now:.0f}s")
                    else:
                        n_rounds += 1
                        log(f"A: 第{n_rounds}局 roundNo={rn} t={now:.0f}s")
            elif pid == _QS_NOTICE:
                log(f"123预警 t={now:.0f}s {dumps(p)}")
            elif pid == 102 and isinstance(p, dict):
                if phase == "A_等踢" and kick_payload is None:
                    kick_payload = p
                    log(f"!!! 102踢出 t={now:.0f}s 完整负载={dumps(p)}")
                    phase = "C_重进验证"
                    last_round = ""
                    log(f"C: 立即重发401进桌 {tid}")
                    await enter_401(conn, tid)
                elif phase == "D_主动离桌":
                    log(f"!!! D: 主动离桌后收到102推送 t={now:.0f}s "
                        f"完整负载={dumps(p)}")
                    break
                else:
                    log(f"102帧 phase={phase} t={now:.0f}s {dumps(p)}")
            elif pid == 10026:
                log(f"!!! 10026会话踢 t={now:.0f}s {dumps(p)}")
                break
    except Exception as e:
        ws = conn._ws
        log(f"!!! 连接断开 t={time.time()-t0:.0f}s phase={phase} "
            f"exc={type(e).__name__}: {e} "
            f"close_code={getattr(ws, 'close_code', '?')}")

    try:
        await conn.__aexit__()
    except Exception:
        pass
    ok = (kick_payload is not None and reenter_rounds >= 2)
    log(f"VERDICT: {'REENTER_WORKS 踢后重进恢复监控' if ok else 'INCOMPLETE'} "
        f"kick={'有' if kick_payload else '无'} 重进后局数={reenter_rounds}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
