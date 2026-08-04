"""uuidToBase64 签名表 AES-CBC 解密（唯一实现，叶子模块）。

原实现在 token_manager.py 的 TokenManager._decrypt_sign_table，
Task 4 抽出为独立模块以便 session.py / headers.py 无循环引用地复用。
"""
from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_KEY = b"ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0"
AES_IV = b"CbE3P3t1lY34Ns8F"


def decrypt_sign_table(uuid_b64: str) -> dict[str, str]:
    """AES-CBC 解密签名表。

    Args:
        uuid_b64: 缓存的 uuidToBase64 字段（base64 编码的密文）

    Returns:
        解密后的签名表 dict（key=API 路径前缀, value=签名）

    Raises:
        IndexError / json.JSONDecodeError 等：输入非法时按原语义上抛，
        由调用方（resolve_api_xxx）的 try/except 兜底。
    """
    ct = base64.b64decode(uuid_b64)
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    return json.loads(padded[: -padded[-1]])
