"""gateway HTTP 请求：HMAC 加签 + 加密体加解密（_gateway_request）与 JSON 解析叶子。"""

from __future__ import annotations

import time


def _gateway_request(method: str, url: str, payload: dict | None,
                     session: dict, timestamp: int = 0) -> dict:
    """game-http gateway 请求（内部）。

    GET: 响应为明文 JSON；
    POST: 请求体 gateway_encrypt(payload)，另需加签的 token 头，
          响应为 gateway_encrypt 加密体，解密后返回。
    """
    import base64 as _b64
    import hashlib as _hash
    import hmac as _hmac

    from curl_cffi import requests

    from hdata.protocol.codec import GATEWAY_KEY, gateway_decrypt, gateway_encrypt

    keyid = "probinpjms7rfm26"  # release keyid（大厅 bundle 硬编码）
    headers = {
        "deviceType": "15",
        # NOTE: model 硬编码 Chrome/149.0.0.0 与下方 get_impersonate 的
        # 指纹可能不一致——历史原因保留，不改（见 P4 收敛）。
        "model": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/149.0.0.0 Safari/537.36"),
        "deviceId": session.get("device_id", "") or f"{int(time.time()*1000)}-1",
        "X-Request-Token": session.get("game_token", ""),
        "keyid": keyid,
        "Content-Type": "application/json;charset=UTF-8",
    }
    body: bytes | None = None
    if method == "POST" and payload is not None:
        enc = gateway_encrypt(payload)
        sign = _b64.b64encode(_hmac.new(
            GATEWAY_KEY, (enc + "0" + str(timestamp)).encode(),
            _hash.sha1).digest()).decode()
        meta = {"encrypted": True, "gzipped": True, "platform": "h5",
                "version": "1.2.2", "application": "game_http",
                "timestamp": timestamp, "nonce": 0,
                "sign": sign, "keyid": keyid}
        headers["token"] = gateway_encrypt(meta)
        body = enc.encode()

    proxy = session.get("proxy") or ""
    from hdata.auth.fingerprint import get_impersonate
    resp = requests.request(
        method, url, data=body, headers=headers,
        impersonate=get_impersonate(session.get("account", "")), timeout=15,
        proxies={"http": proxy, "https": proxy} if proxy else None)
    resp.raise_for_status()
    text = resp.text
    try:
        return _json_loads(text)
    except Exception:
        return gateway_decrypt(text)


def _json_loads(s: str):
    import json as _j
    return _j.loads(s)
