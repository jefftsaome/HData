"""快速验证：主动 102 离桌后，服务器是否推 102 确认？leaveTableType=?"""
import asyncio, json, sys, time
sys.path.insert(0, ".")
from hdata.client import GameClient, _WSConnection
from hdata.protocol.codec import OT_GAME, DEVICE_TYPE_PC, build_message, extract_param

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"

async def main():
    client = GameClient(entry_url=ENTRY_URL, geepass_token=GEEPASS, jfbym_token=JFBYM)
    await client.login("lidongsen1", "lds19830413")
    tables = await client.get_tables()
    tid = int([t for t in tables if t["game_type_id"] == 2001][0]["table_id"])
    conn = _WSConnection(client._require_session(), on_before_connect=client._make_refresh_cb())
    await conn.__aenter__()
    await conn.send(build_message(401, {"tableId": tid, "gameTypeId": 2001, "identity": 1,
        "joinTableMode": 2, "gameCasinoId": 0, "deviceType": DEVICE_TYPE_PC,
        "deviceId": conn.device_id}, player_id=conn._player_id,
        game_type_id=2001, table_id=tid, service_type_id=OT_GAME))
    await asyncio.sleep(3)
    print(f"进桌 {tid}，3秒后主动发102离桌")
    await conn.send(build_message(102, {}, player_id=conn._player_id,
        game_type_id=2001, table_id=tid, service_type_id=OT_GAME))
    end = time.time() + 8
    got = False
    while time.time() < end:
        try:
            frame = await asyncio.wait_for(conn.recv(), timeout=max(0.1, end - time.time()))
        except asyncio.TimeoutError:
            break
        if frame and frame.get("protocolId") == 102:
            p = extract_param(frame) or {}
            print(f"收到服务器102推送: {json.dumps(p.get('param') or p.get('data'), ensure_ascii=False)}")
            got = True
    if not got:
        print("8秒内未收到102推送（主动离桌无服务器确认帧）")
    await conn.__aexit__()

asyncio.run(main())
