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


# 协议 schema 内容哈希（protocolCodecConfig），2026-07-24 浏览器登录帧抓包实录，
# 与 .cache/h3_schemas.json 各协议 version 字段逐字一致。
# 键为 {protocolId}_{schema版本}；值为静态值，仅当平台升级协议 schema 时才变，
# 届时需重新抓包更新（见 docs/数据样本.md"登录请求帧"一节）。
PROTOCOL_CODEC_CONFIG = {
    "10053_7": "26695a937138721cdec2878bf9ca16ada04535f16cd1d83d115c95548c558a38",
    "10089_7": "da3d29a428cf043dbd86724edac7f25c2e9c185ca986812c01003aaf2fce8548",
    "10073_7": "a5e098a48a2f406aaeac90ff7c7ff5f1832b098507b0e60c7cb0a014c9f5c127",
    "10075_7": "9c69c9b2566b7700b2aa699aa7662dbbca482eab73c3a15be047ed8e6df1e323",
    "301_2": "0ea525bf9283b3d65a008cbb340a093d994d7c2862fdf34bebbbadfc92bcc075",
    "302_2": "5de34be7725f7feca1bcdb09876abcaa804bc9d414837c9b7c040e9c30899927",
}


def build_login_msg(token: str, player_id: int, device_id: str,
                    game_type_id: int = 2013) -> dict:
    """构造登录消息（Fs.Login=10000）。

    与浏览器 _sendLogin 一致：
      data = {jwtToken, deviceType: 15, deviceId, timeZoneArea, offsetMinutes,
              protocolCodecConfig: PROTOCOL_CODEC_CONFIG, version: "1.1.1"}
      getRequestDataVO(Fs.Login, data, 2013, 0, playerId, Ot.HALL)

    protocolCodecConfig 历史版本发空表 {}，实测与真实客户端不一致；
    2026-07-24 起补齐 6 个 schema 哈希（静态常量，见 PROTOCOL_CODEC_CONFIG）。
    """
    offset = -time.timezone // 60 if time.daylight == 0 else -time.altzone // 60
    data = {
        "jwtToken": token,
        "deviceType": DEVICE_TYPE_PC,
        "deviceId": device_id,
        "timeZoneArea": "Asia/Shanghai",
        "offsetMinutes": offset,
        "protocolCodecConfig": dict(PROTOCOL_CODEC_CONFIG),
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
