#!/usr/bin/env python3
"""测试匹配的 e_obj 组合 + jfbym 坐标的 verify 结果。"""
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

# 获取 captcha
data = fetch_captcha()
print(f"lot_number: {data['lot_number']}")

# jfbym
solver = JfbymSolver(api_token=JFBYM_TOKEN)
ch = CaptchaChallenge(**{k:data[k] for k in ['lot_number','payload','process_token','bg_url','ques_urls']}, captcha_id=CAPTCHA_ID)
sol = asyncio.run(solver.solve(ch))
coords = sol.coords
print(f"jfbym: {coords}")

# 构建匹配真实 SDK 的 e_obj (No EKAI, no ep, no device_id, em={})
def build_optimized_w(ld, cs):
    ln = ld["lot_number"]; pd = ld["pow_detail"]
    ca = [[int(p.split(',')[0]), int(p.split(',')[1])] for p in cs.split('|')]
    lp = LotParser()
    
    eo = {
        **_generate_pow(ln, CAPTCHA_ID, pd["hashfunc"], pd["version"], pd["bits"], pd["datetime"]),
        **lp.get_dict(ln),
        "biht": "1426265548",
        "em": {},
        "gee_guard": {"roe": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                               "res": "3", "rew": "3", "sep": "3", "snh": "3"}},
        "geetest": "captcha",
        "lang": "zh",
        "lot_number": ln,
        "userresponse": ca,
        "passtime": random.randint(2000, 4000),
    }
    rk = _rand_uid()
    ej = json.dumps(eo, separators=(',', ':'))
    cipher = AES_C.new(rk.encode(), AES_C.MODE_CBC, b"0000000000000000")
    ee = cipher.encrypt(pad(ej.encode(), AES_C.block_size))
    rc = PKCS1_v1_5.new(construct((RSA_N, RSA_E)))
    ek = rc.encrypt(rk.encode())
    w = binascii.hexlify(ee).decode() + binascii.hexlify(ek).decode()
    return w, ej

# 测试 1: 匹配版本 (No EKAI, no ep, no device_id, em={})
w1, ej1 = build_optimized_w(data, coords)
print(f"\nV1(匹配): e_obj={len(ej1)}B, AES段={(len(w1)-256)//2}B")

cb = f"botion_{int(time.time()*1000)}"
params = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
    "lot_number": data["lot_number"], "payload": data["payload"],
    "process_token": data["process_token"],
    "payload_protocol": data.get("payload_protocol","1"),
    "pt": data.get("pt","1"), "w": w1}
url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
resp = cr.get(url, impersonate="chrome110",
              headers={"Referer": "https://www.leyu.me/"}, timeout=30)
text = resp.text
m = re.search(r"\((.*)\)$", text, re.DOTALL)
if m:
    d = json.loads(m.group(1))
    r = d.get("data", {})
    print(f"  verify: status={d.get('status')} result={r.get('result')} fail_count={r.get('fail_count')} score={r.get('score')}")
    if r.get("result") == "success":
        print(f"\n🎉🎉🎉 纯 HTTP verify 成功!")
else:
    print(f"  raw: {text[:100]}")
