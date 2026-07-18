"""WS 进桌冒烟：登录 → 拉桌台列表 → 进桌 → 收牌局帧。

用法:
    .venv/Scripts/python.exe scripts/smoke_ws_table.py [account] [--watch N]

协议要点（来自游戏前端静态分析）:
  - 桌台列表: TABLE_LIST_ALL=10089, {labelTypeId:1}, serviceTypeId=Ot.HALL(7)
  - 进桌:     NEW_INTER_GAME=401 (普通百家乐), INTER_GAME=101 (VIP 等),
              data={tableId, gameTypeId, identity:1(SEAT), joinTableMode:2(BASE),
                    gameCasinoId}, serviceTypeId=Ot.GAME(3)
  - 离桌:     OUT_GAME=102
"""

import asyncio
import json
import sys
import time

from hdata.auth.session import build_ws_config, refresh_game_session
from hdata.protocol.codec import (
    FS_LOGIN,
    OT_GAME,
    OT_HALL,
    DEVICE_TYPE_PC,
    build_login_msg,
    build_message,
    decode_frame,
    encode_frame,
    extract_param,
)

QS_TABLE_LIST_ALL = 10089
QS_NEW_INTER_GAME = 401
QS_INTER_GAME = 101
QS_OUT_GAME = 102
KICK_OUT_GAME = 123   # 桌台级踢出（含连续5局未投注被踢）

# notForceExitArr 中的游戏类型走 101，其余走 401
FORCE_101_GAME_TYPES = {2003, 2004, 2014, 2020}  # BID/VIP/HIGH_STAKES/BLACKJACK 等

HT_SEAT = 1
PT_BASE = 2


async def main(account: str = "lidongsen1", watch_sec: int = 30):
    # ── 1. 刷新 token + 连接 ──
    cache_path = f".cache/{account}.json"
    full = json.loads(open(cache_path, encoding="utf-8").read())
    params = await refresh_game_session(account, full)
    full["game_token"] = params["token"]
    full["game_backend"] = params.get("backendDomainUrl", full.get("game_backend", ""))
    full["backend_domain_url_list"] = params.get(
        "backendDomainUrlList", full.get("backend_domain_url_list", ""))
    full["updated_at"] = int(time.time())
    open(cache_path, "w", encoding="utf-8").write(
        json.dumps(full, ensure_ascii=False, indent=2))
    print(f"[1] game_token 已刷新")

    cfg = build_ws_config({
        "game_token": full["game_token"],
        "game_player_id": full["game_player_id"],
        "game_backend": full["game_backend"],
        "backend_domain_url_list": full.get("backend_domain_url_list", ""),
    })
    token = full["game_token"]
    player_id = full["game_player_id"]
    device_id = cfg["device_id"]

    import websockets
    async with websockets.connect(cfg["ws_url"], open_timeout=12, close_timeout=3,
                                  max_size=50 * 1024 * 1024) as ws:
        print(f"[2] 已连接 wss://{cfg['host']}:{cfg['port']}")

        async def send(msg):
            await ws.send(encode_frame(msg))

        async def recv_frames(timeout: float):
            """收帧生成器：yield (protocol_id, info_dict, raw_frame)。"""
            end = time.time() + timeout
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(0.1, end - time.time()))
                except asyncio.TimeoutError:
                    return
                if isinstance(raw, str):
                    continue
                frame = decode_frame(raw)
                if frame:
                    yield frame.get("protocolId"), extract_param(frame), frame

        # ── 2. 登录 ──
        await send(build_login_msg(token, player_id, device_id))
        login_ok = False
        async for pid, info, _ in recv_frames(10):
            if pid == FS_LOGIN and info.get("status") == 1:
                login_ok = True
                break
            if pid == 10026:
                print(f"[x] 登录失败: {info.get('msg')}")
                return False
        if not login_ok:
            print("[x] 登录无响应")
            return False
        print("[3] 登录成功")

        # ── 3. 拉桌台列表（10089 触发，实际数据在 10052 推送里） ──
        await send(build_message(QS_TABLE_LIST_ALL, {"labelTypeId": 1},
                                 player_id=player_id, game_type_id=2013,
                                 service_type_id=OT_HALL))
        tables = []
        game_table_map = {}
        async for pid, info, _ in recv_frames(12):
            if pid == 10052:
                data = info.get("param") or info.get("data") or {}
                if isinstance(data, str):
                    data = json.loads(data)
                game_table_map.update(data.get("gameTableMap") or {})
                if game_table_map:
                    # 收到首个快照即可（后续还有增量）
                    if len(game_table_map) >= 10:
                        break
        if not game_table_map:
            print("[x] 未收到 10052 桌台快照")
            return False
        print(f"[4] 10052 快照: {len(game_table_map)} 桌")
        first_key = sorted(game_table_map.keys(), key=int)[0]
        first = game_table_map[first_key]
        print(f"    样例 tableId={first_key} fields={sorted(first.keys())[:20]}")

        # 选桌：优先普通百家乐(2001)且游戏中(gameStatus!=0)
        def _gt(v):
            return v.get("gameTypeId") or 0
        baccarat = [(tid, v) for tid, v in game_table_map.items() if _gt(v) == 2001]
        pool = baccarat or [(tid, v) for tid, v in game_table_map.items() if _gt(v)]
        if not pool:
            print("[x] 快照里没有可用桌台")
            return False
        table_id, tinfo = pool[0]
        table_id = int(table_id)
        game_type = _gt(tinfo)
        casino_id = tinfo.get("gameCasinoId", 0) or 0
        print(f"    目标桌 tableId={table_id} gameType={game_type} "
              f"status={tinfo.get('gameStatus')} (百家乐桌 {len(baccarat)} 张)")

        # ── 4. 进桌 ──
        enter_proto = QS_INTER_GAME if game_type in FORCE_101_GAME_TYPES else QS_NEW_INTER_GAME
        enter_data = {
            "tableId": table_id,
            "gameTypeId": game_type,
            "identity": HT_SEAT,
            "joinTableMode": PT_BASE,
            "gameCasinoId": casino_id,
            "deviceType": DEVICE_TYPE_PC,
            "deviceId": device_id,
        }
        print(f"[5] 进桌 tableId={table_id} gameType={game_type} proto={enter_proto}")
        await send(build_message(enter_proto, enter_data,
                                 player_id=player_id, game_type_id=game_type,
                                 table_id=table_id, service_type_id=OT_GAME))

        # ── 5. 收听牌局帧（含踢出自动重进） ──
        print(f"[6] 收听 {watch_sec}s 牌局数据...")
        end = time.time() + watch_sec
        counter = {}
        kicked = False
        reenter_count = 0
        entered = False
        samples = []
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            except Exception as e:
                print(f"  连接关闭: {type(e).__name__}")
                break
            if isinstance(raw, str):
                continue
            frame = decode_frame(raw)
            if not frame:
                continue
            pid = frame.get("protocolId")
            counter[pid] = counter.get(pid, 0) + 1
            info = extract_param(frame) or {}
            # 收集前几帧样例
            if len(samples) < 8 and pid not in (10028, 10011, 10040):
                samples.append((pid, json.dumps(
                    info.get("param") or info.get("data") or info,
                    ensure_ascii=False, default=str)[:220]))
            # 会话级踢出（token 失效等）：无法重进，直接失败
            if pid == 10026:
                print(f"  [!] 会话被踢(10026): {json.dumps(info, ensure_ascii=False)[:200]}")
                kicked = True
                break
            # 桌台级踢出（KICK_OUT_GAME=123，含连续5局未投注）：自动重进
            if pid == KICK_OUT_GAME:
                reenter_count += 1
                print(f"  [!] 被踢出桌台(123) 第{reenter_count}次，自动重进...")
                await send(build_message(enter_proto, enter_data,
                                         player_id=player_id,
                                         game_type_id=game_type,
                                         table_id=table_id,
                                         service_type_id=OT_GAME))
                continue
            # 进桌响应确认
            if pid == enter_proto and info.get("status") == 1:
                entered = True
                print(f"  [✓] 进桌成功 (pid={pid})")

        print(f"[7] 帧统计: {dict(sorted(counter.items(), key=lambda x: -x[1]))}")
        print(f"    进桌成功={entered} 重进次数={reenter_count}")
        for pid, s in samples:
            print(f"  sample pid={pid}: {s}")
        return entered and not kicked


if __name__ == "__main__":
    acct = "lidongsen1"
    watch = 30
    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        acct = args[0]
    if "--watch" in args:
        watch = int(args[args.index("--watch") + 1])
    ok = asyncio.run(main(acct, watch))
    print("进桌冒烟:", "PASS" if ok else "FAIL/被踢")
    sys.exit(0 if ok else 1)
