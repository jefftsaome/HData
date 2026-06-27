"""分析 vendor bundle 中的 AES 解密密钥"""
import re

with open("/tmp/vendor_bundle.js") as f:
    content = f.read()

print(f"Total size: {len(content)} bytes")

# 找 AES 密钥样式
# 常见的 AES 密钥是 16 字节的 ASCII 或 Base64
patterns = [
    (r'["\']([A-Za-z0-9+/=]{16})["\']', "16字节字符串"),
    (r'["\']([A-Za-z0-9+/=]{24})["\']', "24字节字符串"),
    (r'["\']([A-Za-z0-9+/=]{32})["\']', "32字节字符串"),
    (r'\.parse\s*\(\s*["\']([A-Za-z0-9+/=]{8,64})["\']', "parse密钥"),
]

for pat, desc in patterns:
    matches = list(re.finditer(pat, content))
    print(f"\n{desc}: {len(matches)} 个")
    for m in matches[:10]:
        ctx_start = max(0, m.start() - 40)
        ctx_end = min(len(content), m.end() + 40)
        ctx = content[ctx_start:ctx_end].replace("\n", " ")
        print(f"  {m.group(1)}  ctx: ...{ctx}...")

# 找 decrypt 函数
idx = content.find("decrypt")
if idx >= 0:
    print(f"\ndecrypt 首次出现位置: {idx}")
    print(content[max(0, idx - 80) : idx + 200])

# 找已知密钥
known = "ED7AA06BD8628B55"
if known in content:
    idx = content.find(known)
    print(f"\n已知密钥 {known} 在位置 {idx}")
    print(content[max(0, idx - 60) : idx + 100])
else:
    print(f"\n已知密钥 {known} 未找到")

# 尝试找其他 AES 密钥
for key_hint in ["key", "KEY", "aesKey", "aes_key", "secret"]:
    for m in re.finditer(
        rf'{key_hint}[\s:=]+["\']([A-Za-z0-9+/=]{{8,}})["\']', content
    ):
        val = m.group(1)
        if len(val) in (16, 24, 32):
            ctx_start = max(0, m.start() - 30)
            ctx_end = min(len(content), m.end() + 30)
            print(f"\n  密钥 {key_hint}={val}")
            print(f"  上下文: {content[ctx_start:ctx_end]}")
