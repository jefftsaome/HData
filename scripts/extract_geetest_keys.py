#!/usr/bin/env python3
"""从 GeeTest JS 中提取 RSA 公钥和动态参数。

用法: uv run python scripts/extract_geetest_keys.py
"""

import re, json, subprocess, sys
from pathlib import Path
from urllib.parse import unquote

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def decode_xor_string(encoded, key):
    """解码 GeeTest 的 XOR 字符串 (key 与 decodeURI 后的字符串逐字 XOR)。"""
    decoded = unquote(encoded)
    result = ""
    for i, ch in enumerate(decoded):
        result += chr(ord(ch) ^ ord(key[i % len(key)]))
    return result


def extract_strings(js_text):
    """从 JS 中提取所有 decodeURI 编码的字符串并尝试解码。"""
    # 找 decodeURI('...')
    pattern = re.compile(r"decodeURI\('([^']+)'\)")
    matches = pattern.findall(js_text)

    results = []
    for encoded in matches:
        try:
            # 尝试用常见 key 解码
            for key in ["je4_click", "MIrmw", "6Kjyv", "WLpnx", "bbMIR", "Ranfh"]:
                try:
                    decoded = decode_xor_string(encoded, key)
                    if decoded and len(decoded) > 3 and all(32 <= ord(c) < 127 for c in decoded):
                        results.append({"key": key, "decoded": decoded[:120]})
                        break
                except Exception:
                    continue
        except Exception:
            pass
    return results


def main():
    # 1. 提取 gct4.js 的字符串
    gct4 = (PROJ / "data/gct4.js").read_text()
    print("=== gct4.js 解码字符串 ===")
    results = extract_strings(gct4)
    for r in results:
        print(f"  key={r['key']}: {r['decoded']}")

    # 2. 提取 bcaptcha.js 的字符串
    bc = (PROJ / "data/bcaptcha.js").read_text()
    print(f"\n=== bcaptcha.js ({len(bc)} bytes) ===")
    results = extract_strings(bc)
    for r in results:
        print(f"  key={r['key']}: {r['decoded']}")

    # 3. 用 Node.js 执行 GeeTest JS 提取公钥（如果可用）
    # 或者手动搜索 RSA 相关模式
    print(f"\n=== 搜索 RSA 密钥 ===")
    for pattern in [
        r'"([A-Za-z0-9+/=]{300,600})"',  # 长 base64 (n)
        r'0x10001',  # 常见 RSA 指数
    ]:
        for m in re.finditer(pattern, bc):
            print(f"  找到: {m.group()[:120]}")


if __name__ == "__main__":
    main()
