"""防踢实验（第3轮）— 纯被动观察，抓真实踢出的完整现场。

不做任何重进，直连收帧，记录每一帧 protocolId，连接断开时抓
close_code/close_reason 与异常类型，回答三个问题:
  1. 连续未下注 3 局预警(123/noticeId=21002)之后，第 5 局的"真踢"长什么样？
     是踢出帧（哪个协议号/负载），还是直接断连（close_code=?）？
  2. 踢出发生在第几局结束后？
  3. 断连前后是否还有其他帧？

进桌前对所有相关桌发 102 清残留座位。
日志: logs/antikick_exp3.log
"""
from __future__ import annotations

import asyncio
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

LOG = Path("logs/antikick_exp3.log")
MAX_SECONDS = 12 * 60
_HT_SEAT = 1
_PT_BASE = 2
STALE_TABLES = [2612, 2666]   # 前两轮实验桌


def log(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


async def main() -> int:
    LOG.unlink(missing_ok=True)
    log(f"实验3开始（纯被动，不重进）account={ACCOUNT} 上限={MAX_SECONDS}s")
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    s = await client.login(ACCOUNT, PASSWORD)
    log(f"登录OK player_id={s['player_id']}")

    tables = await client.get_tables()
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    if not bacc:
        log("VERDICT: INCONCLUSIVE 无2001桌台")
        return 1
    target = bacc[0]
    tid = int(target["table_id"])

    conn = _WSConnection(client._require_session(),
                         on_before_connect=client._make_refresh_cb())
    await conn.__aenter__()
    for stale in set(STALE_TABLES + [tid]):
        await conn.send(build_message(
            _QS_OUT_GAME, {}, player_id=conn._player_id,
            game_type_id=2001, table_id=stale, service_type_id=OT_GAME))
    await asyncio.sleep(2)
    log(f"预离桌完成，进桌 {tid}")

    # 直接发 401 进桌（绕过 MultiTableSession，完全手动）
    await conn.send(build_message(
        401, {"tableId": tid, "gameTypeId": 2001, "identity": _HT_SEAT,
              "joinTableMode": _PT_BASE, "gameCasinoId": 0,
              "deviceType": DEVICE_TYPE_PC, "deviceId": conn.device_id},
        player_id=conn._player_id, game_type_id=2001, table_id=tid,
        service_type_id=OT_GAME))
    t0 = time.time()
    log("已发401进桌 t=0s")

    last_round = ""
    n_rounds = 0
    try:
        while time.time() - t0 < MAX_SECONDS:
            try:
                frame = await asyncio.wait_for(conn.recv(), timeout=30)
            except asyncio.TimeoutError:
                log(f"心跳 t={time.time()-t0:.0f}s 局数={n_rounds}")
                continue
            if not frame:
                continue
            pid = frame.get("protocolId")
            info = extract_param(frame) or {}
            payload = info.get("param") or info.get("data")
            import json as _json
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    pass
            # 全量记录关键帧；普通帧只记协议号
            if pid == 104 and isinstance(payload, dict):
                rn = str(payload.get("roundNo") or "")
                if rn and rn != last_round:
                    last_round = rn
                    n_rounds += 1
                    log(f"第{n_rounds}局 roundNo={rn} "
                        f"t={time.time()-t0:.0f}s")
            elif pid == _QS_NOTICE:
                log(f"!!! 123通知 t={time.time()-t0:.0f}s "
                    f"payload={_json.dumps(payload, ensure_ascii=False)}")
            elif pid in (401, 101):
                pass  # 进桌响应不记
            elif pid == 10026:
                log(f"!!! 10026会话踢 t={time.time()-t0:.0f}s "
                    f"payload={_json.dumps(payload, ensure_ascii=False)}")
            else:
                log(f"帧 pid={pid} t={time.time()-t0:.0f}s "
                    f"keys={list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__}")
    except Exception as e:
        ws = conn._ws
        log(f"!!! 连接断开 t={time.time()-t0:.0f}s 局数={n_rounds} "
            f"exc={type(e).__name__}: {e} "
            f"close_code={getattr(ws, 'close_code', '?')} "
            f"close_reason={getattr(ws, 'close_reason', '?')!r}")
    try:
        await conn.__aexit__()
    except Exception:
        pass
    log(f"VERDICT: 观察结束 局数={n_rounds}（详见上方帧记录）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
