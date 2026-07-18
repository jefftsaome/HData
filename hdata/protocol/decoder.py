"""AES-CBC → GZIP → JSON 解码链路（纯算法，无 IO）。

兼容旧模块路径；实现已迁移至 hdata.protocol.codec。
"""

from hdata.protocol.codec import AES_KEY, aes_decrypt, decode_frame

__all__ = ["AES_KEY", "aes_decrypt", "decode_frame"]
