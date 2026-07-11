"""浏览器登录 — 通过 browser-act stealth Chrome + raw CDP 实现。

核心发现: Playwright 的 page.mouse / locator.click / connect_over_cdp
均会干扰 GeeTest SDK 接受鼠标事件。必须用 raw CDP websocket 直连
browser-act 的 Chrome，通过 Input.dispatchMouseEvent 发送原始点击。

架构:
  full_login: raw CDP (Runtime.evaluate + Input.dispatchMouseEvent)  ← 验证码点击
  refresh_jwt: Playwright (仅导航 + 拦截 params)  ← 无验证码，可用 Playwright

用法:
    hl = HeadlessLogin("lds003", Path(".cache/browser_profiles/lds003"))
    session = await hl.full_login(user, pwd, solver)
    jwt = await hl.refresh_jwt()
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from htools.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_ENTRY_URL = "https://leyu.me"
CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"


def _save_domain_cache(cache_path: Path, domain: str):
    """写入域名缓存。"""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "domain": domain, "updated_at": int(time.time())
        }))
    except Exception:
        pass


class HeadlessLogin:
    """通过 browser-act stealth Chrome 完成登录。

    browser-act Chrome 需以 --remote-debugging-port 运行。
    full_login 使用 raw CDP，refresh_jwt 使用 Playwright。
    """

    def __init__(self, account: str, profile_dir: Path,
                 ba_manager=None):
        self._account = account
        self._profile_dir = Path(profile_dir)
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._ba = ba_manager  # BrowserActManager，延迟注入
        self._cdp_port = 0
        self._cdp_base = ""

    # ── 公开方法 ──────────────────────────────────────────

    async def refresh_jwt(self, entry_url: str = DEFAULT_ENTRY_URL,
                          timeout: float = 15.0) -> dict | None:
        """用持久化 session cookies 自动跳转游戏页截获 JWT。Playwright 实现。"""
        from playwright.async_api import async_playwright

        # 快速检查：browser-act 是否在运行
        domain = await self._resolve_domain(entry_url)
        if not domain or "leyu.me" in domain:
            return None  # 浏览器不可用，跳过

        logger.info(f"[{self._account}] session 刷新...")
        if not domain or "leyu.me" in domain:
            logger.warning(f"[{self._account}] 域名解析失败: {domain}")
            return None

        async with async_playwright() as p:
            await self._ensure_ba()
            browser = await p.chromium.connect_over_cdp(self._cdp_base)
            try:
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else await context.new_page()

                captured_params = ""
                captured_ttl = ""

                def on_request(request):
                    nonlocal captured_params, captured_ttl
                    url = request.url
                    if "lisxdc.com" in url and "params=" in url:
                        parsed = urlparse(url)
                        qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
                        if qs.get("params"):
                            captured_params = qs["params"]
                            captured_ttl = qs.get("ttl", "")
                            logger.debug(f"[{self._account}] 截获 params")

                page.on("request", on_request)
                await page.goto(f"{domain}/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                if "/user/login" in page.url:
                    logger.debug(f"[{self._account}] session 已过期")
                    return None

                deadline = time.time() + timeout
                while time.time() < deadline:
                    if captured_params:
                        return self._decrypt_params(captured_params, captured_ttl)
                    if "/user/login" in page.url:
                        return None
                    await asyncio.sleep(1)

                logger.warning(f"[{self._account}] 刷新超时")
                return None
            finally:
                pass  # 不关闭 browser，由 browser-act 管理

    async def full_login(self, user: str, pwd: str,
                         solver,  # CaptchaSolver
                         entry_url: str = DEFAULT_ENTRY_URL,
                         timeout: float = 30.0) -> dict:
        """完整登录流程 — 全 raw CDP，无 Playwright 参与。"""
        import aiohttp, websockets
        from hdata.auth.captcha_solver import CaptchaSolveError

        logger.info(f"[{self._account}] raw CDP 登录 (user={user})...")

        await self._ensure_ba()

        # 获取域名
        domain = await self._resolve_domain(entry_url)
        logger.debug(f"[{self._account}] 域名: {domain}")

        # ── 获取 CDP WebSocket ──
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{self._cdp_base}/json/list", timeout=5)
            targets = await r.json()
            r2 = await s.get(f"{self._cdp_base}/json/version", timeout=5)
            ws_url = (await r2.json()).get("webSocketDebuggerUrl", "")

        if not ws_url:
            raise RuntimeError("CDP: 无法获取 WebSocket URL")

        # 找页面（优先 /user/login 页面，其次任何非 leyu.me 页面）
        page_t = next((t for t in targets if t["type"] == "page"
                       and "/user/login" in t.get("url", "")), None)
        if not page_t:
            page_t = next((t for t in targets if t["type"] == "page"
                           and "leyu.me" not in t.get("url", "")), None)
        if not page_t:
            page_t = next((t for t in targets if t["type"] == "page"), None)
        if not page_t:
            raise RuntimeError("CDP: 未找到 page target")

        async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
            cdp_mid = 0

            async def cdp(method, params=None):
                nonlocal cdp_mid; cdp_mid += 1
                await ws.send(json.dumps(
                    {"id": cdp_mid, "method": method, "params": params or {}}))
                return cdp_mid

            async def cdp_eval(expression: str) -> dict | None:
                """Runtime.evaluate + 等待响应，返回 value。"""
                mid = await cdp("Runtime.evaluate",
                                {"expression": expression, "returnByValue": True})
                dl = time.time() + 5
                while time.time() < dl:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if msg.get("id") == mid:
                        return msg.get("result", {}).get("result", {}).get("value")
                return None

            # ── Attach + Navigate ──
            await cdp("Target.attachToTarget",
                      {"targetId": page_t["id"], "flatten": True})
            cdp_sid = ""
            dl = time.time() + 5
            while time.time() < dl:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if msg.get("id") == cdp_mid:
                    cdp_sid = msg.get("result", {}).get("sessionId", "")
                if msg.get("method") == "Target.attachedToTarget":
                    cdp_sid = msg["params"]["sessionId"]
                if cdp_sid:
                    break
            if not cdp_sid:
                raise RuntimeError("CDP: attachToTarget 失败")

            async def cdp_page(method, params=None):
                """发送 CDP 命令到页面 session（sessionId 在顶层，不在 params 内）。"""
                nonlocal cdp_mid; cdp_mid += 1
                msg = {"id": cdp_mid, "method": method, "params": params or {}}
                if cdp_sid:
                    msg["sessionId"] = cdp_sid
                await ws.send(json.dumps(msg))
                return cdp_mid

            async def cdp_page_eval(expression: str) -> dict | None:
                mid = await cdp_page("Runtime.evaluate",
                                     {"expression": expression, "returnByValue": True})
                dl = time.time() + 5
                while time.time() < dl:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if msg.get("id") == mid:
                        return msg.get("result", {}).get("result", {}).get("value")
                return None

            # ── Step 1: 导航到登录页 ──
            login_url = f"{domain}/user/login"
            logger.debug(f"[{self._account}] 导航: {login_url}")
            await cdp_page("Page.navigate", {"url": login_url})
            # 等页面渲染完成
            await asyncio.sleep(4)

            # ── Step 2: 填表 + 提交 ──
            logger.debug(f"[{self._account}] 填表 + 提交...")
            escaped_pwd = pwd.replace("\\", "\\\\").replace("'", "\\'")
            escaped_user = user.replace("\\", "\\\\").replace("'", "\\'")
            fill_result = await cdp_page_eval(
                f"""(function() {{
                    var io = document.querySelectorAll('input[type=text], input:not([type]), input[type=password]');
                    var userInp = null, pwdInp = null;
                    for (var i = 0; i < io.length; i++) {{
                        var inp = io[i];
                        if (!userInp && inp.type !== 'password') userInp = inp;
                        else if (!pwdInp && inp.type === 'password') pwdInp = inp;
                        if (userInp && pwdInp) break;
                    }}
                    if (!userInp || !pwdInp) return 'no_inputs';
                    userInp.focus(); userInp.value = '{escaped_user}';
                    userInp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    userInp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    pwdInp.focus(); pwdInp.value = '{escaped_pwd}';
                    pwdInp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    pwdInp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    var btn = document.querySelector('span.sV0BIdNgkCghFjH6HXzUFg__');
                    if (!btn) {{
                        var spans = document.querySelectorAll('span');
                        for (var j = 0; j < spans.length; j++) {{
                            if (spans[j].textContent.trim() === '登录') {{ btn = spans[j]; break; }}
                        }}
                    }}
                    if (btn) {{ btn.click(); return 'clicked'; }}
                    return 'no_button';
                }})()"""
            )
            logger.debug(f"[{self._account}] 表单结果: {fill_result}")
            await asyncio.sleep(1)

            # ── Step 3: 等待验证码弹窗 ──
            logger.debug(f"[{self._account}] 等待 GeeTest 弹窗...")
            popup_box = None
            for _ in range(15):
                await asyncio.sleep(1)
                val = await cdp_page_eval(
                    """(function(){
                        var e = document.querySelector('[class*="botion_click"]');
                        if (!e) return null;
                        var r = e.getBoundingClientRect();
                        return r.width > 50 ? {x:r.x, y:r.y, w:r.width, h:r.height} : null;
                    })()"""
                )
                if val:
                    popup_box = val
                    logger.debug(f"[{self._account}] 弹窗: {val['w']:.0f}x{val['h']:.0f}")
                    break
            if not popup_box:
                raise RuntimeError("验证码弹窗未出现")

            # ── Step 4: 获取验证码图片 + 识别 ──
            logger.debug(f"[{self._account}] 调用 {solver.info().name} 识别...")
            img_val = await cdp_page_eval(
                """(function(){
                    var r = {bg: null, ques: []};
                    document.querySelectorAll('[class*="botion_bg"]').forEach(function(e){
                        var bg = getComputedStyle(e).backgroundImage;
                        var m = bg.match(/url\\(["']?([^"')\\s]+)["']?\\)/);
                        if (m && m[1] && m[1].indexOf('captcha_v4') >= 0) r.bg = m[1];
                    });
                    document.querySelectorAll('[class*="botion"] img').forEach(function(i){
                        if (i.naturalWidth >= 60 && i.naturalHeight >= 60
                            && i.src.indexOf('sprite') < 0) r.ques.push(i.src);
                    });
                    return JSON.stringify(r);
                })()"""
            )
            if not img_val:
                raise RuntimeError("无法从 DOM 提取验证码图片")
            img_data = json.loads(img_val)
            if not img_data.get("bg") or len(img_data.get("ques", [])) < 3:
                raise RuntimeError("验证码图片提取不完整")

            from hdata.auth.captcha_solver import CaptchaChallenge
            challenge = CaptchaChallenge(
                lot_number="", payload="", process_token="",
                bg_url=img_data["bg"],
                ques_urls=img_data["ques"][:3],
                captcha_id=CAPTCHA_ID,
            )

            solution = None
            for retry in range(3):
                try:
                    solution = await solver.solve(challenge)
                    break
                except CaptchaSolveError:
                    if retry < 2:
                        logger.warning(f"[{self._account}] jfbym 重试 {retry + 1}/2...")
                        await asyncio.sleep(2)
                    else:
                        raise
            if not solution:
                raise RuntimeError("jfbym solve 返回 None")
            logger.debug(f"[{self._account}] 识别成功: {len(solution.pts)} 点, "
                        f"{solution.latency_ms:.0f}ms")

            # ── Step 5: CDP Input 点击（用 botion_bg 坐标，非 botion_click）──
            logger.debug(f"[{self._account}] 点击: {solution.coords}")

            # botion_bg 是实际点击图片元素，botion_click 是外层容器
            # jfbym 坐标基于 300×200 原图，需缩放到 botion_bg 实际尺寸
            bg_box = await cdp_page_eval(
                """(function(){
                    var e = document.querySelector('[class*="botion_bg"]');
                    if (!e) return null;
                    var r = e.getBoundingClientRect();
                    return r.width > 50 ? {x:r.x, y:r.y, w:r.width, h:r.height} : null;
                })()"""
            )
            if bg_box:
                scale_x = bg_box["w"] / 300.0
                scale_y = bg_box["h"] / 200.0
                ref_x = bg_box["x"]
                ref_y = bg_box["y"]
            else:
                # 兜底：用 botion_click
                scale_x = popup_box["w"] / 300.0
                scale_y = popup_box["h"] / 200.0
                ref_x = popup_box["x"]
                ref_y = popup_box["y"]

            for i, (x, y) in enumerate(solution.pts):
                sx = ref_x + x * scale_x
                sy = ref_y + y * scale_y
                logger.debug(f"[{self._account}] 点击 {i + 1}: ({sx:.0f}, {sy:.0f})")
                await cdp_page("Input.dispatchMouseEvent",
                               {"type": "mouseMoved", "x": sx, "y": sy})
                await asyncio.sleep(0.08)
                await cdp_page("Input.dispatchMouseEvent",
                               {"type": "mousePressed", "x": sx, "y": sy,
                                "button": "left", "clickCount": 1})
                await asyncio.sleep(0.08)
                await cdp_page("Input.dispatchMouseEvent",
                               {"type": "mouseReleased", "x": sx, "y": sy,
                                "button": "left", "clickCount": 1})
                await asyncio.sleep(0.4)

            # ── Step 6: 等待登录 + 提取完整 session ──
            logger.debug(f"[{self._account}] 等待登录...")
            deadline = time.time() + timeout
            while time.time() < deadline:
                await asyncio.sleep(0.5)
                val = await cdp_page_eval(
                    'JSON.stringify({'
                    'u: localStorage.getItem("uuidToBase64") || "",'
                    't: localStorage.getItem("X-API-TOKEN") || "",'
                    'id: localStorage.getItem("_uuid") || ""'
                    '})'
                )
                if val:
                    ls = json.loads(val)
                    if ls.get("t") and len(ls["t"]) > 10:
                        # uuidToBase64 可能延迟写入，等 1s 再读一次
                        if not ls.get("u"):
                            await asyncio.sleep(1)
                            val2 = await cdp_page_eval(
                                'JSON.stringify({'
                                'u: localStorage.getItem("uuidToBase64") || "",'
                                't: localStorage.getItem("X-API-TOKEN") || "",'
                                'id: localStorage.getItem("_uuid") || ""'
                                '})'
                            )
                            if val2:
                                ls = json.loads(val2)

                        session = {
                            "token": ls.get("t", ""),
                            "uuid": ls.get("id", ""),
                            "uuidToBase64": ls.get("u", ""),
                            "cookies": "",
                            "domain": domain,
                        }
                        logger.info(f"[{self._account}] ✅ 登录成功 "
                                    f"(uuidToBase64={'有' if ls.get('u') else '无'})")
                        return session

            raise RuntimeError("登录响应超时")

    async def _ensure_ba(self):
        """确保 browser-act 在线并更新 CDP 连接信息。"""
        if self._ba is None:
            from hdata.auth.browser_act import BrowserActManager
            self._ba = BrowserActManager()
        if not self._cdp_port or not self._cdp_base:
            port = await self._ba.ensure_running()
            self._cdp_port = port
            self._cdp_base = f"http://127.0.0.1:{port}"

    # ── 域名解析 ──────────────────────────────────────────

    async def _resolve_domain(self, entry_url: str = DEFAULT_ENTRY_URL) -> str:
        """获取乐鱼真实域名。委托给 domain.resolve_domain()。"""
        import os
        from hdata.auth.domain import resolve_domain, DomainCache

        # 1. 缓存
        cache = DomainCache()
        cached = cache.get(entry_url)
        if cached:
            return cached

        # 2. leyu.com HTML（urllib TLS，不依赖 browser-act）
        domain = resolve_domain(entry_url)
        if domain:
            return domain

        # 3. 环境变量兜底
        env = os.getenv("LEYU_DOMAIN", "")
        if env:
            return env

        # 4. browser-act CDP 最后手段
        import re
        await self._ensure_ba()
        port = self._cdp_port
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                r = await s.get(f"http://127.0.0.1:{port}/json/list", timeout=3)
                targets = await r.json()
                for t in targets:
                    if t.get("type") == "page":
                        url = t.get("url", "")
                        m = re.match(r"https://[^/]+", url)
                        if m and "leyu.me" not in url:
                            domain = m.group(0)
                            cache.set(entry_url, domain)
                            return domain
        except Exception:
            pass

        return entry_url.rstrip("/")

    # ── 参数解密 ──────────────────────────────────────────

    @staticmethod
    def _decrypt_params(params_b64: str, ttl: str) -> dict | None:
        """AES-ECB 解密 game params URL。"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        try:
            key = (ttl + "AES").encode("ascii")
            ct = base64.b64decode(params_b64)
            cipher = Cipher(algorithms.AES(key), modes.ECB())
            padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
            return json.loads(padded[: -padded[-1]])
        except Exception as e:
            logger.error(f"[headless] 解密失败: {e}")
            return None
