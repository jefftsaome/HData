#!/usr/bin/env python3
"""测试坐标偏移对 verify 结果的影响。
每个测试使用独立的新 captcha + jfbym 坐标，然后尝试不同的偏移。
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
from hdata.auth.captcha import fetch_captcha
from hdata.auth.captcha_solver import JfbymSolver, CaptchaChallenge
from hdata.auth.geetest_signer import generate_w

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")

if not JFBYM_TOKEN:
    print("需要 JFBYM_TOKEN")
    sys.exit(1)

# 获取一个新 captcha + jfbym
ld = fetch_captcha()
print(f"lot_number: {ld['lot_number']}")

solver = JfbymSolver(api_token=JFBYM_TOKEN)
ch = CaptchaChallenge(**{k:ld[k] for k in ['lot_number','payload','process_token','bg_url','ques_urls']}, captcha_id=CAPTCHA_ID)
sol = asyncio.run(solver.solve(ch))
original_pts = sol.pts
print(f"jfbym 原始坐标: {original_pts}")

# 测试不同的偏移
offsets = [
    ("原始", 0, 0),
    ("右+10", 10, 0),
    ("下+10", 0, 10),
    ("右-5下-5", -5, -5),
]

n = 0
success = False
for label, dx, dy in offsets:
    n += 1
    adjusted = [[x+dx, y+dy] for x, y in original_pts]
    coords_str = "|".join(f"{x},{y}" for x, y in adjusted)
    
    w = generate_w(ld, CAPTCHA_ID, coords_str)
    
    cb = f"botion_{int(time.time()*1000)}"
    params = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": ld["lot_number"], "payload": ld["payload"],
        "process_token": ld["process_token"],
        "payload_protocol": ld.get("payload_protocol","1"), "pt": ld.get("pt","1"), "w": w}
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/"},
                  timeout=30)
    text = resp.text
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            result = d.get("data", {}).get("result")
            fc = d.get("data", {}).get("fail_count")
            sc = d.get("data", {}).get("score")
            status = d.get("status")
            icon = "✅" if result == "success" else "❌"
            print(f"  {icon} {n}. {label:10s} dx={dx:+d} dy={dy:+d} → {adjusted}  status={status} result={result} fail_count={fc} score={sc}")
            if result == "success" and not success:
                print(f"\n🎉🎉🎉 首次 success! 偏移: {label}")
                success = True
        except:
            print(f"  ⚠️ {n}. {label}: parse error")
    else:
        print(f"  ⚠️ {n}. {label}: 响应异常: {text[:80]}")
    
    # 如果 fail_count 达到 3，停止（可能被锁定）
    if not success and n >= 3:
        pass  # 继续试
    
    time.sleep(0.5)  # 避免被限速

if not success:
    print(f"\n❌ 所有偏移都失败，问题可能不在坐标精度")
else:
    print(f"\n✅ 坐标偏移找到了通过方案! 偏移: ???")
