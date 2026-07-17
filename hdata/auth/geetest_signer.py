"""GeeTest v4 w 参数签名 — 基于 GeekedTest 的 RSA/AES 加密实现。

只做签名，不包含 solver（坐标由 jfbym 30112 获取）。
"""

import binascii
import hashlib
import json
import random
import re
import urllib.parse
from pathlib import Path

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey.RSA import construct
from Crypto.Util.Padding import pad

# ═══════════════════════════════════════════════════════════
# RSA 公钥（从 GeekedTest 提取，GeeTest 全局统一）
# ═══════════════════════════════════════════════════════════

_RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
_RSA_E = int("10001", 16)
RSA_PUBKEY = construct((_RSA_N, _RSA_E))


def extract_rsa_key_from_browser(cdp_port: int | None = None) -> tuple[int, int] | None:
    """从浏览器 botion SDK 运行时提取 RSA 公钥 (n, e)。

    优先从 bcaptcha.js 静态数组提取，失败则返回 None。

    Returns:
        (n, e) 元组，或 None
    """
    try:
        with open(Path(__file__).resolve().parent.parent.parent
                  / "data" / "botion_js" / "bcaptcha.js") as f:
            js = f.read()

        # 256-byte 数组: n = [214, 144, 233, 254, ...]
        import re as _re
        match = _re.search(r',n=\[(214,144,233[^\]]+)\]', js)
        if not match:
            return None

        arr = [int(x.strip()) for x in match.group(1).split(',')]
        # 128-byte 窗口，第一个和最后字节都是奇数的 1024-bit n
        for start in range(len(arr)):
            end = start + 128
            if end > len(arr):
                break
            if arr[end - 1] % 2 == 1:  # 最后字节奇数 = n 是奇数
                n_bytes = bytes(arr[start:end])
                n = int.from_bytes(n_bytes, 'big')
                if n.bit_length() == 1024:
                    return (n, 65537)  # 标准 RSA exponent

        return None
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════
# LotParser — 从 lot_number 提取动态 key
# ═══════════════════════════════════════════════════════════

class LotParser:
    """LotParser — 支持动态 mapping（botion 每次生成新的）。"""

    def __init__(self, mapping: dict | None = None):
        if mapping is None:
            # botion 从 __BOTION__.ctStore 提取的动态 mapping
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
                start, end = s[0], (s[1] + 1) if len(s) > 1 else (start + 1)
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


_lot_parser = LotParser()


# ═══════════════════════════════════════════════════════════
# 加密函数
# ═══════════════════════════════════════════════════════════

def _rand_uid():
    result = ""
    for _ in range(4):
        result += hex(int(65536 * (1 + random.random())))[2:].zfill(4)[-4:]
    return result


def _encrypt_aes(text: str, key: str) -> bytes:
    cipher = AES.new(key.encode(), AES.MODE_CBC, b"0000000000000000")
    return cipher.encrypt(pad(text.encode(), AES.block_size))


def _encrypt_rsa(message: str) -> str:
    cipher = PKCS1_v1_5.new(RSA_PUBKEY)
    return binascii.hexlify(cipher.encrypt(message.encode())).decode()


def _hash_pow(value: str, hash_func: str) -> str:
    if hash_func not in {"md5", "sha1", "sha256"}:
        raise ValueError(f"unsupported pow hashfunc: {hash_func}")
    return getattr(hashlib, hash_func)(value.encode()).hexdigest()


def _generate_pow(lot_number, captcha_id, hash_func, version, bits, date, nonce=None) -> dict:
    bit_remainder = bits % 4
    bit_division = bits // 4
    prefix = "0" * bit_division
    pow_string = f"{version}|{bits}|{hash_func}|{date}|{captcha_id}|{lot_number}||"

    if nonce is not None:
        combined = pow_string + nonce
        return {"pow_msg": combined, "pow_sign": _hash_pow(combined, hash_func)}

    while True:
        h = _rand_uid()
        combined = pow_string + h
        hashed = _hash_pow(combined, hash_func)

        if bit_remainder == 0:
            if hashed.startswith(prefix):
                return {"pow_msg": pow_string + h, "pow_sign": hashed}
        elif hashed.startswith(prefix) and len(prefix) <= [0, 7, 3, 1][bit_remainder]:
            return {"pow_msg": pow_string + h, "pow_sign": hashed}


def build_e_obj(
    load_data: dict,
    captcha_id: str,
    coords: str,
    *,
    passtime: int | None = None,
    pow_nonce: str | None = None,
) -> dict:
    required = {"hashfunc", "version", "bits", "datetime"}
    pow_detail = load_data.get("pow_detail") or {}
    if not required.issubset(pow_detail):
        raise ValueError("pow_detail is missing required fields")

    try:
        coords_array = [[int(v) for v in point.split(",")] for point in coords.split("|")]
    except (AttributeError, TypeError, ValueError):
        raise ValueError("coords must contain exactly three x,y integer pairs") from None
    if len(coords_array) != 3 or any(len(point) != 2 for point in coords_array):
        raise ValueError("coords must contain exactly three x,y integer pairs")

    lot_number = load_data["lot_number"]
    return {
        **_generate_pow(
            lot_number,
            captcha_id,
            pow_detail["hashfunc"],
            pow_detail["version"],
            pow_detail["bits"],
            pow_detail["datetime"],
            pow_nonce,
        ),
        **_lot_parser.get_dict(lot_number),
        "biht": "1426265548",
        "em": {"cp": 0, "ek": "11"},
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3",
                      "res": "3", "rew": "3", "sep": "3", "snh": "3"},
        "geetest": "captcha",
        "lang": "zh",
        "lot_number": lot_number,
        "userresponse": coords_array,
        "passtime": passtime if passtime is not None else random.randint(1500, 3500),
    }


def serialize_e_obj(e_obj: dict) -> str:
    return json.dumps(e_obj, separators=(",", ":"), ensure_ascii=False)


def generate_w(
    load_data: dict,
    captcha_id: str,
    coords: str,
    *,
    diagnostics: dict | None = None,
) -> str:
    """生成 GeeTest v4 文字点选的 w 参数。

    w = hex(AES-CBC(e_obj, random_key, zero-IV)) + hex(RSA-1024(random_key))
    RSA 使用 1024-bit 密钥，单次加密（与标准 GeeTest/GeekedTest 一致）。

    Args:
        load_data: fetch_captcha 返回的 dict
        captcha_id: GeeTest captcha_id
        coords: jfbym 返回的坐标 "x1,y1|x2,y2|x3,y3"

    Returns:
        w 参数字符串
    """
    e_obj = build_e_obj(load_data, captcha_id, coords)
    plaintext = serialize_e_obj(e_obj)
    if diagnostics is not None:
        diagnostics.update(
            e_obj_fields=sorted(e_obj),
            e_obj_bytes=len(plaintext.encode()),
        )

    random_key = _rand_uid()
    encrypted_input = _encrypt_aes(plaintext, random_key)
    encrypted_key = _encrypt_rsa(random_key)

    return binascii.hexlify(encrypted_input).decode() + encrypted_key
