"""乐鱼 Token 管理器 — 多账号 + 无感浏览器登录 + 逐层降级。

设计原则:
  - 外部只需 TokenManager(account="x").get_token()，内部黑盒
  - 浏览器完全不可见（headless + stealth patches）
  - 多账号通过独立 profile_dir 隔离
  - CaptchaSolver 可注入，换平台只改一行

降级链:
  L0: 缓存 game_token 有效 → 直接返回
  L1: session 有效 → venue/launch API 刷新
  L2: 持久化 profile 有效 → headless 浏览器自动跳转截获
  L3: 无缓存 → headless 完整登录（填表 + 验证码）
  L4: 抛出 TokenUnavailableError

用法:
    from hdt.auth.token_manager import TokenManager
    from hdt.auth.captcha_solver import JfbymSolver

    tm = TokenManager(account="lds003", solver=JfbymSolver(token="xxx"))
    jwt = await tm.get_token()  # 一切自动
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

from htools.utils.logger import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = _PROJ_ROOT / ".cache"
PROFILE_ROOT = CACHE_DIR / "browser_profiles"

AES_KEY = b"ZFRYCMdFYGf0i5HgO0oWvFV0terUABU0"
AES_IV = b"CbE3P3t1lY34Ns8F"


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════


def decode_jwt(token: str) -> dict | None:
    """解码 JWT payload（不验证签名）。模块级函数，兼容旧引用。"""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════


class TokenUnavailableError(RuntimeError):
    """所有降级层级均失败，无法获取 token。

    chain: list of (层级, 操作, 失败原因)
    """

    def __init__(self, account: str, chain: list[tuple[str, str, str]]):
        self.account = account
        self.chain = chain

        lines = [f"[{account}] Token 获取失败，尝试链路:"]
        for level, step, reason in chain:
            indicator = "⚠️ " if "跳过" in reason else "❌"
            lines.append(f"  {level}: {step}")
            lines.append(f"      {indicator} {reason}")
        super().__init__("\n".join(lines))


# ═══════════════════════════════════════════════════════════
# TokenManager
# ═══════════════════════════════════════════════════════════


class TokenManager:
    """多账号 Token 管理器。

    每个实例绑定一个 account，拥有独立的缓存文件和浏览器 profile。
    提供 get_token() 作为唯一对外入口，内部自动降级。

    Attributes:
        account: 账号标识（用于缓存隔离和日志）
    """

    def __init__(self, account: str = "default",
                 solver=None,  # CaptchaSolver | None
                 user: str = "",
                 pwd: str = ""):
        self.account = account

        # CaptchaSolver — 默认用 JfbymSolver（从环境变量读 token）
        if solver is None:
            from hdt.auth.captcha_solver import JfbymSolver
            jfbym_token = os.getenv("JFBYM_TOKEN", "")
            solver = JfbymSolver(api_token=jfbym_token) if jfbym_token else None
        self._solver = solver

        self._user = user or os.getenv("LEYU_USER", "")
        self._pwd = pwd or os.getenv("LEYU_PWD", "")

        self._cache_path = CACHE_DIR / f"{account}.json"
        self._profile_dir = PROFILE_ROOT / account
        self._lock = asyncio.Lock()

    # ── 对外 API ──────────────────────────────────────────

    async def get_token(self, user: str = "", pwd: str = "") -> str:
        """获取有效的游戏 JWT token。内部自动降级。

        Args:
            user: 用户名（覆盖构造时设置的值）
            pwd: 密码（覆盖构造时设置的值）

        Returns:
            game JWT 字符串

        Raises:
            TokenUnavailableError: 所有降级层级均失败
        """
        _user = user or self._user
        _pwd = pwd or self._pwd
        chain: list[tuple[str, str, str]] = []

        async with self._lock:
            cache = self._load()

            # ── L0: 缓存 game_token 有效 ──────────────────
            if cache and self._game_token_valid(cache.get("game_token", "")):
                logger.info(f"[{self.account}] L0 命中: 缓存 game_token 有效")
                self._touch_cache(cache)
                return cache["game_token"]
            if not cache:
                chain.append(("L0 缓存", "读缓存",
                              f"跳过 — {self._cache_path} 不存在"))

            # ── L1: session 有效 → API 刷新 game JWT ──────
            if cache and cache.get("token") and cache.get("domain"):
                try:
                    token = await self._refresh_game_via_api(cache)
                    if token:
                        cache["game_token"] = token
                        self._update_game_meta(cache, token)
                        self._save(cache)
                        logger.info(f"[{self.account}] L1 成功: API 刷新 game JWT")
                        return token
                    chain.append(("L1 API刷新", "venue/launch",
                                  "返回空 token — 签名可能过期或域名变了"))
                except Exception as e:
                    chain.append(("L1 API刷新", "venue/launch", str(e)[:100]))
            elif cache:
                missing = []
                if not cache.get("token"): missing.append("token")
                if not cache.get("domain"): missing.append("domain")
                chain.append(("L1 API刷新", "检查 session",
                              f"跳过 — 缺少 {missing}"))

            # ── L2: 持久化 profile → 浏览器自动刷新 ────────
            try:
                result = await self._refresh_via_headless(cache)
                if result:
                    token = result.get("token", "")
                    if token:
                        cache = cache or {}
                        cache["game_token"] = token
                        cache["domain"] = result.get("domain", cache.get("domain", ""))
                        self._update_game_meta(cache, token)
                        self._save(cache)
                        logger.info(f"[{self.account}] L2 成功: headless 自动刷新")
                        return token
                chain.append(("L2 浏览器刷新", "Playwright 自动跳转",
                              "无 params URL 截获 — browser profile 无有效 session"))
            except Exception as e:
                chain.append(("L2 浏览器刷新", "Playwright",
                              f"不可用: {str(e)[:100]}"))

            # ── L3a: 纯 HTTP 登录（无浏览器）──
            if _user and _pwd and self._solver:
                try:
                    session = await self._login_via_http(_user, _pwd, self._solver)
                    if session and session.get("token"):
                        cache = session.copy()
                        cache.setdefault("signatures", {
                            "/game/api": "60358732c589e34b1211d173273e480d969f457adaa7cca735466145bb336634",
                            "/site/api": "f756f9fa09856322a815c9b5ec2cbb7cdafa3979e65d9339f783b2dc8963aa08",
                        })
                        token = await self._refresh_game_via_api(cache)
                        if token:
                            cache["game_token"] = token
                            self._update_game_meta(cache, token)
                            self._save(cache)
                            logger.info(f"[{self.account}] L3a 成功: 纯 HTTP 登录")
                            return token
                        chain.append(("L3a 纯HTTP登录", "venue/launch",
                                      "纯 HTTP 登录成功但 game JWT 获取失败"))
                    else:
                        chain.append(("L3a 纯HTTP登录", "verify/validate/login",
                                      "verify 失败 — 坐标精度不足"))
                except Exception as e:
                    chain.append(("L3a 纯HTTP登录", "http_login", str(e)[:100]))
            else:
                chain.append(("L3a 纯HTTP登录", "检查凭据",
                              "跳过 — 缺 user/pwd/solver"))

            # ── L3b: 完整 headless 登录 ────────────────────
            if not _user or not _pwd:
                chain.append(("L3b 完整登录", "检查凭据",
                              f"跳过 — user={'***' if _user else '未设置'}, pwd={'***' if _pwd else '未设置'}"))
                raise TokenUnavailableError(self.account, chain)
            if not self._solver:
                chain.append(("L3b 完整登录", "检查打码平台",
                              "跳过 — 未配置 CaptchaSolver (设 JFBYM_TOKEN 或注入)"))
                raise TokenUnavailableError(self.account, chain)

            try:
                session = await self._login_via_headless(_user, _pwd, self._solver)
                cache = session.copy()
                cache.setdefault("signatures", {
                    "/game/api": "60358732c589e34b1211d173273e480d969f457adaa7cca735466145bb336634",
                    "/site/api": "f756f9fa09856322a815c9b5ec2cbb7cdafa3979e65d9339f783b2dc8963aa08",
                })
                token = await self._refresh_game_via_api(cache)
                if not token:
                    chain.append(("L3b 完整登录", "venue/launch",
                                  "登录成功但 game JWT 获取失败 — 签名可能过期"))
                    raise TokenUnavailableError(self.account, chain)
                cache["game_token"] = token
                self._update_game_meta(cache, token)
                self._save(cache)
                logger.info(f"[{self.account}] L3b 成功: headless 完整登录")
                return token
            except TokenUnavailableError:
                raise
            except Exception as e:
                chain.append(("L3b 完整登录", "headless login",
                              str(e)[:150]))
                raise TokenUnavailableError(self.account, chain) from e

    def diagnose(self) -> dict:
        """自诊断：检查所有依赖和状态，返回可操作的修复建议。"""
        import os
        result = {
            "account": self.account,
            "timestamp": int(time.time()),
            "checks": [],
            "issues": [],
            "fixes": [],
        }

        def check(name: str, ok: bool, detail: str, fix: str = ""):
            result["checks"].append({"name": name, "ok": ok, "detail": detail})
            if not ok:
                result["issues"].append(name)
                if fix:
                    result["fixes"].append(fix)

        # 1. 缓存状态
        cache = self._load()
        if cache:
            game_token = cache.get("game_token", "")
            if self._game_token_valid(game_token):
                remaining = self._token_remaining_hours(game_token)
                check("game_token", True, f"有效 (剩余 {remaining:.1f}h)")
            elif cache.get("token"):
                check("game_token", False, "已过期/不存在 — 但 session 可用",
                      "运行 get_token() 自动刷新")
            else:
                check("game_token", False, "不存在且无 session",
                      "运行 get_token() --user X --pwd Y 执行完整登录")
        else:
            check("缓存", False, f"文件不存在: {self._cache_path}",
                  "运行 get_token() --user X --pwd Y 执行完整登录")

        # 2. 域名
        domain_cache = CACHE_DIR / "domain.json"
        domain = ""
        if domain_cache.exists():
            try:
                domain = json.loads(domain_cache.read_text()).get("domain", "")
            except Exception:
                pass
        domain = domain or os.getenv("LEYU_DOMAIN", "")
        if domain:
            src = "缓存" if domain_cache.exists() else "环境变量 LEYU_DOMAIN"
            check("域名", True, f"{domain} (来源: {src})")
        else:
            check("域名", False, "未缓存且未设置 LEYU_DOMAIN",
                  "打开 browser-act 浏览器并访问 leyu.me，或 export LEYU_DOMAIN=...")

        # 3. browser-act
        try:
            port = self._discover_cdp_port()
            import urllib.request
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3)
            ver = json.loads(resp.read()).get("Browser", "")
            check("browser-act", True, f"{ver} @ port {port}")
        except Exception as e:
            check("browser-act", False, f"不可用: {e}",
                  f"运行: browser-act --session leyu2 browser open <id> --headed")

        # 4. jfbym
        if self._solver:
            info = self._solver.info()
            detail = f"{info.name} (type={info.type_code})"
            if hasattr(self._solver, 'get_balance'):
                balance = self._solver.get_balance()
                if balance:
                    detail += f", 余额 ￥{balance}"
                else:
                    detail += ", 余额查询失败"
            check("打码平台", True, detail)
        else:
            check("打码平台", False, "未配置 CaptchaSolver",
                  "设置 JFBYM_TOKEN 环境变量，或注入 JfbymSolver(api_token=...)")

        # 5. 签名
        if cache:
            sigs = cache.get("signatures", {})
            uuid_b64 = cache.get("uuidToBase64", "")
            if sigs:
                sig_keys = list(sigs.keys())
                check("签名", True, f"手动注入 {len(sigs)} 个: {sig_keys}")
            elif uuid_b64:
                try:
                    st = self._decrypt_sign_table(uuid_b64)
                    empty = sum(1 for v in st.values() if not v)
                    check("签名", empty == 0,
                          f"uuidToBase64 解密: {len(st)} 个, {empty} 个为空",
                          "签名表为空 → 运行 --recapture-signatures（待实现）")
                except Exception:
                    check("签名", False, "uuidToBase64 解密失败")
            else:
                check("签名", False, "无 signatures 且无 uuidToBase64")
        else:
            check("签名", None, "无缓存，跳过")

        # 6. 选择器快照
        sel_cache = CACHE_DIR / "selectors.json"
        if sel_cache.exists():
            try:
                sels = json.loads(sel_cache.read_text())
                check("CSS选择器", True, f"快照 {sels.get('updated','?')}")
            except Exception:
                check("CSS选择器", False, "快照损坏",
                      "运行 --update-selectors（待实现）")
        else:
            check("CSS选择器", False, "无快照（使用内置默认值）")

        return result

    @staticmethod
    def _discover_cdp_port() -> int:
        """从运行中的 browser-act 进程自动发现 CDP 端口。"""
        import subprocess, re, os
        port_env = os.getenv("LEYU_CDP_PORT", "")
        if port_env and port_env.isdigit():
            return int(port_env)
        try:
            result = subprocess.run(
                ["ps", "aux"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split('\n'):
                if 'BrowserAct' in line and 'remote-debugging-port' in line:
                    m = re.search(r'remote-debugging-port=(\d+)', line)
                    if m:
                        return int(m.group(1))
        except Exception:
            pass
        return 57073

    def health(self) -> dict:
        """返回当前 token 状态（同步，不触发登录）。"""
        cache = self._load()
        if not cache:
            return {"account": self.account, "state": "empty", "token_remaining": "0h"}

        game_token = cache.get("game_token", "")
        if self._game_token_valid(game_token):
            remaining = self._token_remaining_hours(game_token)
            return {"account": self.account, "state": "ok",
                    "token_remaining": f"{remaining:.1f}h",
                    "login_method": cache.get("login_method", "unknown")}

        if cache.get("token"):
            return {"account": self.account, "state": "session_ok",
                    "token_remaining": "0h (需刷新)"}

        return {"account": self.account, "state": "expired", "token_remaining": "0h"}

    # ── L1: API 刷新 ─────────────────────────────────────

    async def _refresh_game_via_api(self, session: dict) -> str | None:
        """调用 venue/launch API 获取游戏 JWT。"""
        from curl_cffi import requests

        domain = session.get("domain", "")
        if not domain:
            return None

        url = f"{domain}/game/api/v1/venue/launch"
        headers = self._api_headers(session, url)

        resp = requests.post(url, headers=headers, json={"enName": "YBZR"},
                             impersonate="chrome110", timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[{self.account}] venue/launch 返回 {resp.status_code}: {resp.text[:200]}")
            return None

        try:
            data = resp.json()
            game_url = data.get("data", {}).get("url", "")
        except Exception:
            return None

        if not game_url or "params=" not in game_url:
            return None

        # 提取 params 和 ttl
        from urllib.parse import urlparse, unquote
        parsed = urlparse(game_url)
        qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
        params_b64 = qs.get("params", "")
        ttl = qs.get("ttl", "")

        if not params_b64 or not ttl:
            return None

        # AES-ECB 解密
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        key = (ttl + "AES").encode("ascii")
        raw = unquote(params_b64)
        raw = raw + "=" * (4 - len(raw) % 4)
        ct = base64.b64decode(raw)
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
        decrypted = json.loads(padded[: -padded[-1]])

        return decrypted.get("token", "")

    # ── L2: headless 自动刷新 ────────────────────────────

    async def _refresh_via_headless(self, cache: dict | None) -> dict | None:
        """用持久化 browser profile 自动跳转截获 JWT。"""
        from hdt.auth.headless_login import HeadlessLogin

        hl = HeadlessLogin(account=self.account, profile_dir=self._profile_dir)
        domain = (cache or {}).get("domain", "")
        entry = f"{domain}/" if domain else "https://leyu.me"

        result = await hl.refresh_jwt(entry_url=entry, timeout=45.0)
        if not result:
            return None

        token = result.get("token", "")
        if not token:
            return None

        backend = result.get("backendDomainUrl", "")
        if not backend:
            backend = result.get("backendDomainUrlList", "").split(",")[0].strip()

        return {
            "token": token,
            "player_id": result.get("playerId", 0),
            "backend": backend,
            "domain": domain or "",
        }

    # ── L3: headless 完整登录 ─────────────────────────────

    async def _login_via_headless(self, user: str, pwd: str, solver) -> dict:
        """完整 headless 登录流程。"""
        from hdt.auth.headless_login import HeadlessLogin

        hl = HeadlessLogin(account=self.account, profile_dir=self._profile_dir)
        return await hl.full_login(user=user, pwd=pwd, solver=solver)

    # ── L3a: 纯 HTTP 登录 ────────────────────────────────

    async def _login_via_http(self, user: str, pwd: str, solver) -> dict | None:
        """纯 HTTP 登录（无浏览器）。

        流程: fetch_captcha → solver → generate_w → verify → validate → login

        注意: verify 依赖坐标精度，当前 jfbym 坐标约 ±20px 偏移。
        如果 verify 持续返回 result=fail，可尝试 Capsolver 等替代打码平台。
        """
        from hdt.auth.captcha import fetch_captcha
        from hdt.auth.captcha_solver import CaptchaChallenge
        from hdt.auth.geetest_signer import generate_w
        from curl_cffi import requests as cr
        import hashlib, urllib.parse, re

        CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"

        # 1. 获取验证码
        ld = fetch_captcha()
        if not ld:
            logger.error(f"[{self.account}] fetch_captcha 失败")
            return None

        # 2. 坐标识别
        challenge = CaptchaChallenge(
            lot_number=ld["lot_number"], payload=ld["payload"],
            process_token=ld["process_token"], bg_url=ld["bg_url"],
            ques_urls=ld["ques_urls"], captcha_id=CAPTCHA_ID,
        )
        try:
            solution = await solver.solve(challenge)
        except Exception as e:
            logger.error(f"[{self.account}] solver 失败: {e}")
            return None

        # 3. 生成 w
        w = generate_w(ld, CAPTCHA_ID, solution.coords)

        # 4. verify
        cb = f"botion_{int(time.time() * 1000)}"
        params = {
            "callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
            "lot_number": ld["lot_number"], "payload": ld["payload"],
            "process_token": ld["process_token"],
            "payload_protocol": ld.get("payload_protocol", "1"),
            "pt": ld.get("pt", "1"), "w": w,
        }
        url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
        resp = cr.get(url, impersonate="chrome110",
                      headers={"Referer": "https://www.leyu.me/"}, timeout=30)
        text = resp.text

        m = re.search(r"\((.*)\)$", text, re.DOTALL)
        if not m:
            logger.error(f"[{self.account}] verify 响应解析失败")
            return None
        vdata = json.loads(m.group(1))
        if vdata.get("data", {}).get("result") != "success":
            logger.warning(f"[{self.account}] verify 失败: {vdata.get('data', {}).get('result')}")
            return None

        seccode = vdata.get("data", {}).get("seccode", {})

        # 5. validateGeeCheckV2
        domain = await self._resolve_domain()
        if not domain:
            return None
        validate_url = f"{domain}/site/api/v1/user/member/validateGeeCheckV2"
        validate_body = {
            "validate_way": 1,
            "lot_number": ld["lot_number"],
            "captcha_output": seccode.get("captcha_output", ""),
            "gen_time": seccode.get("gen_time", ""),
            "pass_token": seccode.get("pass_token", ""),
        }
        resp = cr.post(validate_url, json=validate_body,
                       headers={"Content-Type": "application/json",
                                "Referer": f"{domain}/"},
                       impersonate="chrome110", timeout=15)
        vresp = resp.json()
        if vresp.get("status_code") != 6000:
            logger.error(f"[{self.account}] validateGeeCheckV2 失败: {vresp}")
            return None

        # 6. login
        pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()
        login_body = {
            "name": user, "password": pwd_md5,
            "Kaptchcate": 0, "codeId": ld["lot_number"],
        }
        resp = cr.post(f"{domain}/site/api/v1/user/login", json=login_body,
                       headers={"Content-Type": "application/json",
                                "Referer": f"{domain}/"},
                       impersonate="chrome110", timeout=15)
        lresp = resp.json()
        token = (lresp.get("data", {}) or {}).get("token", "")
        if lresp.get("status_code") == 6000 and token:
            logger.info(f"[{self.account}] 纯 HTTP 登录成功")
            return {"token": token, "domain": domain, "lot_number": ld["lot_number"]}

        logger.error(f"[{self.account}] 登录失败: {lresp.get('message', '')}")
        return None

    async def _resolve_domain(self) -> str | None:
        """解析乐鱼域名。"""
        from hdt.auth.domain import resolve_domain, DomainCache
        cache = DomainCache()
        cached = cache.get()
        if cached:
            return cached
        domain = resolve_domain()
        if domain:
            return domain
        return os.getenv("LEYU_DOMAIN", None)

    # ── 缓存管理 ──────────────────────────────────────────

    def _cache_path_for(self, account: str) -> Path:
        """返回指定账号的缓存路径（兼容旧代码）。"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return CACHE_DIR / f"{account}.json"

    def _load(self) -> dict | None:
        """读取缓存，自动清理损坏文件。"""
        if not self._cache_path.exists():
            return None
        try:
            data = json.loads(self._cache_path.read_text())
            # 基本校验
            if not isinstance(data, dict):
                raise ValueError("缓存不是 dict")
            return data
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning(f"[{self.account}] 缓存损坏 ({e})，自动清理")
            self._cache_path.unlink(missing_ok=True)
            return None

    def _save(self, data: dict):
        """写入缓存。自动注入已知 API 签名（新域名签名表为空时的兜底）。"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = int(time.time())
        data["account"] = self.account
        # 注入已知签名（新域名签名表为空时的兜底，从 BrowserAct 网络捕获）
        if not data.get("signatures"):
            data["signatures"] = {
                "/game/api": "60358732c589e34b1211d173273e480d969f457adaa7cca735466145bb336634",
                "/site/api": "f756f9fa09856322a815c9b5ec2cbb7cdafa3979e65d9339f783b2dc8963aa08",
            }
        self._cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _touch_cache(self, cache: dict):
        """更新缓存时间戳（不阻塞的轻量写入）。"""
        cache["updated_at"] = int(time.time())
        try:
            self._cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
        except OSError:
            pass  # 写入失败不影响返回 token

    def _update_game_meta(self, cache: dict, token: str):
        """从 JWT 中提取 player_id 和 backend 写入缓存。"""
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                pb = parts[1] + "=" * (4 - len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(pb))
                sub = payload.get("sub", {})
                if isinstance(sub, dict):
                    cache["game_player_id"] = sub.get("playerId", 0)
                cache["game_exp"] = payload.get("exp", 0)
        except Exception:
            pass
        cache["login_method"] = "headless"

    # ── JWT 校验 ─────────────────────────────────────────

    @staticmethod
    def _game_token_valid(token: str) -> bool:
        """检查 game JWT 是否还有 >1h 有效期。"""
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

    @staticmethod
    def _token_remaining_hours(token: str) -> float:
        """返回 token 剩余有效时间（小时）。"""
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return 0.0
            pb = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(pb))
            return max(0.0, (payload.get("exp", 0) - time.time()) / 3600)
        except Exception:
            return 0.0

    # ── API 签名头 ───────────────────────────────────────

    @staticmethod
    def _decrypt_sign_table(b64: str) -> dict[str, str]:
        """AES-CBC 解密签名表。"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        ct = base64.b64decode(b64)
        cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ct) + decryptor.finalize()
        return json.loads(padded[: -padded[-1]])

    def _api_headers(self, session: dict, url: str) -> dict:
        """构造乐鱼 API 请求头（含 X-API-XXX 签名）。"""
        # 手动注入签名优先
        manual_sigs = session.get("signatures", {})
        xxx = ""
        for k in sorted(manual_sigs.keys(), key=lambda x: -len(x)):
            if k in url:
                xxx = manual_sigs[k]
                break

        # 兜底：从 uuidToBase64 解密签名表
        if not xxx:
            uuid_b64 = session.get("uuidToBase64", "")
            if uuid_b64:
                try:
                    st = self._decrypt_sign_table(uuid_b64)
                    xxx = next((v for k, v in sorted(st.items(),
                                key=lambda x: -len(x[0])) if k in url), "")
                except Exception:
                    pass

        return {
            "X-API-TOKEN": session.get("token", ""),
            "X-API-UUID": session.get("uuid", ""),
            "X-API-XXX": xxx,
            "X-API-CLIENT": "web",
            "X-API-SITE": "2001",
            "X-API-VERSION": "2.0.0",
            "Content-Type": "application/json",
            "Referer": session.get("domain", "") + "/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "Cookie": session.get("cookies", ""),
        }


# ═══════════════════════════════════════════════════════════
# CLI（保持兼容）
# ═══════════════════════════════════════════════════════════


async def main():
    import argparse

    p = argparse.ArgumentParser(description="乐鱼 Token 管理器")
    p.add_argument("--account", default="default", help="账号名")
    p.add_argument("--user", help="用户名")
    p.add_argument("--pwd", help="密码")
    p.add_argument("--jfbym", dest="jfbym_token", help="jfbym API token")
    p.add_argument("--status", action="store_true", help="查看状态")
    p.add_argument("--health", action="store_true", help="健康检查")
    p.add_argument("--diagnose", action="store_true", help="自诊断")
    p.add_argument("--recapture-signatures", action="store_true", help="重新捕获 API 签名")
    p.add_argument("--resolve-domain", nargs="?", const="", help="获取真实域名并缓存。可选指定入口 URL")
    args = p.parse_args()

    from hdt.auth.captcha_solver import JfbymSolver

    solver = JfbymSolver(api_token=args.jfbym_token) if args.jfbym_token else None
    tm = TokenManager(account=args.account, solver=solver,
                      user=args.user or "", pwd=args.pwd or "")

    if args.resolve_domain is not None:
        from hdt.auth.domain import resolve_domain as do_resolve
        entry = args.resolve_domain if args.resolve_domain else ""
        domain = do_resolve(entry)
        if domain:
            print(f"✅ 域名: {domain}")
            return 0
        else:
            print(f"❌ 域名解析失败"
                  + (f" (入口: {entry})" if entry else ""))
            return 1

    if args.recapture_signatures:
        from hdt.auth.signature_recapture import recapture_signatures
        try:
            sigs = await recapture_signatures(args.account)
            if sigs:
                print(f"✅ 捕获 {len(sigs)} 个签名: {list(sigs.keys())}")
                return 0
            else:
                print("❌ 未捕获到签名 — browser-act 是否已打开且有已登录的页面?")
                return 1
        except Exception as e:
            print(f"❌ {e}")
            return 1

    if args.diagnose:
        d = tm.diagnose()
        print("=" * 60)
        print(f"  诊断报告: {d['account']}")
        print("=" * 60)
        for c in d["checks"]:
            status = "✅" if c["ok"] else ("❌" if c["ok"] is False else "⬜")
            print(f"  {status} {c['name']}: {c['detail']}")
        if d["issues"]:
            print(f"\n  ⚠️  问题 ({len(d['issues'])}):")
            for issue in d["issues"]:
                print(f"    - {issue}")
        if d["fixes"]:
            print(f"\n  🔧 修复建议:")
            for fix in d["fixes"]:
                print(f"    → {fix}")
        return 0 if not d["issues"] else 1

    if args.status or args.health:
        h = tm.health()
        print(json.dumps(h, indent=2, ensure_ascii=False))
        return 0

    try:
        import sys
        token = await tm.get_token(user=args.user or "", pwd=args.pwd or "")
        if token:
            print(f"✅ [{args.account}] {token[:80]}...")
            h = tm.health()
            print(f"   状态: {h['state']}, 剩余: {h['token_remaining']}")
        else:
            print(f"❌ [{args.account}] get_token 返回 None", file=__import__('sys').stderr)
            return 1
    except TokenUnavailableError as e:
        print(f"❌ [{args.account}] {e}", file=__import__('sys').stderr)
        return 1

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
