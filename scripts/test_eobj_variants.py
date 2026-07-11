#!/usr/bin/env python3
"""尝试不同的 e_obj 变体，逐个测试 verify 响应变化。

通过系统性地修改 e_obj 字段（移除/简化），定位 verify result=fail 的根因。
每次尝试记录 verify 响应，输出差异分析。

用法:
    uv run python scripts/test_eobj_variants.py [--jfbym]
"""

import binascii
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Crypto.Cipher import AES as AES_CRYPTO
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")

RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)


def _rand_uid():
    result = ""
    for _ in range(4):
        result += hex(int(65536 * (1 + random.random())))[2:].zfill(4)[-4:]
    return result


def generate_pow(lot_number, captcha_id, hash_func, version, bits, date):
    bit_remainder = bits % 4
    bit_division = bits // 4
    prefix = "0" * bit_division
    pow_string = f"{version}|{bits}|{hash_func}|{date}|{captcha_id}|{lot_number}||"
    while True:
        h = _rand_uid()
        combined = pow_string + h
        if hash_func == "md5":
            hashed = hashlib.md5(combined.encode()).hexdigest()
        elif hash_func == "sha1":
            hashed = hashlib.sha1(combined.encode()).hexdigest()
        elif hash_func == "sha256":
            hashed = hashlib.sha256(combined.encode()).hexdigest()
        else:
            hashed = ""
        if bit_remainder == 0:
            if hashed.startswith(prefix):
                return {"pow_msg": pow_string + h, "pow_sign": hashed}
        elif hashed.startswith(prefix) and len(prefix) <= [0, 7, 3, 1][bit_remainder]:
            return {"pow_msg": pow_string + h, "pow_sign": hashed}


class LotParser:
    def __init__(self, mapping=None):
        if mapping is None:
            mapping = {
                "(n[3:3]+n[30:30]+n[22:22]+n[18:18])+.+(n[12:12]+n[8:8]+n[3:3]+n[1:1])": "n[24:27]"
            }
        self.mapping = mapping
        self.lot = self._parse(list(self.mapping.keys())[0])
        self.lot_res = self._parse(list(self.mapping.values())[0])

    @staticmethod
    def _parse_slice(s):
        return [int(x) for x in s.split(":")]

    @staticmethod
    def _extract(part):
        return re.search(r"\[(.*?)\]", part).group(1)

    def _parse(self, s):
        parts = s.split("+.+")
        parsed = []
        for part in parts:
            if "+" in part:
                subs = part.split("+")
                parsed.append([self._parse_slice(self._extract(sub)) for sub in subs])
            else:
                parsed.append([self._parse_slice(self._extract(part))])
        return parsed

    @staticmethod
    def _build_str(parsed, num):
        result = []
        for p in parsed:
            current = []
            for s in p:
                start = s[0]
                end = (s[1] + 1) if len(s) > 1 else (start + 1)
                current.append(num[start:end])
            result.append("".join(current))
        return ".".join(result)

    def get_dict(self, lot_number):
        i = self._build_str(self.lot, lot_number)
        r = self._build_str(self.lot_res, lot_number)
        parts = i.split(".")
        a = {}
        current = a
        for idx, part in enumerate(parts):
            if idx == len(parts) - 1:
                current[part] = r
            else:
                current[part] = current.get(part, {})
                current = current[part]
        return a


def make_w(load_data, coords_str, eobj_overrides=None):
    """构建并加密 e_obj，返回 w 参数 + e_obj 信息。"""
    lot_number = load_data["lot_number"]
    pow_detail = load_data["pow_detail"]
    
    coords_array = [[int(p.split(',')[0]), int(p.split(',')[1])]
                    for p in coords_str.split('|')]
    
    lot_parser = LotParser()
    
    # Base e_obj (match the current generate_w)
    e_obj = {
        **generate_pow(lot_number, CAPTCHA_ID,
                       pow_detail["hashfunc"],
                       pow_detail["version"],
                       pow_detail["bits"],
                       pow_detail["datetime"]),
        **lot_parser.get_dict(lot_number),
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
    
    # Apply overrides
    if eobj_overrides:
        for k, v in eobj_overrides.items():
            if v is None:
                e_obj.pop(k, None)
            else:
                e_obj[k] = v
    
    random_key = _rand_uid()
    eobj_json = json.dumps(e_obj, separators=(',', ':'))
    
    # Encrypt
    rsa_pubkey = construct((RSA_N, RSA_E))
    cipher = AES_CRYPTO.new(random_key.encode(), AES_CRYPTO.MODE_CBC, b"0000000000000000")
    encrypted_eobj = cipher.encrypt(pad(eobj_json.encode(), AES_CRYPTO.block_size))
    rsa_cipher = PKCS1_v1_5.new(rsa_pubkey)
    encrypted_key = rsa_cipher.encrypt(random_key.encode())
    
    w = binascii.hexlify(encrypted_eobj).decode() + binascii.hexlify(encrypted_key).decode()
    
    return w, e_obj, eobj_json, random_key


def call_verify(load_data, w):
    """调用 verify API 并返回解析后的结果。"""
    from curl_cffi import requests as cr
    
    cb = f"botion_{int(time.time() * 1000)}"
    params = {
        "callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": load_data["lot_number"], "payload": load_data["payload"],
        "process_token": load_data["process_token"],
        "payload_protocol": load_data.get("payload_protocol", "1"),
        "pt": load_data.get("pt", "1"), "w": w,
    }
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/",
                           "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                  timeout=30)
    text = resp.text
    
    result = {"status_code": resp.status_code}
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            result["status"] = parsed.get("status")
            result["result"] = parsed.get("data", {}).get("result")
            result["fail_count"] = parsed.get("data", {}).get("fail_count")
            result["score"] = parsed.get("data", {}).get("score")
            result["error"] = parsed.get("msg")
            result["error_code"] = parsed.get("code")
        except:
            pass
    
    return result


def test_variant(label, load_data, coords, overrides, ref_size=None):
    """测试一个 e_obj 变体。"""
    print(f"\n--- 测试: {label} ---")
    w, e_obj, eobj_json, rk = make_w(load_data, coords, overrides)
    
    aes_size = len(w) - 256  # RSA part is 256 hex
    print(f"  e_obj JSON: {len(eobj_json)} bytes")
    print(f"  AES段: {aes_size} hex ({aes_size//2} bytes)")
    print(f"  w 总长: {len(w)} hex chars")
    
    if ref_size:
        diff = aes_size - ref_size
        print(f"  与参考差异: {diff:+d} hex ({diff//2:+d} bytes)")
    
    result = call_verify(load_data, w)
    print(f"  verify: status={result.get('status')}, result={result.get('result')}")
    
    if result.get("error"):
        print(f"  error: [{result.get('error_code')}] {result.get('error')}")
    
    return result


def get_jfbym_coords(load_data):
    """通过 jfbym 获取真实坐标。"""
    from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
    import asyncio
    
    solver = JfbymSolver(api_token=JFBYM_TOKEN)
    challenge = CaptchaChallenge(
        lot_number=load_data["lot_number"],
        payload=load_data["payload"],
        process_token=load_data["process_token"],
        bg_url=load_data["bg_url"],
        ques_urls=load_data["ques_urls"],
        captcha_id=CAPTCHA_ID,
    )
    solution = asyncio.run(solver.solve(challenge))
    return solution.coords, solution.pts


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--jfbym", action="store_true", help="用 jfbym 真实坐标")
    p.add_argument("--coords", default="74,124|235,132|176,65", help="指定坐标")
    p.add_argument("--only", type=int, help="只运行第 N 个测试")
    args = p.parse_args()
    
    from hdt.auth.captcha import fetch_captcha
    print("获取验证码...")
    data = fetch_captcha()
    if not data:
        print("fetch_captcha 失败")
        return 1
    
    print(f"  lot_number: {data['lot_number'][:20]}...")
    print(f"  datetime: {data['pow_detail']['datetime']}")
    
    coords = args.coords
    pts = []
    if args.jfbym:
        if not JFBYM_TOKEN:
            print("需要设置 JFBYM_TOKEN")
            return 1
        print("用 jfbym 获取坐标...")
        coords, pts = get_jfbym_coords(data)
        print(f"  coords: {coords}")
    else:
        pts = [[int(x) for x in p.split(',')] for p in coords.split('|')]
    
    # Reference: real_w.txt has 928 hex AES = 464 bytes AES ciphertext
    # sdk_flow has 960 hex AES = 480 bytes AES ciphertext
    # Real e_obj plaintext is roughly 448-479 bytes
    # Our e_obj is 568 bytes → AES ciphertext = 576 bytes (1152 hex)
    ref_aes_hex = 960  # from sdk_flow (successful verify)
    
    print(f"\n参考 AES 段大小: {ref_aes_hex} hex ({ref_aes_hex//2} bytes)")
    
    tests = []
    
    # --- Test 0: Baseline (current generate_w) ---
    tests.append(("V0: 当前 generate_w()", None))
    
    # --- Test 1: Remove EKAI ---
    tests.append(("V1: 移除 EKAI", {"EKAI": None}))
    
    # --- Test 2: Flat gee_guard (no roe wrapper) ---
    tests.append(("V2: gee_guard 扁平化", {
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                       "res": "3", "rew": "3", "sep": "3", "snh": "3"}
    }))
    
    # --- Test 3: Shorter datetime in pow_msg ---
    # Replace the ISO datetime with Unix timestamp
    tests.append(("V3: pow_msg 短 datetime", {
        "_short_datetime": True
    }))
    
    # --- Test 4: No device_id ---
    tests.append(("V4: 移除 device_id", {"device_id": None}))
    
    # --- Test 5: Minimal em ---
    tests.append(("V5: em 精简", {"em": {}}))
    
    # --- Test 6: Remove ep ---
    tests.append(("V6: 移除 ep", {"ep": None}))
    
    # --- Test 7: Combined - remove multiple fields ---
    tests.append(("V7: 组合精简 (移除 EKAI+device_id+ep, flat gee_guard)", {
        "EKAI": None,
        "device_id": None,
        "ep": None,
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                       "res": "3", "rew": "3", "sep": "3", "snh": "3"}
    }))
    
    # --- Test 8: Geo (standard GeeTest) - use ZAhG instead of EKAI ---
    tests.append(("V8: ZAhG 代替 EKAI", {"EKAI": None, "ZAhG": "MwHu"}))
    
    # --- Test 9: Different coord format (string instead of array) ---
    tests.append(("V9: userresponse 字符串格式", {
        "userresponse": "74,124,235,132,176,65"
    }))
    
    # --- Test 10: Remove biht ---
    tests.append(("V10: 移除 biht", {"biht": None}))
    
    # --- Test 11: Minimal em + no device_id + no EKAI + flat gee_guard ---
    tests.append(("V11: 大幅精简 (多字段移除)", {
        "EKAI": None,
        "device_id": None,
        "ep": None,
        "biht": None,
        "em": {},
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                       "res": "3", "rew": "3", "sep": "3", "snh": "3"}
    }))
    
    # --- Test 12: Try with only essential fields ---
    tests.append(("V12: 最小字段集 (pow+lot_parser+lot_number+userresponse+passtime)", {
        "EKAI": None, "biht": None, "device_id": None, "em": {},
        "gee_guard": {}, "ep": None, "geetest": None, "lang": None,
    }))
    
    # --- Test 13: Re-add geetest+lang ---
    tests.append(("V13: 最小+geetest+lang", {
        "EKAI": None, "biht": None, "device_id": None, "em": {},
        "gee_guard": {}, "ep": None,
    }))
    
    # Run tests
    for i, (label, overrides) in enumerate(tests):
        if args.only is not None and i != args.only:
            continue
        
        if label == "V3: pow_msg 短 datetime":
            # Need special handling - override the datetime format in pow generation
            # Use Unix timestamp-like datetime
            short_dt = str(int(time.time()))
            w, e_obj, eobj_json, rk = make_w(data, coords, {
                "_override_datetime": short_dt,
            })
            # We need to regenerate pow with the short datetime
            # Actually, let me just override the pow_msg/pow_sign
            pow_data = generate_pow(data["lot_number"], CAPTCHA_ID,
                                     data["pow_detail"]["hashfunc"],
                                     data["pow_detail"]["version"],
                                     data["pow_detail"]["bits"],
                                     short_dt)
            overrides = {
                "pow_msg": pow_data["pow_msg"],
                "pow_sign": pow_data["pow_sign"],
            }
        
        if "_override_datetime" in (overrides or {}):
            continue  # handled above
        
        if label == "V3: pow_msg 短 datetime":
            # Re-do with proper override
            short_dt = str(int(time.time()))
            pow_data = generate_pow(data["lot_number"], CAPTCHA_ID,
                                     data["pow_detail"]["hashfunc"],
                                     data["pow_detail"]["version"],
                                     data["pow_detail"]["bits"],
                                     short_dt)
            overrides = {
                "pow_msg": pow_data["pow_msg"],
                "pow_sign": pow_data["pow_sign"],
            }
        
        print(f"\n{'='*60}")
        test_variant(f"{i}: {label}", data, coords, overrides, ref_aes_hex)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
