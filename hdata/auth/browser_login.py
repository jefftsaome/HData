"""游戏自动登录 + 游戏 JWT 提取（基于 Playwright 浏览器自动化）。

═══════════════════════════════════════════════════════════════════════
两种模式:

  1. 首次登录（--login）:
     打开可见浏览器 → 导航到游戏登录页 → 用户手动完成登录+验证码
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
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from hdata.auth.params import (
    build_auth_snapshot,
    decode_jwt,
    decrypt_params,
    save_auth_cache,
)
from htools.utils.logger import get_logger

logger = get_logger(__name__)

_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))

# ── 常量 ──────────────────────────────────────────────────
# 默认入口 URL（可通过 --url 覆盖）
DEFAULT_ENTRY_URL = "https://leyu.me"
# 默认浏览器配置目录
CHROME_PROFILE_DIR = _PROJ_ROOT / ".chrome_profile"
# 默认认证缓存路径
AUTH_CACHE_PATH = _PROJ_ROOT / ".auth_cache.json"
# 缓存目录
CACHE_DIR = _PROJ_ROOT / ".cache"

# ── 核心逻辑 ──────────────────────────────────────────────


class GameBrowserLogin:
    """使用 Playwright 自动化游戏登录 + token 提取。"""

    def __init__(self, entry_url: str = DEFAULT_ENTRY_URL, headless: bool = True,
                 profile_dir: Path | None = None, auth_cache_path: Path | None = None):
        """
        Args:
            entry_url: 游戏入口 URL（默认 https://leyu.me）
            headless: 是否无头模式（True=不显示浏览器窗口）
            profile_dir: 浏览器配置目录（用于持久化登录态）
            auth_cache_path: 认证缓存路径（保存解密后的 token 等信息）
        """
        self._entry_url = entry_url
        self._headless = headless
        self._profile_dir = profile_dir or CHROME_PROFILE_DIR
        self._auth_cache_path = auth_cache_path or AUTH_CACHE_PATH
        self._captured_params: str = ""
        self._captured_ttl: str = ""
        self._captured_url: str = ""

    async def run(self) -> dict | None:
        """执行登录/刷新流程，返回解密后的认证数据（含 session 信息）。"""
        from playwright.async_api import async_playwright

        self._profile_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            # 使用持久化 context 保留登录态
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self._profile_dir),
                headless=self._headless,
                # 种子站域名/证书由平台动态轮换，入口常出现证书与域名
                # 不匹配（ERR_CERT_AUTHORITY_INVALID）；登录流程靠后续
                # 重定向发现真实域名，必须容忍入口证书异常才能走下去
                ignore_https_errors=True,
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
            context.on("request", self._on_request)
            page.on("request", self._on_request)

            # ── 导航到入口 ──
            logger.info("navigating to: {}", self._entry_url)
            await page.goto(self._entry_url, wait_until="domcontentloaded", timeout=30000)

            # 等待重定向完成
            await asyncio.sleep(2)
            current_url = page.url
            logger.info("current URL: {}", current_url[:100])

            if self._headless:
                # 自动模式：等待自动跳转到游戏或超时
                result = await self._wait_for_params(context=context, timeout=60)
            else:
                logger.info("Login mode — please complete the login in the browser.")
                result = await self._wait_for_params(context=context, timeout=600)

            # ── 捕获 Session 数据（cookies / X-API-TOKEN / uuid 等）──
            if result:
                result = await self._enrich_with_session(context, result)

            await context.close()
            return result

    async def _enrich_with_session(self, context, result: dict) -> dict:
        """捕获浏览器 session 数据，合并到 result 中。"""
        domain_str = ""
        x_api_token = ""
        uuid_val = ""
        uuid_b64 = ""
        all_cookies = ""

        # 1. 从各个页面的 localStorage 和 URL 提取数据
        for pg in context.pages:
            try:
                url = pg.url
                if url and not domain_str:
                    from urllib.parse import urlparse as _urlparse
                    parsed = _urlparse(url)
                    if parsed.netloc:
                        domain_str = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass

            # 已经有数据的就不再读
            if x_api_token and uuid_val and uuid_b64:
                break

            try:
                data = await pg.evaluate("""(() => {
                    const g = (k) => {
                        try { return localStorage.getItem(k) || ''; }
                        catch (e) { return ''; }
                    };
                    return {
                        x_api_token: g('X-API-TOKEN'),
                        uuid: g('uuid'),
                        uuidToBase64: g('uuidToBase64'),
                    };
                })()""")
                if not x_api_token:
                    x_api_token = data.get("x_api_token", "")
                if not uuid_val:
                    uuid_val = data.get("uuid", "")
                if not uuid_b64:
                    uuid_b64 = data.get("uuidToBase64", "")
            except Exception:
                pass

        # 2. 捕获 cookies
        try:
            cookies = await context.cookies()
            if cookies:
                all_cookies = "; ".join(
                    f"{c.get('name', '')}={c.get('value', '')}" for c in cookies
                )
        except Exception:
            pass

        # 3. 合并
        if domain_str and "domain" not in result:
            result["domain"] = domain_str
        if x_api_token and "token" not in result:
            result["token"] = x_api_token
        if uuid_val and "uuid" not in result:
            result["uuid"] = uuid_val
        if uuid_b64 and "uuidToBase64" not in result:
            result["uuidToBase64"] = uuid_b64
        if all_cookies and "cookies" not in result:
            result["cookies"] = all_cookies

        logger.info("enriched session: domain={}, token={}..., uuid={}..., cookies={} chars",
                    bool(domain_str), x_api_token[:30] if x_api_token else "NO",
                    bool(uuid_val), len(all_cookies))
        return result

    def _on_request(self, request):
        """拦截 Playwright 网络请求，捕获 params URL。"""
        url = request.url
        if "params=" in url:
            params, ttl = self._extract_params_ttl_from_url(url)
            if params:
                self._captured_params = params
                self._captured_ttl = ttl
                self._captured_url = url
                logger.info("captured params URL: {}", url[:100])

    async def _wait_for_params(self, context, timeout: int) -> dict | None:
        """轮询等待 params URL 被截获，或从页面存储中直接提取 JWT。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._captured_params:
                return self._decrypt_and_save()

            # 兜底：有些站点在新标签页/iframe 中跳转，request 事件可能错过。
            # 直接轮询所有页面，从 URL 与 localStorage/window.urlParams 提取。
            fallback = await self._probe_all_pages(context)
            if fallback:
                return fallback

            await asyncio.sleep(1)
        return None

    async def _probe_all_pages(self, context) -> dict | None:
        """从 context 所有页面兜底提取 token/params。"""
        for pg in list(context.pages):
            try:
                url = pg.url or ""
            except Exception:
                continue

            # 1) 先看页面 URL 是否已经包含 params
            if url and "params=" in url:
                params, ttl = self._extract_params_ttl_from_url(url)
                if params:
                    self._captured_params = params
                    self._captured_ttl = ttl
                    self._captured_url = url
                    logger.info("captured params URL from page: {}", url[:100])
                    return self._decrypt_and_save()

            # 2) 再看 localStorage/window.urlParams 是否已有 game JWT
            try:
                data = await pg.evaluate(
                    """(() => {
                        const g = (k) => {
                            try { return localStorage.getItem(k) || ''; }
                            catch (e) { return ''; }
                        };
                        const wp = (window.urlParams && typeof window.urlParams === 'object')
                            ? window.urlParams : {};
                        return {
                            url: window.location.href || '',
                            ls_token: g('token'),
                            ls_backend: g('KEY_TARGET_API_DOMAIN') || g('backendDomainUrl'),
                            ls_player: g('playerId'),
                            ls_device_id: g('fixedDeviceId'),
                            ls_x_api_token: g('X-API-TOKEN'),
                            ls_uuid: g('uuid'),
                            ls_uuid_to_base64: g('uuidToBase64'),
                            wp_token: wp.token || '',
                            wp_backend: wp.backendDomainUrl || wp.backendDomainUrlList || '',
                            wp_player: wp.playerId || 0,
                            wp_device_id: wp.fixedDeviceId || '',
                            wp_url_params: JSON.stringify(wp),
                        };
                    })()"""
                )
            except Exception:
                logger.debug("evaluate failed on page: {}", url[:100])
                continue

            if not isinstance(data, dict):
                continue

            # --- 提取游戏页字段 ---
            token = (data.get("wp_token") or data.get("ls_token") or "").strip()
            if not token:
                continue

            # 仅接受 game JWT，避免误收 X-API-TOKEN。
            if not decode_jwt(token):
                continue

            backend = (data.get("wp_backend") or data.get("ls_backend") or "").strip()
            player_raw = data.get("wp_player") or data.get("ls_player") or 0
            try:
                player_id = int(player_raw)
            except Exception:
                player_id = 0

            # 提取 window.urlParams 中的备用地址列表
            url_params_str = data.get("wp_url_params", "")
            backend_list = ""
            if url_params_str:
                try:
                    import json as _json
                    wp = _json.loads(url_params_str) if isinstance(url_params_str, str) else {}
                    if not backend:
                        backend = wp.get("backendDomainUrl", "")
                    backend_list = wp.get("backendDomainUrlList", "")
                except Exception:
                    pass

            # --- 额外提取主站页面的 session 字段 ---
            x_api_token = data.get("ls_x_api_token", "")
            uuid = data.get("ls_uuid", "")
            uuid_to_base64 = data.get("ls_uuid_to_base64", "")
            device_id = data.get("ls_device_id", "") or data.get("wp_device_id", "")
            page_domain = data.get("url", "")

            # 从 URL 提取 domain（协议 + 主机 + 端口）
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(page_domain)
            domain_str = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""

            result = {
                "game_token": token,
                "game_player_id": player_id,
                "game_backend": backend,
                "backend_domain_url_list": backend_list,
                "device_id": device_id,
                "token": x_api_token,
                "uuid": uuid,
                "uuidToBase64": uuid_to_base64,
                "domain": domain_str,
                "source": "page_storage",
            }
            # 回写 WS-only 缓存（兼容旧逻辑）
            game_snap = build_auth_snapshot(token, player_id, backend, source="page_storage")
            save_auth_cache(game_snap, self._auth_cache_path)
            logger.info("extracted game JWT from page storage and saved to auth cache: {}", self._auth_cache_path)
            return result

        return None

    @staticmethod
    def _extract_params_ttl_from_url(url: str) -> tuple[str, str]:
        """从 URL 提取 params/ttl，保留 '+'，仅做 %xx 解码。"""
        parsed = urlparse(url)
        params = ""
        ttl = ""
        for part in parsed.query.split("&"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k == "params":
                params = unquote(v)  # 不用 parse_qs，避免 '+' 变空格
            elif k == "ttl":
                ttl = unquote(v)
        return params, ttl

    def _decrypt_and_save(self) -> dict | None:
        """解密 params 并保存到缓存。"""
        logger.info("decrypting params (ttl={})...", self._captured_ttl)
        try:
            decrypted = decrypt_params(self._captured_params, self._captured_ttl)  # noqa: F811
        except Exception as e:
            logger.error("decrypt failed: {}", e)
            return None

        token = decrypted.get("game_token") or decrypted.get("token", "")
        if not token:
            logger.error("decrypt result has no token")
            return None

        player_id = decrypted.get("game_player_id") or decrypted.get("playerId", 0)
        backend = (
            decrypted.get("game_backend")
            or decrypted.get("backendDomainUrl", "")
            or decrypted.get("backendDomainUrlList", "").split(",")[0].strip()
        )

        backend_list = decrypted.get("backendDomainUrlList", "")
        device_id = decrypted.get("fixedDeviceId", "")

        logger.info("decrypted: playerId={}, backendDomain={}, token={}...{}",
                    player_id,
                    backend,
                    token[:50], token[-20:])

        jwt_info = decode_jwt(token)
        if jwt_info:
            exp = jwt_info.get("exp", 0)
            remaining = exp - time.time()
            logger.info("JWT exp={}, remaining={:.1f} hours{}",
                        exp, remaining / 3600, ", [expired!]" if remaining <= 0 else "")

        current = build_auth_snapshot(token, int(player_id or 0), backend,
                                      source="params_decrypt")
        current["backend_domain_url_list"] = backend_list
        if device_id:
            current["device_id"] = device_id
        save_auth_cache(current, self._auth_cache_path)
        logger.info("saved auth cache to: {}", self._auth_cache_path)
        return current


# ── 主入口 ────────────────────────────────────────────────


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="游戏自动登录 + token 提取")
    parser.add_argument("--account", default="",
                        help="账号名（用于隔离 profile/cache；不填则使用默认 .chrome_profile）")
    parser.add_argument("--login", action="store_true",
                        help="首次登录模式（打开可见浏览器，手动登录）")
    parser.add_argument("--url", default=DEFAULT_ENTRY_URL,
                        help=f"游戏入口 URL（默认: {DEFAULT_ENTRY_URL}）")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="强制 headless 模式")
    parser.add_argument("--no-headless", action="store_true",
                        help="显示浏览器窗口（调试用）")
    args = parser.parse_args()

    headless = not args.no_headless if args.no_headless else (not args.login)

    if args.account:
        profile_dir = CACHE_DIR / "browser_profiles" / args.account
        auth_cache_path = CACHE_DIR / f"{args.account}.auth_cache.json"
    else:
        profile_dir = CHROME_PROFILE_DIR
        auth_cache_path = AUTH_CACHE_PATH

    bot = GameBrowserLogin(
        entry_url=args.url,
        headless=headless,
        profile_dir=profile_dir,
        auth_cache_path=auth_cache_path,
    )

    try:
        result = await bot.run()
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt — exiting...")
        return 1

    if result:
        logger.info("Token captured successfully.")
        return 0
    else:
        logger.error("Failed to capture token.")
        if not args.login:
            logger.warning("Session may have expired, please run with --login to re-login.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
