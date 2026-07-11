"""AES-CBC → GZIP → JSON 解码链路（纯算法，无 IO）"""

import gzip
import json
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


AES_KEY = b"ED7AA06BD8628B55"  # 16 字节 ASCII


def decode_frame(raw: bytes) -> dict | None:
    """解码原始 WS 帧数据：AES-CBC 解密 → GZIP 解压 → JSON 解析。

    Args:
        raw: 原始二进制帧（已剥离协议头的 payload）

    Returns:
        解码后的 JSON dict，解码失败返回 None
    """
    try:
        # AES-CBC 解密（IV = KEY）
        cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY))
        decryptor = cipher.decryptor()
        padded = decryptor.update(raw) + decryptor.finalize()

        # 去除 PKCS7 填充
        pad_len = padded[-1]
        data = padded[:-pad_len]

        # GZIP 解压
        decompressed = gzip.decompress(data)

        # JSON 解析
        return json.loads(decompressed.decode("utf-8"))
    except Exception:
        return None
