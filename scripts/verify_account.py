"""账号体检脚本 — 一键验证账号全链路是否健康。

用法:
    .venv/Scripts/python.exe scripts/verify_account.py <账号> <密码>
    .venv/Scripts/python.exe scripts/verify_account.py lds001 lds19830413

检查项:
  1. 登录（缓存/打码自动选择）
  2. 大厅桌台列表拉取
  3. 进桌 + 快照 + 事件流
退出码: 0 = 全部通过, 1 = 某一步失败
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from hdata.client import GameClient, LoginError

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"


async def verify(account: str, password: str) -> bool:
    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)

    # ── 1. 登录 ──
    print(f"━━━ 1/3 登录 {account} ━━━")
    try:
        s = await client.login(account, password)
    except LoginError as e:
        print(f"❌ 登录失败: {e}")
        return False
    print(f"✅ player_id={s['player_id']}")
    print(f"   domain={s['domain']}  backend={s['backend']}")

    # ── 2. 桌台列表 ──
    print("━━━ 2/3 大厅桌台列表 ━━━")
    try:
        tables = await client.get_tables()
    except Exception as e:
        print(f"❌ 拉取失败: {e}")
        return False
    print(f"✅ 共 {len(tables)} 张桌")
    by_type: dict[str, int] = {}
    for t in tables:
        by_type[t["game_type_name"]] = by_type.get(t["game_type_name"], 0) + 1
    for name, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"   {name}: {cnt} 张")

    # ── 3. 进桌读数据 ──
    print("━━━ 3/3 进桌读数据 ━━━")
    bacc = [t for t in tables if t["game_type_id"] == 2001]
    if not bacc:
        print("⚠️ 当前无经典百家乐桌台，跳过进桌测试")
        return True
    target = bacc[0]
    try:
        async with await client.enter_table(target["table_id"], 2001) as ts:
            snap = ts.snapshot
            print(f"✅ 进桌 {target['table_id']}「{snap.get('tableName')}」"
                  f"荷官={snap.get('dealerName')}")
            n = 0
            async for ev in ts.events():
                print(f"   事件: {ev['type']} (pid={ev['protocol_id']})")
                n += 1
                if n >= 3:
                    break
    except Exception as e:
        print(f"❌ 进桌失败: {e}")
        return False

    print("━━━ 体检通过 ✅ ━━━")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    ok = asyncio.run(verify(sys.argv[1], sys.argv[2]))
    sys.exit(0 if ok else 1)
