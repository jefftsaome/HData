"""乐鱼自动登录 + 游戏 JWT 提取（基于 Playwright 浏览器自动化）。

═══════════════════════════════════════════════════════════════════════
两种模式:

  1. 首次登录（--login）:
     打开可见浏览器 → 导航到乐鱼登录页 → 用户手动完成登录+验证码
     → 进入游戏大厅 → 自动截获 params URL → 解密保存 token
     → 浏览器 session（cookies/localStorage）持久化到 .chrome_profile/

  2. 自动刷新（默认）:
     使用持久化 session 启动 headless 浏览器 → 自动跳转到游戏
     → 截获 params URL → 解密保存 token（无需人工干预）

原理:
  - Playwright 持久化 browser context 保留登录态
  - 即使 game JWT 24h 过期，主站 session（7天有效）可生成新 game JWT
  - 拦截到 pc.lisxdc.com:2083/egret/?params=... 的 HTTP 请求即提取

用法:
    cd hdt

    # 首次登录（需要人工操作）
    uv run python -m hdt.auth.browser_login --login

    # 后续自动刷新（无需人工）
    uv run python -m hdt.auth.browser_login

    # 指定入口 URL
    uv run python -m hdt.auth.browser_login --url "https://leyu.me"
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))

# ── 常量 ──────────────────────────────────────────────────
DEFAULT_ENTRY_URL = "https://leyu.me"
CHROME_PROFILE_DIR = _PROJ_ROOT / ".chrome_profile"
AUTH_CACHE_PATH = _PROJ_ROOT / ".auth_cache.json"

# ── params 解密 ───────────────────────────────────────────


def decrypt_params(params_b64: str, ttl: str) -> dict:
    """解密乐鱼 params 参数。

    Key = ttl + "AES"，AES-ECB，PKCS7 padding。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = (ttl + "AES").encode("ascii")
    ct = base64.b64decode(params_b64)
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    return json.loads(padded[: -padded[-1]])  # PKCS7 unpad


def decode_jwt(token: str) -> dict | None:
    """解码 JWT payload（不验证签名）。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if isinstance(payload.get("sub"), str):
            payload["sub"] = json.loads(payload["sub"])
        return payload
    except Exception:
        return None


def save_auth_cache(decrypted: dict) -> dict:
    """保存认证缓存到 .auth_cache.json。"""
    token = decrypted.get("token", "")
    player_id = decrypted.get("playerId", 0)
    backend = decrypted.get("backendDomainUrl", "")

    # 从 backendDomainUrlList 取第一个作为兜底
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
        "token": token,
        "player_id": player_id,
        "backend_domain": backend,
        "device_id": "",
        "ws_url": ws_url,
    }
    AUTH_CACHE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return data


# ── 核心逻辑 ──────────────────────────────────────────────


class LeyuBrowserLogin:
    """使用 Playwright 自动化乐鱼登录 + token 提取。"""

    def __init__(self, entry_url: str = DEFAULT_ENTRY_URL, headless: bool = True):
        self._entry_url = entry_url
        self._headless = headless
        self._captured_params: str = ""
        self._captured_ttl: str = ""
        self._captured_url: str = ""

    async def run(self) -> dict | None:
        """执行登录/刷新流程，返回解密后的认证数据。"""
        from playwright.async_api import async_playwright

        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            # 使用持久化 context 保留登录态
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE_DIR),
                headless=self._headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )

            page = context.pages[0] if context.pages else await context.new_page()

            # ── 拦截网络请求，捕获 params URL ──
            page.on("request", self._on_request)

            # ── 导航到入口 ──
            print(f"🌐 导航到: {self._entry_url}")
            await page.goto(self._entry_url, wait_until="domcontentloaded", timeout=30000)

            # 等待重定向完成
            await asyncio.sleep(2)
            current_url = page.url
            print(f"   当前 URL: {current_url[:100]}")

            if self._headless:
                # 自动模式：等待自动跳转到游戏或超时
                result = await self._wait_for_params(timeout=60)
                if result:
                    await context.close()
                    return result
                print("⚠️  自动模式未截获 params，可能需要重新登录。请使用 --login 模式。")
            else:
                # 登录模式：等待用户操作
                print(f"""
{'='*60}
  首次登录模式 — 请在浏览器中完成操作：

  1. 如果显示登录页，输入账号密码 + 验证码
  2. 登录后，点击进入任意游戏（如百家乐）
  3. 脚本将自动截获 token 并退出

  等待中...（最长 10 分钟）
{'='*60}
""")
                result = await self._wait_for_params(timeout=600)
                if result:
                    print("\n✅ 登录成功！Session 已持久化，下次可直接运行（无需 --login）。")
                    await context.close()
                    return result

            await context.close()

        return None

    def _on_request(self, request):
        """拦截 Playwright 网络请求，捕获 params URL。"""
        url = request.url
        if "lisxdc.com" in url and "params=" in url:
            parsed = urlparse(url)
            qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
            params = qs.get("params", "")
            ttl = qs.get("ttl", "")
            if params:
                self._captured_params = params
                self._captured_ttl = ttl
                self._captured_url = url
                print(f"\n🎯 截获 params URL ({len(params)} chars)")

    async def _wait_for_params(self, timeout: int) -> dict | None:
        """轮询等待 params URL 被截获，然后解密返回。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._captured_params:
                return self._decrypt_and_save()
            await asyncio.sleep(1)
        return None

    def _decrypt_and_save(self) -> dict | None:
        """解密 params 并保存到缓存。"""
        print(f"🔓 解密 params (ttl={self._captured_ttl})...")
        try:
            decrypted = decrypt_params(self._captured_params, self._captured_ttl)
        except Exception as e:
            print(f"❌ 解密失败: {e}")
            return None

        token = decrypted.get("token", "")
        if not token:
            print("❌ 解密结果中无 token")
            return None

        print(f"✅ 解密成功")
        print(f"   playerId: {decrypted.get('playerId')}")
        print(f"   backendDomain: {decrypted.get('backendDomainUrl')}")
        print(f"   token: {token[:50]}...{token[-20:]}")

        jwt_info = decode_jwt(token)
        if jwt_info:
            exp = jwt_info.get("exp", 0)
            remaining = exp - time.time()
            print(f"   JWT 有效期: {remaining / 3600:.1f} 小时"
                  f"{' ❌ 已过期' if remaining <= 0 else ' ✅'}")

        save_auth_cache(decrypted)
        print(f"💾 已保存到 {AUTH_CACHE_PATH}")
        return decrypted


# ── 主入口 ────────────────────────────────────────────────


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="乐鱼自动登录 + token 提取")
    parser.add_argument("--login", action="store_true",
                        help="首次登录模式（打开可见浏览器，手动登录）")
    parser.add_argument("--url", default=DEFAULT_ENTRY_URL,
                        help=f"乐鱼入口 URL（默认: {DEFAULT_ENTRY_URL}）")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="强制 headless 模式")
    parser.add_argument("--no-headless", action="store_true",
                        help="显示浏览器窗口（调试用）")
    args = parser.parse_args()

    headless = not args.no_headless if args.no_headless else (not args.login)

    bot = LeyuBrowserLogin(entry_url=args.url, headless=headless)

    try:
        result = await bot.run()
    except KeyboardInterrupt:
        print("\n⏸️  用户中断")
        return 1

    if result:
        print("\n✅ Token 获取成功。")
        return 0
    else:
        print("\n❌ 未能获取 token。")
        if not args.login:
            print("   提示：Session 可能已过期，请运行 --login 重新登录。")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
