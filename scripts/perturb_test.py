#!/usr/bin/env python3
"""扰动测试：对 jfbym 坐标做随机抖动，看是否能通过 verify。

每次用新 captcha + jfbym，然后对坐标施加不同幅度随机偏移，
记录每个扰动级别的通过率。

用法:
    JFBYM_TOKEN=xxx uv run python scripts/perturb_test.py [--rounds 5]
"""
import asyncio, json, os, random, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import binascii, hashlib
from Crypto.Cipher import AES as AES_C
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad
from curl_cffi import requests as cr
from hdt.auth.captcha import fetch_captcha
from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
from hdt.auth.geetest_signer import LotParser, _generate_pow, _rand_uid

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)


def make_w(load_data, pts, passtime):
    """用给定坐标和 passtime 生成 w。"""
    ln = load_data["lot_number"]
    pd = load_data["pow_detail"]
    lp = LotParser()
    
    eo = {
        **_generate_pow(ln, CAPTCHA_ID, pd["hashfunc"], pd["version"], pd["bits"], pd["datetime"]),
        **lp.get_dict(ln),
        "biht": "1426265548", "em": {},
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                       "res": "3", "rew": "3", "sep": "3", "snh": "3"},
        "geetest": "captcha", "lang": "zh", "lot_number": ln,
        "userresponse": pts, "passtime": passtime,
    }
    rk = _rand_uid()
    ej = json.dumps(eo, separators=(',', ':'))
    cipher = AES_C.new(rk.encode(), AES_C.MODE_CBC, b"0000000000000000")
    ee = cipher.encrypt(pad(ej.encode(), AES_C.block_size))
    rc = PKCS1_v1_5.new(construct((RSA_N, RSA_E)))
    ek = rc.encrypt(rk.encode())
    return binascii.hexlify(ee).decode() + binascii.hexlify(ek).decode()


def verify(load_data, w):
    """调用 verify API，返回结果。"""
    cb = f"botion_{int(time.time()*1000)}"
    params = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": load_data["lot_number"], "payload": load_data["payload"],
        "process_token": load_data["process_token"],
        "payload_protocol": load_data.get("payload_protocol", "1"),
        "pt": load_data.get("pt", "1"), "w": w}
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/",
                           "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                  timeout=30)
    m = re.search(r"\((.*)\)$", resp.text, re.DOTALL)
    if m:
        d = json.loads(m.group(1))
        r = d.get("data", {})
        return {"status": d.get("status"), "result": r.get("result"),
                "fail_count": r.get("fail_count"), "score": r.get("score")}
    return {"result": "parse_error"}


def perturb(pts, max_offset):
    """对坐标列表施加随机偏移 [-max_offset, +max_offset]。"""
    return [[max(0, min(299, x + random.randint(-max_offset, max_offset))),
             max(0, min(199, y + random.randint(-max_offset, max_offset)))]
            for x, y in pts]


async def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=6, help="每轮测试次数")
    p.add_argument("--perturbations", nargs="+", type=int,
                   default=[0, 1, 2, 3, 4, 5],
                   help="要测试的偏移量列表")
    args = p.parse_args()
    
    if not JFBYM_TOKEN:
        print("需要 JFBYM_TOKEN"); return
    
    solver = JfbymSolver(api_token=JFBYM_TOKEN)
    
    # 每个扰动级别统计
    results = {offset: {"pass": 0, "fail": 0, "error": 0} for offset in args.perturbations}
    
    print(f"扰动测试: {len(args.perturbations)} 种偏移 × {args.rounds} 轮 = {len(args.perturbations)*args.rounds} 次 verify")
    print()
    
    for round_num in range(args.rounds):
        for pt_idx, max_offset in enumerate(args.perturbations):
            # 新 captcha + jfbym
            try:
                data = fetch_captcha()
                if not data: raise Exception("fetch_captcha failed")
            except Exception as e:
                print(f"  ⚠️  captcha获取失败: {e}")
                results[max_offset]["error"] += 1
                continue
            
            try:
                challenge = CaptchaChallenge(
                    lot_number=data["lot_number"], payload=data["payload"],
                    process_token=data["process_token"], bg_url=data["bg_url"],
                    ques_urls=data["ques_urls"], captcha_id=CAPTCHA_ID)
                sol = await solver.solve(challenge)
                raw_pts = sol.pts
            except Exception as e:
                print(f"  ⚠️  jfbym失败: {e}")
                results[max_offset]["error"] += 1
                continue
            
            # 扰动
            if max_offset == 0:
                pts = raw_pts  # 原始坐标，没有扰动
            else:
                pts = perturb(raw_pts, max_offset)
            
            # 随机 passtime (800~3000ms)
            passtime = random.randint(800, 3000)
            
            # 生成 w + verify
            w = make_w(data, pts, passtime)
            r = verify(data, w)
            
            # 统计
            if r.get("result") == "success":
                results[max_offset]["pass"] += 1
            elif r.get("result") == "fail":
                results[max_offset]["fail"] += 1
            else:
                results[max_offset]["error"] += 1
            
            # 每轮输出
            icon = "✅" if r.get("result") == "success" else "❌"
            print(f"  R{round_num+1}/{len(args.perturbations)} 偏移={max_offset:+d}px "
                  f"pts={pts} passtime={passtime}ms "
                  f"{icon} {r.get('result', 'ERR')}")
            
            time.sleep(1)  # 避免限速
    
    # 输出统计
    print(f"\n{'='*60}")
    print(f"  统计结果 ({args.rounds} 轮)")
    print(f"{'='*60}")
    print(f"  {'偏移':>6s}  {'通过':>4s}  {'失败':>4s}  {'错误':>4s}  {'通过率':>8s}")
    print(f"  {'-'*35}")
    for offset in args.perturbations:
        r = results[offset]
        total = r["pass"] + r["fail"] + r["error"]
        rate = r["pass"] / total * 100 if total > 0 else 0
        bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
        print(f"  {offset:+>5d}px  {r['pass']:>4d}  {r['fail']:>4d}  {r['error']:>4d}  {rate:>6.1f}%  {bar}")


if __name__ == "__main__":
    asyncio.run(main())
