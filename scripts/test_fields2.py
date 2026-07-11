#!/usr/bin/env python3
"""定向测试不同 e_obj 参数对 verify 的影响，使用原始 generate_w + 字段覆盖。

用法:
    JFBYM_TOKEN=xxx uv run python scripts/test_fields2.py [passtime] [em_cp]
"""

import json, os, random, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import binascii
from Crypto.Cipher import AES as AES_C
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad, unpad

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
# RSA key
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)


def main():
    from hdata.auth.captcha import fetch_captcha
    from hdata.auth.captcha_solver import JfbymSolver, CaptchaChallenge
    import asyncio
    
    # Fresh captcha
    ld = fetch_captcha()
    if not ld: print("fetch_captcha failed"); return 1
    print(f"lot_number: {ld['lot_number']}")
    
    # jfbym
    solver = JfbymSolver(api_token=JFBYM_TOKEN)
    ch = CaptchaChallenge(lot_number=ld["lot_number"], payload=ld["payload"],
        process_token=ld["process_token"], bg_url=ld["bg_url"],
        ques_urls=ld["ques_urls"], captcha_id=CAPTCHA_ID)
    sol = asyncio.run(solver.solve(ch))
    coords = sol.coords
    pts = sol.pts
    print(f"jfbym coords: {coords}")
    
    # Define test cases
    tests = []
    
    # T1: Original generate_w
    tests.append(("T1: generate_w original", {}))
    
    # T2: Large passtime (simulating human delay)
    tests.append(("T2: passtime=5000ms", {"passtime": 5000}))
    
    # T3: cp=1 (click present)
    tests.append(("T3: em.cp=1", {"em": {"cp": 1, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1}}))
    
    # T4: Both
    tests.append(("T4: passtime=5000 + em.cp=1", {"passtime": 5000, "em": {"cp": 1, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1}}))
    
    # T5: Remove gee_guard  
    tests.append(("T5: no gee_guard", {"gee_guard": None, "passtime": 5000}))
    
    # T6: Flat gee_guard (no roe)
    tests.append(("T6: flat gee_guard", {"gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3", "res": "3", "rew": "3", "sep": "3", "snh": "3"}, "passtime": 5000}))
    
    # T7: No em at all
    tests.append(("T7: no em", {"em": None, "passtime": 5000}))
    
    # T8: Minimal em
    tests.append(("T8: minimal em={}", {"em": {}, "passtime": 5000}))
    
    # T9: No device_id
    tests.append(("T9: no device_id", {"device_id": None, "passtime": 5000}))
    
    # T10: No ep
    tests.append(("T10: no ep", {"ep": None, "passtime": 5000}))
    
    # T11: Multiple removals
    tests.append(("T11: no ep+device_id+gee_guard", {"ep": None, "device_id": None, "gee_guard": None, "passtime": 5000}))
    
    for label, ov in tests:
        try:
            result = test_one(ld, coords, ov)
            status = "✅" if result.get("result") == "success" else "❌"
            print(f"\n{status} {label}")
            print(f"   AES段: {(len(result['w'])-256)//2}B, e_obj: {result['eobj_size']}B")
            print(f"   status={result.get('status')}, result={result.get('result')}", end="")
            if result.get("fail_count"): print(f", fail_count={result['fail_count']}", end="")
            if result.get("score"): print(f", score={result['score']}", end="")
            print()
        except Exception as e:
            print(f"\n⚠️  {label}: ERROR {e}")
            import traceback; traceback.print_exc()
    
    return 0


def test_one(ld, coords_str, overrides):
    """Test verify with modified e_obj."""
    from hdata.auth.geetest_signer import generate_w, LotParser
    import hashlib
    
    lot_number = ld["lot_number"]
    pow_detail = ld["pow_detail"]
    
    # Build e_obj the same way as generate_w
    coords_array = [[int(p.split(',')[0]), int(p.split(',')[1])]
                    for p in coords_str.split('|')]
    
    lp = LotParser()
    
    # Replicate the pow generation
    b = pow_detail["bits"]; bd = b // 4
    pow_string = f"{pow_detail['version']}|{b}|{pow_detail['hashfunc']}|{pow_detail['datetime']}|{CAPTCHA_ID}|{lot_number}||"
    
    # Generate pow just like _generate_pow does
    from hdata.auth.geetest_signer import _generate_pow as gen_pow
    import types
    # _generate_pow is a module-level function in geetest_signer
    # But it's private. Let me just replicate it.
    br = b % 4
    prefix = "0" * bd
    
    def _rand_uid():
        r = ""
        for _ in range(4):
            r += hex(int(65536 * (1 + random.random())))[2:].zfill(4)[-4:]
        return r
    
    pow_data = None
    attempts = 0
    while pow_data is None and attempts < 100:
        h = _rand_uid()
        combined = pow_string + h
        hashed = hashlib.md5(combined.encode()).hexdigest()
        if br == 0:
            if hashed.startswith(prefix):
                pow_data = {"pow_msg": combined, "pow_sign": hashed}
        elif hashed.startswith(prefix) and len(prefix) <= [0, 7, 3, 1][br]:
            pow_data = {"pow_msg": combined, "pow_sign": hashed}
        attempts += 1
    if not pow_data:
        raise RuntimeError("pow generation failed")
    
    eo = {
        **pow_data,
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
    
    # Apply overrides BEFORE passtime override
    for k, v in overrides.items():
        if v is None and k in eo:
            del eo[k]
        elif v is not None:
            eo[k] = v
    
    # Ensure passtime override works correctly
    if "passtime" in overrides:
        eo["passtime"] = overrides["passtime"]
    if "em" in overrides:
        eo["em"] = overrides["em"]
    
    random_key = _rand_uid()
    eobj_json = json.dumps(eo, separators=(',', ':'))
    
    # Encrypt
    cipher = AES_C.new(random_key.encode(), AES_C.MODE_CBC, b"0000000000000000")
    encrypted_eobj = cipher.encrypt(pad(eobj_json.encode(), AES_C.block_size))
    rsa_cipher = PKCS1_v1_5.new(construct((RSA_N, RSA_E)))
    encrypted_key = rsa_cipher.encrypt(random_key.encode())
    
    w = binascii.hexlify(encrypted_eobj).decode() + binascii.hexlify(encrypted_key).decode()
    
    # Verify
    from curl_cffi import requests as cr
    cb = f"botion_{int(time.time()*1000)}"
    params = {
        "callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": lot_number, "payload": ld["payload"],
        "process_token": ld["process_token"],
        "payload_protocol": ld.get("payload_protocol", "1"),
        "pt": ld.get("pt", "1"), "w": w,
    }
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/",
                           "User-Agent": "Mozilla/5.0"},
                  timeout=30)
    text = resp.text
    result = {"w_len": len(w), "eobj_size": len(eobj_json)}
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if m:
        try:
            pj = json.loads(m.group(1))
            result["status"] = pj.get("status")
            result["result"] = pj.get("data", {}).get("result")
            result["fail_count"] = pj.get("data", {}).get("fail_count")
            result["score"] = pj.get("data", {}).get("score")
        except:
            result["parse_error"] = text[:100]
    else:
        result["raw"] = text[:100]
    result["w"] = w
    return result


if __name__ == "__main__":
    sys.exit(main())
