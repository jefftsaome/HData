#!/usr/bin/env python3
"""分析真实 w vs 我们的 w，定位 e_obj 差异。
然后根据分析结果测试最可能的正确 e_obj 组合。
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

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)

# 读取真实 w
real_w = (DATA_DIR / "real_w_captured.txt").read_text().strip()
print(f"真实 w: {len(real_w)} hex chars")
print(f"  AES段: {len(real_w)-256} hex = {(len(real_w)-256)//2} bytes")
print(f"  RSA段: 256 hex = 128 bytes")
print(f"  AES块数: {(len(real_w)-256)//2//16} 个AES块")
print(f"  e_obj明文约: {(len(real_w)-256)//2-1}~{(len(real_w)-256)//2-16} bytes")
print()

# 构建我们的 e_obj 并测试不同组合
def build_w(load_data, coords_str, field_ov):
    """构建 e_obj + 加密 → w。"""
    lot_number = load_data["lot_number"]
    pow_detail = load_data["pow_detail"]
    coords_array = [[int(p.split(',')[0]), int(p.split(',')[1])]
                    for p in coords_str.split('|')]
    
    from hdt.auth.geetest_signer import LotParser, _generate_pow, _rand_uid
    lp = LotParser()
    
    eo = {
        **_generate_pow(lot_number, CAPTCHA_ID,
                       pow_detail["hashfunc"], pow_detail["version"],
                       pow_detail["bits"], pow_detail["datetime"]),
        **lp.get_dict(lot_number),
        "EKAI": "y7R8",
        "biht": "1426265548",
        "device_id": "",
        "em": {"cp": 0, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1},
        "gee_guard": {"roe": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                               "res": "3", "rew": "3", "sep": "3", "snh": "3"}},
        "ep": "123",
        "geetest": "captcha",
        "lang": "zh",
        "lot_number": lot_number,
        "userresponse": coords_array,
        "passtime": random.randint(600, 1200),
    }
    
    # 应用覆盖
    for k, v in field_ov.items():
        if v is None: eo.pop(k, None)
        else: eo[k] = v
    
    rk = _rand_uid()
    ej = json.dumps(eo, separators=(',', ':'))
    
    cipher = AES_C.new(rk.encode(), AES_C.MODE_CBC, b"0000000000000000")
    ee = cipher.encrypt(pad(ej.encode(), AES_C.block_size))
    rc = PKCS1_v1_5.new(construct((RSA_N, RSA_E)))
    ek = rc.encrypt(rk.encode())
    w = binascii.hexlify(ee).decode() + binascii.hexlify(ek).decode()
    return w, ej, eo

# 先分析真实 w 结构
real_aes_size = (len(real_w) - 256) // 2  # AES ciphertext bytes
real_plaintext_min = real_aes_size - 15  # PKCS7 padding: 1-16 bytes
real_plaintext_max = real_aes_size - 1

print(f"真实 w 的 e_obj 明文范围: {real_plaintext_min}~{real_plaintext_max} bytes")
print(f"目标: 找到 e_obj 组合，JSON 长度在 {real_plaintext_min}~{real_plaintext_max} 范围")
print()

# 获取一个 captcha 来测试
data = fetch_captcha()
if not data: print("fetch_captcha 失败"); exit(1)
print(f"测试用 lot_number: {data['lot_number']}")
print()

# 测试不同的 e_obj 组合
tests = [
    ("Baseline: 当前generate_w", {}),
    ("No device_id", {"device_id": None}),
    ("No ep+device_id", {"ep": None, "device_id": None}),
    ("No ep+device_id+flat gee_guard", {"ep": None, "device_id": None,
        "gee_guard": {"auh":"3","aup":"3","cdc":"3","egp":"3","res":"3","rew":"3","sep":"3","snh":"3"}}),
    ("No ep+device_id+minimal em", {"ep": None, "device_id": None,
        "em": {"cp": 0, "ek": "11"}}),
    ("No ep+device_id+em={}", {"ep": None, "device_id": None, "em": {}}),
    ("No EKAI+ep+device_id+em={}", {"EKAI": None, "ep": None, "device_id": None, "em": {}}),
    ("No EKAI+ep+device_id+em={}+flat gg", {"EKAI": None, "ep": None, "device_id": None, "em": {},
        "gee_guard": {"auh":"3","aup":"3","cdc":"3","egp":"3","res":"3","rew":"3","sep":"3","snh":"3"}}),
]

print("e_obj 大小分析:")
print(f"{'变体':<40s} {'JSON':>6s} {'AES段':>6s} {'vs真实':>10s}")
print("-" * 70)

best = None
for label, ov in tests:
    w, ej, eo = build_w(data, "100,100|200,100|150,150", ov)
    aes = (len(w)-256)//2
    vs_real = aes - real_aes_size
    marker = " <<< 匹配!" if real_plaintext_min <= len(ej) <= real_plaintext_max else ""
    print(f"{label:<40s} {len(ej):>5d}B {aes:>5d}B {vs_real:+>7d}B{marker}")
    
    if real_plaintext_min <= len(ej) <= real_plaintext_max:
        best = (label, ov, len(ej))
        print(f"\n  ✅ 找到匹配! JSON={len(ej)}B, AES={aes}B")
        print(f"  e_obj keys: {list(eo.keys())}")
        print(f"  e_obj: {json.dumps(eo, indent=2, ensure_ascii=False)[:500]}")

if best:
    print(f"\n最佳匹配: '{best[0]}' JSON={best[2]}B")

# 如果有匹配，测试 verify
if best:
    print(f"\n用 jfbym 测试最佳组合 verify...")
    from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
    JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
    if JFBYM_TOKEN:
        solver = JfbymSolver(api_token=JFBYM_TOKEN)
        ch = CaptchaChallenge(**{k:data[k] for k in ['lot_number','payload','process_token','bg_url','ques_urls']}, captcha_id=CAPTCHA_ID)
        sol = asyncio.run(solver.solve(ch))
        print(f"  jfbym: {sol.coords}")
        
        label, ov, _ = best
        w, ej, eo = build_w(data, sol.coords, ov)
        
        cb = f"botion_{int(time.time()*1000)}"
        params = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
            "lot_number": data["lot_number"], "payload": data["payload"],
            "process_token": data["process_token"],
            "payload_protocol": data.get("payload_protocol","1"),
            "pt": data.get("pt","1"), "w": w}
        url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
        
        resp = cr.get(url, impersonate="chrome110",
                      headers={"Referer": "https://www.leyu.me/"},
                      timeout=30)
        text = resp.text
        m = re.search(r"\((.*)\)$", text, re.DOTALL)
        if m:
            d = json.loads(m.group(1))
            r = d.get("data", {})
            print(f"  verify: status={d.get('status')} result={r.get('result')} "
                  f"fail_count={r.get('fail_count')} score={r.get('score')}")
        else:
            print(f"  raw: {text[:100]}")
