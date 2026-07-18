"""WS 直连冒烟：刷新 game_token → 连接 → Login(10000) → 验证登录响应。

用法:
    .venv/Scripts/python.exe scripts/smoke_ws_login.py [account]

协议细节见 hdata/protocol/codec.py 模块 docstring。
"""

import asyncio
import json
import sys
import time

from hdata.auth.session import build_ws_config, refresh_game_session
from hdata.protocol.codec import (
    FS_LOGIN,
    build_login_msg,
    decode_frame,
    encode_frame,
    extract_param,
)


async def smoke(account: str = "lidongsen1") -> bool:
    # 1. 刷新 game_token（venue/launch），并写回缓存
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
    print(f"[1] game_token 已刷新 (backend={full['game_backend']})")

    # 2. 构造 ws_url（含 deviceId 与 platformId/applicationId/version 后缀）
    cfg = build_ws_config({
        "game_token": full["game_token"],
        "game_player_id": full["game_player_id"],
        "game_backend": full["game_backend"],
        "backend_domain_url_list": full.get("backend_domain_url_list", ""),
    })
    url = cfg["ws_url"]
    print(f"[2] 连接 wss://{cfg['host']}:{cfg['port']} (deviceId={cfg['device_id']})")

    import websockets
    async with websockets.connect(url, open_timeout=12, close_timeout=3,
                                  max_size=50 * 1024 * 1024) as ws:
        print("[3] 已连接，发送 Login(10000)...")
        msg = build_login_msg(full["game_token"], full["game_player_id"],
                              cfg["device_id"])
        await ws.send(encode_frame(msg))

        print("[4] 等待响应...")
        end = time.time() + 15
        n = 0
        login_ok = False
        while time.time() < end and n < 10:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            except Exception as e:
                print(f"  连接关闭: {type(e).__name__}")
                break
            n += 1
            if isinstance(raw, str):
                print(f"  frame#{n} TEXT: {raw[:200]}")
                continue
            decoded = decode_frame(raw)
            if not decoded:
                print(f"  frame#{n} BYTES len={len(raw)} 解码失败 head={raw[:32].hex()}")
                continue
            pid = decoded.get("protocolId")
            info = extract_param(decoded) or {}
            brief = json.dumps(info.get("param") or info.get("data") or info,
                               ensure_ascii=False, default=str)[:260]
            print(f"  frame#{n} protocolId={pid} status={info.get('status')} {brief}")
            if pid == FS_LOGIN and info.get("status") == 1:
                login_ok = True

        print(f"[5] 共收到 {n} 帧, 登录{'成功' if login_ok else '未确认'}")
        return login_ok


if __name__ == "__main__":
    acct = sys.argv[1] if len(sys.argv) > 1 else "lidongsen1"
    ok = asyncio.run(smoke(acct))
    print("冒烟结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
