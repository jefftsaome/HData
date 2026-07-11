#!/usr/bin/env python3
"""纯 HTTP 方式获取乐鱼游戏 JWT — 使用从 localStorage 解密的签名表。

X-API-XXX 签名是预计算的路径前缀表，不依赖浏览器：
  /site/api → ada7f4c6...  /game/api → 3a0a026c...
  /act/api  → 9a71ca45...  /fd/api   → 99371349...

用法:
    uv run python scripts/test_curl_login.py
"""

import asyncio
import base64
import json
import sys
import re
import time
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ_ROOT))

AUTH_CACHE = _PROJ_ROOT / ".auth_cache.json"

# ── AES 解密 uuidToBase64 ─────────────────────────────────


def decrypt_sign_table(encrypted_b64: str) -> dict[str, str]:
    """AES-CBC 解密 localStorage.uuidToBase64 → 路径→签名 映射表。

    key = "ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0"
    iv  = "CbE3P3t1lY34Ns8F"
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    ct = base64.b64decode(encrypted_b64)
    key = b"ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0"
    iv = b"CbE3P3t1lY34Ns8F"

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    plaintext = padded[: -padded[-1]].decode("utf-8")  # PKCS7 unpad
    return json.loads(plaintext)


def get_sign_for_url(sign_table: dict[str, str], url: str) -> str:
    """根据 URL 路径前缀查表返回 X-API-XXX 签名。"""
    m = re.match(r"/\w+/\w+", url.replace("https://", "").split("/", 1)[1]
                 if "/" in url.replace("https://", "").split("/", 1)[-1]
                 else "")
    # 更简单的方法：直接在 url 中搜索匹配的路径前缀
    for prefix in sorted(sign_table.keys(), key=len, reverse=True):
        if prefix in url:
            return sign_table[prefix]
    return ""


def save_auth_cache(decrypted: dict):
    """保存 game JWT 到 .auth_cache.json。"""
    token = decrypted.get("token", "")
    player_id = decrypted.get("playerId", 0)
    backend = decrypted.get("backendDomainUrl", "")
    if not backend:
        backend = decrypted.get("backendDomainUrlList", "").split(",")[0].strip()

    host = backend.split(":")[0] if ":" in backend else backend
    ws_url = (
        f"wss://wsproxy.{host}:18026/"
        f"?playerId={player_id}"
        f"&jwtToken={token}"
        f"&deviceType=2&platform=6"
    )
    data = {
        "token": token, "player_id": player_id,
        "backend_domain": backend, "device_id": "", "ws_url": ws_url,
    }
    AUTH_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"💾 已保存: {AUTH_CACHE}")
    return data


# ── 从 CDP 提取会话数据 ──────────────────────────────────


async def extract_session(port: int = 9222) -> dict:
    """从 Chrome CDP 提取 X-API-TOKEN, X-API-UUID, uuidToBase64。"""
    import aiohttp, websockets

    async with aiohttp.ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{port}/json/version")
        ws_url = (await r.json()).get("webSocketDebuggerUrl", "")

    if not ws_url:
        raise RuntimeError(f"端口 {port} 不可连")

    async with websockets.connect(ws_url) as ws:
        msg_id = 0
        extra: dict = {}

        async def cmd(method, params=None, sid=None):
            nonlocal msg_id
            msg_id += 1
            m = {"id": msg_id, "method": method, "params": params or {}}
            if sid:
                m["sessionId"] = sid
            await ws.send(json.dumps(m))
            return msg_id

        async def read_resp(mid, timeout=10):
            dl = time.time() + timeout
            while time.time() < dl:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                if msg.get("id") == mid:
                    return msg
                if msg.get("method") == "Target.attachedToTarget":
                    extra["session_id"] = msg["params"]["sessionId"]
            return {}

        # Find target
        mid = await cmd("Target.getTargets")
        resp = await read_resp(mid)
        targets = resp.get("result", {}).get("targetInfos", [])
        st = None
        for t in targets:
            if t.get("type") in ("page", "iframe") and "94d9qm.vip" in t.get("url", ""):
                st = t
                break
        if not st:
            raise RuntimeError("未找到主站页面")

        # Attach
        mid = await cmd("Target.attachToTarget",
                        {"targetId": st["targetId"], "flatten": True})
        resp = await read_resp(mid)
        sid = resp.get("result", {}).get("sessionId") or extra.get("session_id", "")

        # Extract
        mid = await cmd("Runtime.evaluate", {
            "expression": (
                "JSON.stringify({"
                "uuidToBase64: localStorage.getItem('uuidToBase64') || '',"
                "token: localStorage.getItem('X-API-TOKEN') || '',"
                "uuid: localStorage.getItem('_uuid') || ''"
                "})"
            ),
            "returnByValue": True,
        }, sid=sid)
        resp = await read_resp(mid)
        val = resp.get("result", {}).get("result", {}).get("value", "{}")
        ls = json.loads(val) if isinstance(val, str) else val

        # Also get cookies
        await cmd("Network.enable", sid=sid)
        await asyncio.sleep(0.3)
        mid = await cmd("Runtime.evaluate", {
            "expression": "document.cookie",
            "returnByValue": True,
        }, sid=sid)
        resp = await read_resp(mid)
        cookie_str = resp.get("result", {}).get("result", {}).get("value", "")

        return {
            "token": ls.get("token", ""),
            "uuid": ls.get("uuid", ""),
            "uuidToBase64": ls.get("uuidToBase64", ""),
            "cookies": cookie_str,
        }


# ── HTTP API 调用 ────────────────────────────────────────


def test_pure_http(session: dict):
    """用纯 HTTP + 签名表调用乐鱼 API。"""
    from curl_cffi import requests

    token = session["token"]
    uuid = session["uuid"]
    uuid_to_b64 = session["uuidToBase64"]
    cookies = session["cookies"]

    # 解密签名表
    if not uuid_to_b64:
        print("❌ uuidToBase64 为空")
        return None

    sign_table = decrypt_sign_table(uuid_to_b64)
    print(f"🔓 签名表解密: {len(sign_table)} 条")
    for k, v in sign_table.items():
        print(f"   {k} → {v[:20]}...")

    # 构造请求 headers
    def make_headers(url_path: str) -> dict:
        sign = get_sign_for_url(sign_table, url_path)
        return {
            "X-API-TOKEN": token,
            "X-API-UUID": uuid,
            "X-API-XXX": sign,
            "X-API-CLIENT": "web",
            "X-API-SITE": "2001",
            "X-API-VERSION": "2.0.0",
            "Content-Type": "application/json",
            "Referer": "https://www.94d9qm.vip:9023/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Cookie": cookies,
        }

    domain = "https://www.94d9qm.vip:9023"

    # Step 1: 测试 JWT 端点
    print(f"\n{'='*60}")
    print("Step 1: 调用 JWT 端点验证签名")
    print(f"{'='*60}")
    url = f"{domain}/site/api/v1/user/member/jwt"
    headers = make_headers(url)
    print(f"   X-API-XXX: {headers['X-API-XXX'][:20]}...")
    resp = requests.post(url, headers=headers, json={},
                         impersonate="chrome110", timeout=15)
    print(f"   状态: {resp.status_code}")
    print(f"   响应: {resp.text[:300]}")

    if resp.status_code != 200 or "非法请求" in resp.text:
        print("   ❌ 签名验证失败！可能需要刷新 session")
        return None

    print("   ✅ 签名验证成功！")

    # 从响应中提取 site JWT
    site_jwt = ""
    try:
        data = resp.json()
        site_jwt = data.get("data", "")
    except Exception:
        pass

    # Step 2: 调用 venue/launch 获取游戏 params URL
    print(f"\n{'='*60}")
    print("Step 2: 调用 venue/launch 获取游戏 URL")
    print(f"{'='*60}")
    url = f"{domain}/game/api/v1/venue/launch"
    headers = make_headers(url)
    print(f"   X-API-XXX: {headers['X-API-XXX'][:20]}...")

    for body in [{"venueCode": "YBZR", "gameTypeId": 2001},
                 {"gameTypeId": 2001, "tableId": 0}, {}]:
        resp = requests.post(url, headers=headers, json=body,
                             impersonate="chrome110", timeout=15)
        print(f"   body={json.dumps(body)} → [{resp.status_code}] {resp.text[:200]}")
        if "params=" in resp.text or "lisxdc" in resp.text:
            print(f"   ✅ 成功获取游戏 URL！")
            # 解析 params URL
            try:
                resp_data = resp.json()
                for key in ("url", "gameUrl", "launchUrl", "data"):
                    val = resp_data.get(key, "")
                    if isinstance(val, str) and "params=" in val:
                        return val  # 返回 params URL
                    elif isinstance(val, dict):
                        for v2 in val.values():
                            if isinstance(v2, str) and "params=" in v2:
                                return v2
            except Exception:
                pass
            return resp.text

        if resp.status_code != 200 or "非法请求" in resp.text:
            print("   ❌ 请求被拒")
            break

    return None


# ── 主入口 ────────────────────────────────────────────────


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9222)
    args = parser.parse_args()

    # 1. 从 CDP 提取 session
    print("📡 从 Chrome 提取 session...")
    session = await extract_session(args.port)
    print(f"   token: {session['token'][:40]}...")
    print(f"   uuid:  {session['uuid']}")
    print(f"   uuidToBase64: {len(session['uuidToBase64'])} chars")

    # 2. 测试纯 HTTP
    result = test_pure_http(session)

    if result:
        print(f"\n{'='*60}")
        print("🎉 纯 HTTP 方案完全可行！")
        print(f"{'='*60}")
        # 如果有 params URL，解密它
        if "params=" in result:
            from urllib.parse import urlparse
            parsed = urlparse(result)
            qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
            params_val = qs.get("params", "")
            ttl_val = qs.get("ttl", "")
            if params_val and ttl_val:
                from scripts.observe_login import decrypt_params as dp
                decrypted = dp(params_val, ttl_val)
                save_auth_cache(decrypted)
                print(f"\n✅ 游戏 JWT 已保存到 {AUTH_CACHE}")
        return 0
    else:
        print(f"\n{'='*60}")
        print("❌ 纯 HTTP 仍有障碍")
        print(f"{'='*60}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
