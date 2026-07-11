import json
import gzip
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from hdata.protocol.decoder import decode_frame, AES_KEY


def _encrypt_payload(data: dict) -> bytes:
    """辅助函数：用 AES_KEY 加密 JSON→GZIP→AES-CBC，模拟服务端帧"""
    payload = json.dumps(data, separators=(",", ":")).encode()
    compressed = gzip.compress(payload)

    # PKCS7 填充
    block_size = 16
    pad_len = block_size - (len(compressed) % block_size)
    padded = compressed + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


class TestDecoder:
    def test_decode_simple_message(self):
        """解码一条简单的 {msgId, data} 消息"""
        original = {"msgId": 303, "data": {"tableId": 2718, "cardResult": []}}
        raw = _encrypt_payload(original)
        result = decode_frame(raw)
        assert result is not None
        assert result["msgId"] == 303
        assert result["data"]["tableId"] == 2718

    def test_decode_round_result(self):
        """解码一条牌局结算消息"""
        original = {
            "msgId": 303,
            "data": {
                "tableId": 2718,
                "roundId": 456354030,
                "cardResult": [
                    {"owner": 0, "result": "4"},
                    {"owner": 0, "result": "5"},
                    {"owner": 1, "result": "2"},
                    {"owner": 1, "result": "0"},
                ],
            },
        }
        raw = _encrypt_payload(original)
        result = decode_frame(raw)
        assert result is not None
        assert result["data"]["roundId"] == 456354030

    def test_decode_invalid_data_returns_none(self):
        """无效数据返回 None"""
        result = decode_frame(b"\x00\x01\x02\x03")
        assert result is None
