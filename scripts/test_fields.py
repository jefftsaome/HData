#!/usr/bin/env python3
"""定向测试不同 e_obj 字段值对 verify 的影响。

每个测试用新的 captcha + jfbym，测试不同字段组合。
"""

import binascii, hashlib, json, os, random, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Crypto.Cipher import AES as AES_C
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)

def rand_uid():
    r = ""
    for _ in range(4):
        r += hex(int(65536 * (1 + random.random())))[2:].zfill(4)[-4:]
    return r

def gen_pow(ln, ci, hf, v, b, dt):
    br = b % 4; bd = b // 4; p = "0" * bd
    ps = f"{v}|{b}|{hf}|{dt}|{ci}|{ln}||"
    while True:
        h = rand_uid(); c = ps + h
        hd = hashlib.md5(c.encode()).hexdigest() if hf == "md5" else ""
        if br == 0:
            if hd.startswith(p): return {"pow_msg": c, "pow_sign": hd}
        elif hd.startswith(p) and len(p) <= [0, 7, 3, 1][br]:
            return {"pow_msg": c, "pow_sign": hd}

from hdt.auth.geetest_signer import LotParser
LP = LotParser

def make_w_eobj(ld, cs, eo_ov):
    """Make w from e_obj overrides. eo_ov can modify fields."""
    ln = ld["lot_number"]; pd = ld["pow_detail"]
    ca = [[int(p)*1 for p in c.split(',')] for c in cs.split('|')]
    lp = LP()
    eo = {**gen_pow(ln, CAPTCHA_ID, pd["hashfunc"], pd["version"], pd["bits"], pd["datetime"]),
          **lp.get_dict(ln),
          "EKAI": "y7R8", "biht": "1426265548", "device_id": "",
          "em": {"cp": 0, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1},
          "gee_guard": {"roe": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                                 "res": "3", "rew": "3", "sep": "3", "snh": "3"}},
          "ep": "123", "geetest": "captcha", "lang": "zh",
          "lot_number": ln, "userresponse": ca, "passtime": random.randint(600, 1200)}
    for k, v in eo_ov.items():
        if v is None: eo.pop(k, None)
        else: eo[k] = v
    rk = rand_uid()
    ej = json.dumps(eo, separators=(',', ':'))
    c = AES_C.new(rk.encode(), AES_C.MODE_CBC, b"0000000000000000")
    ee = c.encrypt(pad(ej.encode(), AES_C.block_size))
    rc = PKCS1_v1_5.new(construct((RSA_N, RSA_E)))
    ek = rc.encrypt(rk.encode())
    return binascii.hexlify(ee).decode() + binascii.hexlify(ek).decode(), ej

def verify(ld, w):
    from curl_cffi import requests as cr
    cb = f"botion_{int(time.time()*1000)}"
    p = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
         "lot_number": ld["lot_number"], "payload": ld["payload"],
         "process_token": ld["process_token"],
         "payload_protocol": ld.get("payload_protocol","1"), "pt": ld.get("pt","1"), "w": w}
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(p)
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/",
                           "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                  timeout=30)
    t = resp.text
    m = re.search(r"\((.*)\)$", t, re.DOTALL)
    if m:
        try:
            pj = json.loads(m.group(1))
            r = {"status": pj.get("status"), "result": pj.get("data",{}).get("result"),
                 "score": pj.get("data",{}).get("score"),
                 "fail_count": pj.get("data",{}).get("fail_count"),
                 "error": pj.get("msg"), "esize": len(ej)}
            return r
        except: pass
    return {"status": "parse_error", "raw": t[:200]}

# Single captcha fetch, reused for all tests
from hdt.auth.captcha import fetch_captcha
from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
import asyncio

print("Fetching captcha...")
ld = fetch_captcha()
if not ld: print("FAIL"); exit(1)
print(f"  lot_number: {ld['lot_number'][:20]}...")

print("Solving via jfbym...")
s = JfbymSolver(api_token=JFBYM_TOKEN)
ch = CaptchaChallenge(lot_number=ld["lot_number"], payload=ld["payload"],
    process_token=ld["process_token"], bg_url=ld["bg_url"],
    ques_urls=ld["ques_urls"], captcha_id=CAPTCHA_ID)
sol = asyncio.run(s.solve(ch))
cs = sol.coords
print(f"  coords: {cs}")

# Test variants
tests = [
    ("V0: Baseline", {}),
    ("V1: passtime=3000", {"passtime": 3000}),
    ("V2: em.cp=1", {"em": {"cp": 1, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1}}),
    ("V3: passtime=3000 + cp=1", {"passtime": 3000, "em": {"cp": 1, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1}}),
    ("V4: lang=zh-cn", {"lang": "zh-cn"}),
    ("V5: em={} + passtime=3000", {"em": {}, "passtime": 3000}),
    ("V6: NO gee_guard", {"gee_guard": None}),
    ("V7: gep_guard flat + passtime=3000", {
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                       "res": "3", "rew": "3", "sep": "3", "snh": "3"},
        "passtime": 3000, "em": {"cp": 1, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1},
    }),
]

for label, ov in tests:
    try:
        w, ej = make_w_eobj(ld, cs, ov)
        r = verify(ld, w)
        s_icon = "✅" if r.get("result") == "success" else "❌"
        print(f"\n{s_icon} {label}")
        print(f"   e_obj: {len(ej)}B → AES: {(len(w)-256)//2}B")
        print(f"   verify: {r}")
    except Exception as e:
        print(f"\n⚠️  {label}: ERROR {e}")
