"""端到端冒烟：login → get_tables → enter_table → events。"""
import asyncio
import sys
sys.path.insert(0, ".")
from hdata.client import GameClient

GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"


async def main():
    client = GameClient(entry_url="https://leyu.com",
                    geepass_token=GEEPASS, jfbym_token=JFBYM)
    s = await client.login("lidongsen1", "lds19830413")
    print(f"[login] OK player_id={s['player_id']} domain={s['domain']} "
          f"backend={s['backend']} exp={s['game_exp']}")

    tables = await client.get_tables()
    print(f"[tables] 共 {len(tables)} 张桌")
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    print(f"[tables] 经典百家乐 {len(bacc)} 张:")
    for t in bacc[:5]:
        print(f"  table={t['table_id']} status={t['status']} "
              f"boot={t['boot_no']} road={t['road_flat'][:20]} ({t['road_count']}局)")

    if not bacc:
        print("没有可进的桌台")
        return
    target = bacc[0]
    print(f"[enter] 进桌 {target['table_id']} ...")
    async with await client.enter_table(target["table_id"], 2001) as ts:
        snap = ts.snapshot
        print(f"[snapshot] tableName={snap.get('tableName')} "
              f"bootNo={snap.get('bootNo')} roundNo={snap.get('roundNo')} "
              f"dealer={snap.get('dealerName')}")
        print(f"[road] {ts.road_flat()[:30]}")
        print("[events] 收 5 个事件 ...")
        n = 0
        async for ev in ts.events():
            print(f"  ev type={ev['type']} pid={ev['protocol_id']} "
                  f"keys={list(ev['data'].keys())[:6] if isinstance(ev['data'], dict) else ev['data']}")
            n += 1
            if n >= 5:
                break
    print("[done] 全流程 OK")


asyncio.run(main())
