"""单桌台数据核对脚本 — 实时打印采集到的桌台数据，用于与网页界面人工比对。

用法:
    .venv/Scripts/python.exe scripts/verify_table.py <账号> <密码> [--table 关键词或桌台ID]

示例:
    .venv/Scripts/python.exe scripts/verify_table.py lds001 lds19830413
    .venv/Scripts/python.exe scripts/verify_table.py lds001 lds19830413 --table B03
    .venv/Scripts/python.exe scripts/verify_table.py lds001 lds19830413 --table 2304

输出说明（每局结算时打印一行）:
    靴序/局号  结果(庄/闲/和+点数)  庄池/闲池/和池金额  下注人数  在线人数  当前路纸尾部
网页上可比对的位置:
    - 结果与点数 → 游戏界面牌面与路纸
    - 庄/闲/和池  → 下注区域各方总额
    - 路纸尾部    → 珠盘路最后一列
Ctrl+C 优雅退出。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import sys

sys.path.insert(0, ".")

from hdata.client import GameClient, LoginError, round_result_token

ENTRY_URL = "https://leyu.com"
GEEPASS = "5a5ca5f84f0d49d1bde19cea2fd71e425s4e7xtqdlm4gvm7"
JFBYM = "ZF3_3Gq7as0TRNBEO3DW51m8XIz0dRpXeElLS8FmdU8"

CN = {"B": "庄", "P": "闲", "T": "和", "B6": "庄(6点)"}


def _fmt_money(x) -> str:
    if x is None:
        return "-"
    return f"{x:,.0f}"


def _pools(bet: dict) -> dict:
    """110 帧 jackpotPoolInfos → {庄, 闲, 和} 金额。"""
    out = {"B": 0.0, "P": 0.0, "T": 0.0}
    for p in bet.get("jackpotPoolInfos") or []:
        pid = p.get("betPointId")
        amt = p.get("totalAmount") or 0
        if pid in (3001, 3013):
            out["B"] += amt
        elif pid == 3002:
            out["P"] += amt
        elif pid == 3003:
            out["T"] += amt
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("account")
    ap.add_argument("password")
    ap.add_argument("--table", default="", help="桌台ID或桌名关键词(如 B03)")
    args = ap.parse_args()

    client = GameClient(entry_url=ENTRY_URL,
                        geepass_token=GEEPASS, jfbym_token=JFBYM)
    print(f"[登录] {args.account} ...")
    try:
        s = await client.login(args.account, args.password)
    except LoginError as e:
        print(f"❌ 登录失败: {e}")
        return 1
    print(f"[登录] ✅ player_id={s['player_id']}")

    tables = await client.get_tables()
    # 只留百家乐类（有路纸的）
    if args.table:
        kw = args.table
        cand = [t for t in tables
                if str(t["table_id"]) == kw or kw in (t.get("table_name") or "")]
        if not cand:
            print(f"❌ 未找到匹配「{kw}」的桌台。")
            print("   注意: 平台按账号分组推送桌台（已实测: 不同账号可见桌集差异很大），")
            print("   网页上存在的桌台不一定推给本账号。当前账号可见桌台:")
            for t in tables[:60]:
                print(f"   {t['table_id']}  {t['game_type_name']}  {t.get('table_name','')}")
            return 1
    else:
        cand = [t for t in tables if t.get("road_count", 0) > 0]
        print("[选桌] 未指定 --table，自动选路纸最深的桌。可选桌台(前15):")
        for t in tables[:15]:
            print(f"   {t['table_id']}  {t['game_type_name']}  {t.get('table_name','')}"
                  f"  路纸{t.get('road_count',0)}局")
    target = max(cand, key=lambda t: t.get("road_count", 0))
    tid = target["table_id"]
    gtid = target["game_type_id"]
    print(f"[选桌] {tid}「{target.get('table_name','')}」{target['game_type_name']}"
          f"  在线{target.get('online','-')}  好路:{target.get('good_roads') or '-'}")

    last_bet: dict = {}
    last104: dict = {}

    print("━" * 100)
    print(f"{'时间':<10}{'靴序/局号':<12}{'结果':<10}{'点数':<8}"
          f"{'庄池':>12}{'闲池':>12}{'和池':>10}{'人数':>6}{'在线':>6}  路纸尾部")
    print("━" * 100)

    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _sig)
        except (NotImplementedError, RuntimeError):
            pass

    async with await client.enter_table(tid, gtid) as ts:
        snap = ts.snapshot
        print(f"[进桌] 桌名={snap.get('tableName')} 荷官={snap.get('dealerName')}"
              f"  靴号={snap.get('bootNo')}")
        print(f"[路纸] 当前完整路纸({len(ts.road_flat())}局): {ts.road_flat()}")
        print("━" * 100)
        async for ev in ts.events():
            if stop.is_set():
                break
            pid = ev["protocol_id"]
            d = ev["data"]
            if pid == 104:
                last104 = d
                print(f"  [新局] roundNo={d.get('roundNo')} "
                      f"bootIndex={d.get('bootIndex')} "
                      f"倒计时={(d.get('countdownEndTime',0)-d.get('serverTime',0))//1000}s"
                      f"          ", end="\r")
            elif pid == 110:
                last_bet = d
            elif pid == 107:
                rr = d.get("roundResult", "")
                token = round_result_token(rr)
                if not token:
                    continue
                try:
                    b_pt, p_pt = (int(x) for x in rr.split(";", 1))
                except Exception:
                    b_pt = p_pt = None
                pools = _pools(last_bet)
                road = ts.road_flat()
                tstr = datetime.datetime.now().strftime("%H:%M:%S")
                boot_idx = last104.get("bootIndex", "?")
                round_no = d.get("roundNo") or last104.get("roundNo") or "?"
                pts = f"{b_pt}:{p_pt}" if b_pt is not None else "-"
                print(f"{tstr:<10}{str(boot_idx)+'/'+str(round_no):<12}"
                      f"{CN.get(token, token):<10}{pts:<8}"
                      f"{_fmt_money(pools['B']):>12}{_fmt_money(pools['P']):>12}"
                      f"{_fmt_money(pools['T']):>10}"
                      f"{last_bet.get('currentRoundPlayerCount','-'):>6}"
                      f"{(snap.get('tableOnline') or {}).get('onlineNumber','-'):>6}"
                      f"  …{road[-20:]}")
            elif ev["type"] == "kick":
                print(f"  [踢出] 被系统踢出，已自动重进")

    print("\n[退出] 已离桌")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
