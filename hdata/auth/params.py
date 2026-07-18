"""乐鱼平台参数解密/解析/校验工具。

本模块只做数据变换，不做网络请求。职责：
  - AES-ECB 解密 params（venue/launch API 返回的加密参数）
  - JWT 解码与校验
  - 缓存数据结构构建
  - 提取 params/ttl 从 URL

用法:
    from hdata.auth.params import decrypt_params, decode_jwt

    decrypted = decrypt_params(params_b64, ttl)
    jwt_info = decode_jwt(token)
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import unquote, urlparse


# ── params 解密 ──────────────────────────────────────


def decrypt_params(params_b64: str, ttl: str) -> dict:
    """解密游戏 params 参数。

    算法: AES-128-ECB + PKCS7 padding
    密钥: ttl + "AES" (ASCII)
    输出: JSON dict

    Args:
        params_b64: Base64 编码的加密参数（可能被 URL 编码污染）
        ttl: 时间戳字符串，用作 AES 密钥种子

    Returns:
        解密后的 JSON dict

    Raises:
        ValueError: Base64 解码或 AES 解密失败
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = (ttl + "AES").encode("ascii")

    # querystring 解析时常见问题：
    # 1) '+' 被转换为空格
    # 2) params 可能被二次 URL 编码
    # 3) base64/urlsafe-base64 混用
    raw = (params_b64 or "").strip()
    candidates = [raw, raw.replace(" ", "+"), unquote(raw), unquote(raw).replace(" ", "+")]

    ct = None
    for c in candidates:
        if not c:
            continue
        padded = c + "=" * ((4 - len(c) % 4) % 4)
        try:
            ct = base64.b64decode(padded)
            break
        except Exception:
            pass
        try:
            ct = base64.urlsafe_b64decode(padded)
            break
        except Exception:
            pass

    if ct is None:
        raise ValueError("params base64 解码失败")

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    raw_decrypted = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    plain = raw_decrypted[: -raw_decrypted[-1]]  # PKCS7 unpad
    return json.loads(plain.decode("utf-8"))


# ── JWT 工具 ─────────────────────────────────────────


def decode_jwt(token: str) -> dict | None:
    """解码 JWT payload（不验证签名）。

    Args:
        token: JWT 字符串（三部分，以 '.' 分隔）

    Returns:
        解码后的 payload dict；如果 sub 字段是 JSON 字符串则自动解析为 dict。
        解码失败返回 None。
    """
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if isinstance(payload.get("sub"), str):
            try:
                payload["sub"] = json.loads(payload["sub"])
            except Exception:
                pass
        return payload
    except Exception:
        return None


def validate_game_token(token: str) -> bool:
    """检查 game JWT 是否还有 >1h 有效期。

    Args:
        token: game JWT 字符串

    Returns:
        True 如果 token 有效且剩余 >1h，否则 False
    """
    if not token:
        return False
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return False
        pb = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pb))
        return payload.get("exp", 0) - time.time() > 3600
    except Exception:
        return False


def token_remaining_hours(token: str) -> float:
    """返回 game JWT 剩余有效时间（小时）。

    Args:
        token: game JWT 字符串

    Returns:
        剩余小时数（>= 0）
    """
    if not token:
        return 0.0
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        pb = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pb))
        return max(0.0, (payload.get("exp", 0) - time.time()) / 3600)
    except Exception:
        return 0.0


# ── URL 参数提取 ─────────────────────────────────────


def extract_params_from_url(url: str) -> tuple[str, str]:
    """从游戏 URL 提取 params 和 ttl。

    Args:
        url: 包含 params 和 ttl query 参数的 URL

    Returns:
        (params_b64, ttl) 元组；如果缺少任一参数则返回 ("", "")
    """
    if not url:
        return "", ""
    parsed = urlparse(url)
    params = ""
    ttl = ""
    for part in parsed.query.split("&"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k == "params":
            params = unquote(v)
        elif k == "ttl":
            ttl = unquote(v)
    return params, ttl


# ── 缓存数据结构 ─────────────────────────────────────


def build_auth_snapshot(
    token: str,
    player_id: int,
    backend: str,
    source: str = "params",
) -> dict:
    """构造 WS-only 认证快照（缓存数据）。

    Args:
        token: game JWT
        player_id: 游戏玩家 ID
        backend: 游戏后端地址（如 "txdzbjc.com:18034"）
        source: 来源标记，用于追踪数据来源

    Returns:
        可用于写入缓存的 dict
    """
    snapshot: dict = {
        "game_token": token,
        "game_player_id": player_id,
        "game_backend": backend,
        "source": source,
        "updated_at": int(time.time()),
    }
    jwt = decode_jwt(token)
    if jwt:
        snapshot["game_exp"] = jwt.get("exp", 0)
    return snapshot


def save_auth_cache(data: dict, cache_path: Path) -> dict:
    """将认证快照写入缓存文件。

    Args:
        data: 认证数据 dict（通常来自 build_auth_snapshot()），
              字段名已标准化：game_token, game_player_id, game_backend
        cache_path: 缓存文件路径

    Returns:
        写入的 dict
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data
