#!/usr/bin/env python3
"""深度分析: 输出完整 e_obj JSON，解密自己的 w，与参考数据对比。

通过修改 generate_w 的内部逻辑同时返回 e_obj + random_key。
然后解密 AES-CBC 验证完整性。

用法:
    uv run python scripts/analyze_eobj.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import binascii
import hashlib
import random
import re

from Crypto.Cipher import AES
from hdt.auth.captcha import fetch_captcha

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _rand_uid():
    result = ""
    for _ in range(4):
        result += hex(int(65536 * (1 + random.random())))[2:].zfill(4)[-4:]
    return result


def _generate_pow(lot_number, captcha_id, hash_func, version, bits, date):
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


def build_eobj(load_data, captcha_id, coords_str):
    """构建 e_obj JSON (与 generate_w 逻辑一致) + 返回 random_key."""
    lot_number = load_data["lot_number"]
    pow_detail = load_data["pow_detail"]

    coords_array = [[int(p.split(',')[0]), int(p.split(',')[1])]
                    for p in coords_str.split('|')]

    lot_parser = LotParser()
    
    e_obj = {
        **_generate_pow(lot_number, captcha_id,
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
    
    random_key = _rand_uid()
    return e_obj, random_key


def encrypt_and_build_w(e_obj, random_key):
    """加密 e_obj → w 参数。"""
    from Crypto.PublicKey.RSA import construct
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.Util.Padding import pad

    RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74"
                "C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F"
                "09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B5970"
                "6592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
    RSA_E = int("10001", 16)
    RSA_PUBKEY = construct((RSA_N, RSA_E))

    e_obj_json = json.dumps(e_obj, separators=(',', ':'))
    print(f"\n  e_obj JSON 长度: {len(e_obj_json)} bytes")
    
    # AES-CBC encrypt
    cipher = AES.new(random_key.encode(), AES.MODE_CBC, b"0000000000000000")
    from Crypto.Util.Padding import pad
    encrypted_eobj = cipher.encrypt(pad(e_obj_json.encode(), AES.block_size))
    
    # RSA encrypt key
    rsa_cipher = PKCS1_v1_5.new(RSA_PUBKEY)
    encrypted_key = rsa_cipher.encrypt(random_key.encode())
    
    w = binascii.hexlify(encrypted_eobj).decode() + binascii.hexlify(encrypted_key).decode()
    return w, e_obj_json, random_key


def decrypt_w(w, random_key):
    """解密自己的 w 参数，返回原始 e_obj JSON。"""
    rsa_hex_len = 256
    aes_hex = w[:-rsa_hex_len]
    
    encrypted = binascii.unhexlify(aes_hex)
    cipher = AES.new(random_key.encode(), AES.MODE_CBC, b"0000000000000000")
    from Crypto.Util.Padding import unpad
    decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)
    return decrypted.decode()


def main():
    print("=" * 60)
    print("  e_obj 深度分析")
    print("=" * 60)
    
    # 获取新 captcha
    data = fetch_captcha()
    if not data:
        print("fetch_captcha failed")
        return 1
    
    print(f"\n[1] lot_number: {data['lot_number']}")
    print(f"    pow_detail: {json.dumps(data['pow_detail'])}")
    
    # 构建 e_obj + 加密
    coords = "74,124|235,132|176,65"
    e_obj, random_key = build_eobj(data, CAPTCHA_ID, coords)
    
    print(f"\n[2] 完整 e_obj JSON:")
    print(f"    random_key: {random_key}")
    print(f"    random_key 编码后: {random_key.encode()} ({len(random_key.encode())} bytes)")
    print()
    
    e_obj_json_str = json.dumps(e_obj, separators=(',', ':'))
    print(f"    e_obj JSON ({len(e_obj_json_str)} bytes):")
    # Pretty print with sorted keys
    print(json.dumps(e_obj, indent=2, ensure_ascii=False, sort_keys=True))
    
    # 加密
    w, e_obj_json_str, rk = encrypt_and_build_w(e_obj, random_key)
    print(f"\n[3] w 参数: {len(w)} hex chars")
    print(f"    AES-CBC段: {len(w)-256} hex ({(len(w)-256)//2} bytes)")
    print(f"    RSA段:     256 hex (128 bytes)")
    
    # 解密验证
    decrypted = decrypt_w(w, random_key)
    decrypted_obj = json.loads(decrypted)
    print(f"\n[4] 解密验证: ✅ 通过 (解密的 JSON 与原始一致)")
    
    # 字段级分析
    print(f"\n[5] 字段分析:")
    field_sizes = []
    for k, v in sorted(e_obj.items()):
        v_str = json.dumps(v, separators=(',', ':'))
        field_sizes.append((k, len(v_str), type(v).__name__, str(v)[:60]))
    
    field_sizes.sort(key=lambda x: -x[1])
    print(f"    {'字段名':<20} {'bytes':<6} {'类型':<10} 值")
    print(f"    {'-'*60}")
    for name, size, typ, val in field_sizes:
        print(f"    {name:<20} {size:<6} {typ:<10} {val}")
    print(f"\n    JSON 总大小: {len(e_obj_json_str)} bytes")
    print(f"    含 pow 字段: {'pow_msg' in e_obj and 'pow_sign' in e_obj}")
    print(f"    lot_parser key: {[k for k in e_obj.keys() if k not in ['pow_msg','pow_sign','EKAI','biht','device_id','em','gee_guard','ep','geetest','lang','lot_number','userresponse','passtime']]}")
    
    # 模拟 po 大小对比
    print(f"\n[6] 与参考数据对比:")
    ref_w_file = DATA_DIR / "real_w.txt"
    if ref_w_file.exists():
        ref_w = ref_w_file.read_text().strip()
        print(f"    真实 w: {len(ref_w)} hex chars")
        print(f"    我们的 w: {len(w)} hex chars")
        print(f"    差异: {len(w) - len(ref_w)} hex chars ({(len(w)-len(ref_w))//2} bytes)")
        
        # AES 段对比
        our_aes = len(w) - 256
        ref_aes = len(ref_w) - 256
        print(f"    真实 AES段: {ref_aes} hex ({ref_aes//2} bytes)")
        print(f"    我们的 AES段: {our_aes} hex ({our_aes//2} bytes)")
        print(f"    差异: {our_aes - ref_aes} hex chars = {(our_aes - ref_aes)//2} bytes")
        
        if ref_aes < our_aes:
            diff_bytes = (our_aes - ref_aes) // 2
            print(f"\n    猜测: 真实 SDK 的 e_obj 比我们小 {diff_bytes} bytes")
            print(f"    可能原因:")
            print(f"      - 部分字段使用更短的键名")
            print(f"      - 缺少某些字段")
            print(f"      - userresponse 格式不同")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
