#!/usr/bin/env python3
"""观察并捕获乐鱼登录全流程，提取 JWT token。

═══════════════════════════════════════════════════════════════════════
工作流程:
  1. 启动 Chrome（headless=new，可看到窗口）
  2. 导航到 leyu.com（会被 302 重定向到分配的子域名）
  3. 开启 Network + Target 监控，捕获所有 HTTP 请求和页面跳转
  4. 用户在浏览器中手动完成登录
  5. 自动检测跳转到游戏 iframe（pc.lisxdc.com:2083/egret/）
  6. 从 URL 提取 params+ttl → 解密得到 JWT
  7. 从 localStorage 二次提取 token 作为验证
  8. 保存到 .auth_cache.json 和 login_flow_analysis.json

用法:
    cd hdt
    uv run python scripts/observe_login.py

    # 连接已有 Chrome（免去每次启动）
    uv run python scripts/observe_login.py --attach http://127.0.0.1:9222

    # 指定入口 URL（默认 leyu.com，会被重定向到分配的子域名）
    uv run python scripts/observe_login.py --url "https://leyu.me"

输出:
    .auth_cache.json          — token/playerId/backendDomain/deviceId/ws_url
    data/login_flow_analysis.json  — 完整流程分析（含 HTTP 请求记录）
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# ── 项目路径 ──────────────────────────────────────────────
_PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ_ROOT))

# ── 常量 ──────────────────────────────────────────────────
# 乐鱼入口域名（会被 302 重定向到分配的子域名）
DEFAULT_ENTRY_URL = "https://leyu.com"
ALT_ENTRY_URL = "https://leyu.me"
CDP_PORT = 9222
AUTH_CACHE = _PROJ_ROOT / ".auth_cache.json"
ANALYSIS_OUTPUT = _PROJ_ROOT / "data" / "login_flow_analysis.json"

# ── params 解密（复用 decrypt_params.py 算法）─────────────


def decrypt_params(params_b64: str, ttl: str) -> dict:
    """解密乐鱼 params 参数获取认证数据。

    Key = ttl + "AES" (如 "1782535308601AES")，AES-ECB，PKCS7 padding。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = (ttl + "AES").encode("ascii")
    ct = base64.b64decode(params_b64)
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    # PKCS7 unpad
    data = padded[: -padded[-1]]
    return json.loads(data)


def decode_jwt(token: str) -> dict | None:
    """解码 JWT payload（不验证签名）。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if isinstance(payload.get("sub"), str):
            payload["sub"] = json.loads(payload["sub"])
        return payload
    except Exception:
        return None


# ── CDP 监控器 ────────────────────────────────────────────


class LoginObserver:
    """通过 CDP 监控浏览器登录流程，捕获 token。"""

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url
        self._ws: object = None  # websockets 连接
        self._msg_id = 0
        self._sessions: dict[str, str] = {}  # targetId → sessionId

        # 监控数据
        self._events: list[dict] = []
        self._http_requests: list[dict] = []
        self._http_responses: list[dict] = []
        self._game_iframe_url: str = ""
        self._params: str = ""
        self._ttl: str = ""
        self._extracted_token: str = ""
        self._local_storage: dict = {}

        # JWT API 拦截
        self._jwt_request_id: str = ""
        self._jwt_session_id: str = ""
        # venue/launch 拦截
        self._venue_launch_request_id: str = ""
        self._venue_launch_session_id: str = ""
        # POST body 收集
        self._post_bodies: dict[str, str] = {}  # requestId → body

        # 控制
        self._stop_event = asyncio.Event()

    async def _cmd(self, method: str, params: dict | None = None,
                   session_id: str | None = None) -> int:
        """发送 CDP 命令，返回 msg id。"""
        self._msg_id += 1
        msg: dict = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        await self._ws.send(json.dumps(msg))
        return self._msg_id

    async def _reader_loop(self):
        """持续读取 CDP 事件直到停止。"""
        buf = ""
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            method = msg.get("method", "")
            params = msg.get("params", {})
            sid = msg.get("sessionId", "")

            # ── Target 创建 → 自动 attach + 启用 Network ──
            if method == "Target.targetCreated":
                ti = params.get("targetInfo", {})
                url = ti.get("url", "")
                ttype = ti.get("type", "")
                tid = ti.get("targetId", "")
                self._events.append({
                    "t": time.time(), "type": "target_created",
                    "url": url[:300], "target_type": ttype,
                })
                print(f"  📄 [{ttype}] {url[:120]}")
                # 对 page 和 iframe 类型自动 attach
                if ttype in ("page", "iframe") and url and url != "about:blank":
                    await self._cmd("Target.attachToTarget",
                                    {"targetId": tid, "flatten": True})

            elif method == "Target.attachedToTarget":
                new_sid = params.get("sessionId", "")
                tid = params.get("targetInfo", {}).get("targetId", "")
                if new_sid and tid:
                    self._sessions[tid] = new_sid
                    # 立即启用 Network 监控
                    await self._cmd("Network.enable", session_id=new_sid)
                    # 也启用 Page 域以捕获 frameNavigated
                    await self._cmd("Page.enable", session_id=new_sid)
                    print(f"  🔗 attached + Network enabled: {tid[:16]}...")

            # ── 页面导航 ──
            elif method == "Page.frameNavigated":
                frame = params.get("frame", {})
                url = frame.get("url", "")
                if "lisxdc.com" in url and "params=" in url:
                    self._game_iframe_url = url
                    print(f"\n  🎯 检测到游戏 iframe: {url[:150]}...")
                    # 提取 params 和 ttl
                    parsed = urlparse(url)
                    qs = dict(
                        p.split("=", 1)
                        for p in parsed.query.split("&")
                        if "=" in p
                    )
                    self._params = qs.get("params", "")
                    self._ttl = qs.get("ttl", "")
                    self._stop_event.set()  # 完成！

            # ── HTTP 请求/响应 ──
            elif method == "Network.requestWillBeSent":
                req = params.get("request", {})
                url = req.get("url", "")
                req_id = params.get("requestId", "")
                if "127.0.0.1:9222" not in url:
                    post_data = params.get("postData", "")
                    self._http_requests.append({
                        "t": time.time(),
                        "url": url,
                        "method": req.get("method", ""),
                        "request_id": req_id,
                        "headers": _safe_headers(req.get("headers", {})),
                        "post_data": _safe_post(post_data),
                    })
                    # 如果 postData 为空但方法是 POST，尝试获取 POST body
                    if req.get("method") == "POST" and not post_data:
                        asyncio.create_task(self._fetch_post_data(req_id, url, sid))

            elif method == "Network.responseReceived":
                resp = params.get("response", {})
                url = resp.get("url", "")
                req_id = params.get("requestId", "")
                status = resp.get("status", 0)
                if "127.0.0.1:9222" not in url:
                    self._http_responses.append({
                        "t": time.time(),
                        "url": url,
                        "request_id": req_id,
                        "status": status,
                        "mime_type": resp.get("mimeType", ""),
                    })
                    # ── 重点：venue/launch 响应体 ──
                    if "venue/launch" in url:
                        print(f"  🚀 venue/launch [{status}] — 获取响应体...")
                        self._venue_launch_request_id = req_id
                        self._venue_launch_session_id = sid
                    # ── 重点关注 JWT token 端点 ──
                    elif "/jwt" in url or "/token" in url:
                        print(f"  🎫 JWT 端点 [{status}] {url[:120]}")
                        self._jwt_request_id = req_id
                        self._jwt_session_id = sid
                    elif any(kw in url for kw in ("login", "auth", "signin",
                                                   "user/member")):
                        print(f"  🔑 响应 [{status}] {url[:120]}")
                    # 也关注包含 token 的 302 重定向
                    if status in (301, 302, 303, 307, 308):
                        loc = resp.get("headers", {}).get("location", "")
                        if "params=" in loc:
                            print(f"  🎯 重定向到游戏: {loc[:150]}...")

    async def run(self, entry_url: str, timeout: int = 300) -> dict:
        """执行监控循环。

        Args:
            entry_url: 乐鱼入口 URL（如 leyu.com，会被重定向到子域名）
            timeout: 最长等待时间（秒）
        """
        import websockets

        self._entry_url = entry_url

        ws_url = self._cdp_url
        print(f"🔌 连接 CDP: {ws_url}")
        self._ws = await websockets.connect(ws_url, max_size=10 * 10**6)

        # 启动事件读取循环（后台）
        reader_task = asyncio.create_task(self._reader_loop())

        # 启用 Target 发现
        await self._cmd("Target.setDiscoverTargets", {"discover": True})

        # 导航到入口 URL（leyu.com → 302 重定向到分配的子域名）
        print(f"\n🌐 导航到入口: {entry_url}（等待重定向...）")
        await self._cmd("Target.createTarget", {"url": entry_url})

        # ── 等待用户登录 ──
        print(f"""
{'='*60}
  请在浏览器中完成登录操作：

  1. 输入账号密码
  2. 完成验证码（如有）
  3. 点击"进入游戏"或等效按钮
  4. 等待游戏大厅加载

  脚本将自动检测游戏页面并提取 token...
  超时时间: {timeout} 秒
{'='*60}
""")

        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"\n⚠️  超时 ({timeout}s)，尝试从已有页面提取...")
        except asyncio.CancelledError:
            print(f"\n⏸️  用户中断，尝试提取已捕获的数据...")

        # ── 先停止 reader loop，释放 WebSocket 读锁 ──
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

        # 尝试从 JWT API 响应中提取 token（路径 A：/site/api/v1/user/member/jwt）
        if not self._extracted_token and self._jwt_request_id:
            await self._fetch_jwt_response()

        # 如果没检测到 params，尝试从所有页面提取
        if not self._params and not self._extracted_token:
            await self._extract_from_all_pages()

        self._stop_event.set()
        await self._ws.close()

        return self._build_result()

    async def _fetch_post_data(self, req_id: str, url: str, sid: str):
        """异步获取 POST 请求体（CDP 不会在 requestWillBeSent 中包含）。"""
        try:
            await asyncio.sleep(0.5)  # 等待请求完成
            await self._cmd("Network.getRequestPostData", {
                "requestId": req_id,
            }, session_id=sid)
            # 响应会由 _reader_loop 收集（但我们不在这里阻塞等待）
            # 暂存 requestId，后续通过手动读取获取
        except Exception:
            pass

    async def _fetch_jwt_response(self):
        """从 /site/api/v1/user/member/jwt 响应体中直接提取 JWT。

        比等待游戏 iframe 更快，因为 JWT API 在登录后立即被调用。
        """
        print(f"\n🔍 尝试从 JWT API 响应提取 token...")
        try:
            await self._cmd("Network.getResponseBody", {
                "requestId": self._jwt_request_id,
            }, session_id=self._jwt_session_id)
            deadline = time.time() + 5
            while time.time() < deadline:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=3.0)
                try:
                    resp = json.loads(raw)
                    if resp.get("id") == self._msg_id:
                        body = resp.get("result", {}).get("body", "")
                        base64_encoded = resp.get("result", {}).get("base64Encoded", False)
                        if base64_encoded:
                            body = base64.b64decode(body).decode("utf-8")
                        try:
                            data = json.loads(body)
                            # 乐鱼格式: {"data": "<JWT>", "message": "...", "status_code": 200}
                            token = (data.get("data") or data.get("token") or
                                     data.get("jwt") or data.get("accessToken") or "")
                            # 嵌套结构: {"data": {"token": "..."}}
                            if isinstance(token, dict):
                                token = (token.get("token") or token.get("jwt") or
                                         token.get("accessToken") or "")
                            if token and isinstance(token, str) and len(token) > 20:
                                self._extracted_token = token
                                print(f"  ✅ 从 JWT API 提取到 token ({len(token)} chars): {token[:50]}...{token[-20:]}")
                                jwt_info = decode_jwt(token)
                                if jwt_info:
                                    exp = jwt_info.get("exp", 0)
                                    remaining = exp - time.time()
                                    print(f"  JWT 过期: {'❌ 已过期' if remaining <= 0 else f'✅ {remaining/3600:.1f} 小时'}")
                                    sub = jwt_info.get("sub", {})
                                    if isinstance(sub, dict):
                                        print(f"  playerId: {sub.get('playerId', '?')}")
                                self._stop_event.set()
                            else:
                                print(f"  ⚠️  JWT 响应字段: {list(data.keys())[:10]}")
                                print(f"  响应预览: {body[:300]}")
                        except json.JSONDecodeError:
                            print(f"  JWT 响应非 JSON: {body[:200]}")
                        break
                except json.JSONDecodeError:
                    continue
        except asyncio.TimeoutError:
            print(f"  ⚠️  获取 JWT 响应超时")

    async def _extract_from_all_pages(self):
        """从所有 attached 页面提取 localStorage + URL（备用方案）。"""
        for tid, sid in list(self._sessions.items()):
            mid = await self._cmd("Runtime.evaluate", {
                "expression": """
                JSON.stringify({
                    token: localStorage.getItem('token') || '',
                    playerId: localStorage.getItem('playerId') || '',
                    backendDomainUrl: localStorage.getItem('KEY_TARGET_API_DOMAIN') ||
                                      localStorage.getItem('backendDomainUrl') || '',
                    deviceId: localStorage.getItem('fixedDeviceId') || '',
                    url: window.location.href
                })
                """,
                "returnByValue": True,
            }, session_id=sid)
            # 读取该命令的响应
            try:
                while True:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=3.0)
                    try:
                        resp = json.loads(raw)
                        if resp.get("id") == mid:
                            value = resp.get("result", {}).get("result", {}).get("value", "{}")
                            data = json.loads(value) if isinstance(value, str) else value
                            token = data.get("token", "")
                            url = data.get("url", "")
                            print(f"  🔍 [{tid[:12]}...] token={'✅' if token else '❌'} url={url[:80]}")
                            if token:
                                self._extracted_token = token
                                self._local_storage = data
                            if "lisxdc.com" in url and "params=" in url:
                                self._game_iframe_url = url
                                parsed = urlparse(url)
                                qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
                                self._params = qs.get("params", "")
                                self._ttl = qs.get("ttl", "")
                            break
                    except json.JSONDecodeError:
                        continue
            except asyncio.TimeoutError:
                print(f"  ⚠️ [{tid[:12]}...] 无响应")
                continue

        # 尝试从 event 中恢复 params
        if not self._params:
            for evt in self._events:
                url = evt.get("url", "")
                if "lisxdc.com" in url and "params=" in url:
                    self._game_iframe_url = url
                    parsed = urlparse(url)
                    qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
                    self._params = qs.get("params", "")
                    self._ttl = qs.get("ttl", "")
                    print(f"  ✅ 从 event 恢复 params ({len(self._params)} chars)")
                    break

    def _build_result(self) -> dict:
        """汇总分析结果。"""
        # 从 HTTP 请求中尝试提取 params URL（备用）
        if not self._params:
            for r in self._http_requests:
                url = r.get("url", "")
                if "lisxdc.com" in url and "params=" in url:
                    parsed = urlparse(url)
                    qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
                    p = qs.get("params", "")
                    if p:
                        self._params = p
                        self._ttl = qs.get("ttl", "")
                        self._game_iframe_url = url
                        print(f"  ✅ 从 HTTP 请求恢复 params ({len(self._params)} chars)")
                        break

        result = {
            "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entry_url": self._entry_url,
            "game_iframe_url": self._game_iframe_url[:200] if self._game_iframe_url else "",
            "params_extracted": bool(self._params),
            "ttl": self._ttl,
            "params_preview": self._params[:80] + "..." if len(self._params) > 80 else self._params,
            "http_request_count": len(self._http_requests),
            "http_response_count": len(self._http_responses),
        }

        # 解密 params
        if self._params and self._ttl:
            try:
                decrypted = decrypt_params(self._params, self._ttl)
                result["decrypted_params"] = {
                    "playerId": decrypted.get("playerId"),
                    "token": (decrypted.get("token", "")[:40] + "..."
                              if len(decrypted.get("token", "")) > 40
                              else decrypted.get("token")),
                    "token_full": decrypted.get("token", ""),
                    "backendDomainUrl": decrypted.get("backendDomainUrl"),
                    "backendDomainUrlList": decrypted.get("backendDomainUrlList", ""),
                }
                token = decrypted.get("token", "")
                self._extracted_token = token

                # 解码 JWT
                jwt_info = decode_jwt(token)
                if jwt_info:
                    result["jwt_decoded"] = {
                        "iat": jwt_info.get("iat"),
                        "exp": jwt_info.get("exp"),
                        "sub": jwt_info.get("sub"),
                    }
                    expiry = jwt_info.get("exp", 0)
                    remaining = expiry - time.time()
                    result["jwt_decoded"]["expires_in_hours"] = round(remaining / 3600, 1)
                    result["jwt_decoded"]["expired"] = remaining <= 0
            except Exception as e:
                result["decrypt_error"] = str(e)

        # 关键 URL 列表
        result["key_urls"] = [
            {"url": r["url"], "method": r["method"]}
            for r in self._http_requests
            if any(kw in r["url"].lower() for kw in
                   ("login", "auth", "signin", "token", "params", "lisxdc", "egret",
                    "nhfspi", "rzhsir"))
        ]

        result["all_http_requests"] = self._http_requests
        result["all_http_responses"] = self._http_responses
        result["timeline"] = self._events

        return result


# ── 辅助函数 ──────────────────────────────────────────────


def _safe_headers(headers: dict) -> dict:
    """脱敏 headers（隐藏 cookie/token）。"""
    safe = {}
    for k, v in headers.items():
        if k.lower() in ("cookie", "set-cookie", "authorization", "x-auth-token"):
            safe[k] = v[:50] + "..." if len(v) > 50 else v
        else:
            safe[k] = v
    return safe


def _safe_post(post_data: str) -> str:
    """脱敏 POST body（隐藏密码）。"""
    if not post_data or len(post_data) < 300:
        return post_data
    try:
        d = json.loads(post_data)
        for key in ("password", "pwd", "passwd", "pass"):
            if key in d:
                d[key] = "***"
        return json.dumps(d, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return post_data[:200] + "..."

# ── 保存认证缓存 ──────────────────────────────────────────


def save_auth_cache(token: str, player_id: int, backend_domain: str,
                    device_id: str = ""):
    """保存认证缓存为 hdt/.auth_cache.json。"""
    # 从 backendDomain 提取 host
    host = backend_domain.split(":")[0] if ":" in backend_domain else backend_domain

    ws_url = (
        f"wss://wsproxy.{host}:18026/"
        f"?playerId={player_id}"
        f"&jwtToken={token}"
        f"&deviceId={device_id}"
        f"&deviceType=2&platform=6"
    )

    data = {
        "token": token,
        "player_id": player_id,
        "backend_domain": backend_domain,
        "device_id": device_id,
        "ws_url": ws_url,
    }
    AUTH_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\n💾 认证缓存已保存: {AUTH_CACHE}")
    return data


# ── 主入口 ────────────────────────────────────────────────


async def _resolve_cdp_ws_url(http_url: str) -> str:
    """从 Chrome HTTP CDP endpoint 获取 WebSocket URL。"""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{http_url}/json/version",
                             timeout=aiohttp.ClientTimeout(total=3)) as resp:
                data = await resp.json()
                url = data.get("webSocketDebuggerUrl", "")
                if url:
                    return url
    except Exception:
        pass
    return f"ws://127.0.0.1:{CDP_PORT}/devtools/browser"


async def _launch_visible_chrome(port: int = CDP_PORT) -> asyncio.subprocess.Process:
    """启动可见模式 Chrome（非 headless），让用户可以手动登录。"""
    import shutil
    import platform as _platform
    import tempfile

    system = _platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        candidates = [
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            "chrome.exe",
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

    chrome_path = None
    for c in candidates:
        if not os.path.isabs(c):
            found = shutil.which(c)
            if found:
                chrome_path = found
                break
        elif os.path.isfile(c):
            chrome_path = c
            break

    if not chrome_path:
        raise RuntimeError(f"找不到 Chrome，请手动安装: {candidates}")

    temp_dir = tempfile.mkdtemp(prefix="chrome_leyu_")
    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={temp_dir}",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if system == "Linux":
        args += ["--no-sandbox", "--disable-dev-shm-usage"]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # 等待端口就绪
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1,
            )
            writer.close()
            await writer.wait_closed()
            break
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.5)
    else:
        raise TimeoutError(f"Chrome 端口 {port} 未在 15 秒内就绪")

    print(f"✅ Chrome 已启动 (PID={proc.pid}, port={port})")
    return proc


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="观察并捕获乐鱼登录流程")
    parser.add_argument("--url", default=DEFAULT_ENTRY_URL,
                        help=f"乐鱼入口 URL，会被重定向到分配的子域名（默认: {DEFAULT_ENTRY_URL}）")
    parser.add_argument("--attach", type=str, default="",
                        help="连接已有 Chrome（如 --attach http://127.0.0.1:9222）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="等待登录的最大秒数（默认 300）")
    args = parser.parse_args()

    chrome_proc = None

    # ── 获取 CDP WebSocket URL ──
    if args.attach:
        # attach 模式：连接到用户已启动的 Chrome
        http_url = args.attach.rstrip("/")
        print(f"🔗 附加到已有 Chrome: {http_url}")
        cdp_ws_url = await _resolve_cdp_ws_url(http_url)
    else:
        # auto 模式：启动可见 Chrome
        print("🚀 启动可见 Chrome（非 headless，你需要手动登录）...")
        chrome_proc = await _launch_visible_chrome(CDP_PORT)
        cdp_ws_url = await _resolve_cdp_ws_url(f"http://127.0.0.1:{CDP_PORT}")

    print(f"   CDP WebSocket: {cdp_ws_url}")

    # ── 运行监控 ──
    observer = LoginObserver(cdp_ws_url)
    try:
        result = await observer.run(args.url, timeout=args.timeout)
    finally:
        # 不要立即关闭 Chrome，让用户检查
        if chrome_proc is not None:
            print("\n⏸️  Chrome 保持运行，按 Enter 关闭浏览器...")
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, input)
            except Exception:
                pass
            chrome_proc.terminate()
            try:
                await asyncio.wait_for(chrome_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                chrome_proc.kill()
                await chrome_proc.wait()
            print("🧹 Chrome 已关闭")

    # ── 输出结果 ──
    print(f"\n{'='*60}")
    print(f"  分析报告")
    print(f"{'='*60}")

    if result.get("decrypted_params"):
        dp = result["decrypted_params"]
        print(f"  ✅ params 解密成功")
        print(f"  playerId: {dp.get('playerId')}")
        print(f"  backendDomain: {dp.get('backendDomainUrl')}")
        print(f"  token: {dp.get('token', '')[:50]}...")
        if result.get("jwt_decoded"):
            j = result["jwt_decoded"]
            print(f"  JWT 过期: {'❌ 已过期' if j.get('expired') else '✅ 有效'}")
            print(f"  剩余: {j.get('expires_in_hours', 0)} 小时")
            sub = j.get("sub", {})
            if isinstance(sub, dict):
                print(f"  nickName: {sub.get('nickName', '?')}")
                print(f"  playerId: {sub.get('playerId', '?')}")
    elif observer._extracted_token:
        print(f"  ⚠️  从备用途径提取到 token")
        print(f"  token: {observer._extracted_token[:50]}...")
    else:
        print(f"  ❌ 未能提取到 token")
        if result.get("params_preview"):
            print(f"  params 已获取但解密失败")
        if result.get("decrypt_error"):
            print(f"  解密错误: {result['decrypt_error']}")

    print(f"\n  HTTP 请求: {result.get('http_request_count', 0)}")
    print(f"  HTTP 响应: {result.get('http_response_count', 0)}")

    key_urls = result.get("key_urls", [])
    if key_urls:
        print(f"\n  🔑 关键 URL ({len(key_urls)} 条):")
        for u in key_urls[:15]:
            print(f"    {u['method']:>6} {u['url'][:100]}")

    # ── 保存分析结果 ──
    ANALYSIS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ANALYSIS_OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                          default=str))
    print(f"\n📊 完整分析结果: {ANALYSIS_OUTPUT}")

    # ── 保存认证缓存 ──
    token = observer._extracted_token
    if not token:
        dp = result.get("decrypted_params", {})
        token = dp.get("token_full", "")

    if token:
        dp = result.get("decrypted_params", {})
        player_id = dp.get("playerId", 0)
        backend_domain = dp.get("backendDomainUrl", "")
        device_id = ""
        # 从 JWT 中获取 playerId 作为兜底
        if not player_id and result.get("jwt_decoded"):
            sub = result["jwt_decoded"].get("sub", {})
            if isinstance(sub, dict):
                player_id = sub.get("playerId", 0)

        save_auth_cache(token, player_id, backend_domain, device_id)

    print("\n✅ 完成。")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n⏸️  用户中断")
        sys.exit(1)
