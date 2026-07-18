"""Leyu WS 协议编解码（纯算法，无 IO）。

协议格式（来自游戏前端 egret release js 静态分析，2026-07-17 实测验证）：

发送方向（p3.send → DataHandle.encryptWsData）：
    msg = {
        "jsonData": jsonData,      # str: JSON.stringify({"id": protocolId, "param": "<json str>"})
        "nonce": nonce,            # int: Math.round(Math.random() * 2**31)
        "protocolId": protocolId,  # int
        "gameTypeId": gameTypeId,  # int
        "sign": sign,              # Base64(HmacSHA1(jsonData + nonce + timestamp, KEY))
        "timestamp": timestamp,    # int: ms
        "playerId": playerId,
        "tableId": tableId,
        "serviceTypeId": serviceTypeId,
    }
    wire = AES-128-CBC(gzip(JSON.stringify(msg)), key=KEY, iv=KEY, PKCS7) → raw bytes

接收方向（DataHandle.decryptWsData）：
    raw bytes → AES-128-CBC 解密(key=iv=KEY) → gunzip → JSON

密钥：DataHandle._defaultKey = "ED7AA06BD8628B55"（16 字节 ASCII，iv 与 key 相同）
"""

import base64
import gzip
import hashlib
import hmac
import json
import random
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# DataHandle._defaultKey（游戏前端硬编码）
AES_KEY = b"ED7AA06BD8628B55"

# ── 协议常量（来自游戏前端枚举） ──
FS_LOGIN = 10000        # Fs.Login — 登录请求/响应
FS_LOGIN_FAIL = 10026   # 登录失败踢出（kickType）
OT_HALL = 7             # Ot.HALL — serviceTypeId 大厅
OT_GAME = 3             # Ot.GAME — serviceTypeId 游戏
DEVICE_TYPE_PC = 15     # _t.EGRET2_PC — PC 网页端设备类型


# ── AES ──────────────────────────────────────────────


def aes_encrypt(data: bytes) -> bytes:
    """AES-128-CBC 加密（key=iv=AES_KEY，PKCS7 填充）。"""
    pad = 16 - len(data) % 16
    padded = data + bytes([pad]) * pad
    c = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY))
    enc = c.encryptor()
    return enc.update(padded) + enc.finalize()


def aes_decrypt(data: bytes) -> bytes:
    """AES-128-CBC 解密并去 PKCS7 填充。"""
    c = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY))
    dec = c.decryptor()
    padded = dec.update(data) + dec.finalize()
    return padded[:-padded[-1]]


# ── gateway HTTP 载荷（game-http/* 接口） ─────────────
# 算法同 WS 帧：base64(AES-128-CBC(gzip(JSON), key=iv))，但密钥独立。
# 来源：大厅 iframe 内联 dataHandle bundle（见 docs §12.8）。

GATEWAY_KEY = b"015CCB80A680E129"      # release 环境
GATEWAY_KEY_DEV = b"AA4194657AD89A56"  # dev/training 环境


def gateway_encrypt(payload: dict, key: bytes = GATEWAY_KEY) -> str:
    """gateway HTTP 请求载荷加密：dict → base64(AES-CBC(gzip(JSON)))。"""
    import base64 as _b64
    plaintext = gzip.compress(
        json.dumps(payload, separators=(",", ":")).encode())
    pad = 16 - len(plaintext) % 16
    padded = plaintext + bytes([pad]) * pad
    c = Cipher(algorithms.AES(key), modes.CBC(key))
    enc = c.encryptor()
    return _b64.b64encode(enc.update(padded) + enc.finalize()).decode()


def gateway_decrypt(b64: str, key: bytes = GATEWAY_KEY) -> dict:
    """gateway HTTP 载荷解密：base64 → AES-CBC → gunzip → dict。"""
    import base64 as _b64
    ct = _b64.b64decode(b64 + "=" * ((4 - len(b64) % 4) % 4))
    c = Cipher(algorithms.AES(key), modes.CBC(key))
    dec = c.decryptor()
    padded = dec.update(ct) + dec.finalize()
    return json.loads(gzip.decompress(padded[:-padded[-1]]).decode())


# ── 帧编解码 ──────────────────────────────────────────


def encode_frame(msg: dict) -> bytes:
    """消息 dict → wire bytes：gzip(JSON) → AES-CBC。"""
    plaintext = gzip.compress(json.dumps(msg, separators=(",", ":")).encode())
    return aes_encrypt(plaintext)


def decode_frame(raw: bytes) -> dict | None:
    """wire bytes → 消息 dict：AES-CBC 解密 → gunzip → JSON。失败返回 None。"""
    try:
        data = aes_decrypt(raw)
        return json.loads(gzip.decompress(data).decode("utf-8"))
    except Exception:
        return None


# ── 消息构造 ──────────────────────────────────────────


def build_message(protocol_id: int, data: dict, *,
                  player_id: int, game_type_id: int = 2013,
                  table_id: int = 0, service_type_id: int = OT_HALL) -> dict:
    """构造一个完整的待发消息（含签名）。

    与浏览器端 X9.getRequestDataVO + p3.send 一致：
      jsonData = JSON.stringify({"id": protocolId, "param": JSON.stringify(data)})
      sign = Base64(HmacSHA1(jsonData + nonce + timestamp, AES_KEY))
    """
    inner = {"id": protocol_id, "param": json.dumps(data, separators=(",", ":"))}
    json_data = json.dumps(inner, separators=(",", ":"))
    nonce = random.randint(0, 2**31)
    timestamp = int(time.time() * 1000)
    sign = base64.b64encode(
        hmac.new(AES_KEY, f"{json_data}{nonce}{timestamp}".encode(),
                 hashlib.sha1).digest()
    ).decode()
    return {
        "jsonData": json_data,
        "nonce": nonce,
        "protocolId": protocol_id,
        "gameTypeId": game_type_id,
        "sign": sign,
        "timestamp": timestamp,
        "playerId": player_id,
        "tableId": table_id,
        "serviceTypeId": service_type_id,
    }


def build_login_msg(token: str, player_id: int, device_id: str,
                    game_type_id: int = 2013) -> dict:
    """构造登录消息（Fs.Login=10000）。

    与浏览器 _sendLogin 一致：
      data = {jwtToken, deviceType: 15, deviceId, timeZoneArea, offsetMinutes,
              protocolCodecConfig: {}, version: "1.1.1"}
      getRequestDataVO(Fs.Login, data, 2013, 0, playerId, Ot.HALL)
    """
    offset = -time.timezone // 60 if time.daylight == 0 else -time.altzone // 60
    data = {
        "jwtToken": token,
        "deviceType": DEVICE_TYPE_PC,
        "deviceId": device_id,
        "timeZoneArea": "Asia/Shanghai",
        "offsetMinutes": offset,
        "protocolCodecConfig": {},
        "version": "1.1.1",
    }
    return build_message(FS_LOGIN, data,
                         player_id=player_id, game_type_id=game_type_id,
                         table_id=0, service_type_id=OT_HALL)


def extract_param(frame: dict) -> dict | None:
    """从解码后的帧中提取业务参数（jsonData → param 两层 JSON 解包）。"""
    jd = frame.get("jsonData")
    if isinstance(jd, str):
        try:
            jd = json.loads(jd)
        except Exception:
            return None
    if not isinstance(jd, dict):
        return None
    param = jd.get("param")
    if isinstance(param, str):
        try:
            param = json.loads(param)
        except Exception:
            pass
    return {"id": jd.get("id"), "param": param,
            "status": jd.get("status"), "msg": jd.get("msg"),
            "data": jd.get("data")}
