"""乐鱼 Token 管理器 — 多账号 + 无感浏览器登录 + 逐层降级。

设计原则:
  - 外部只需 TokenManager(account="x").get_token()，内部黑盒
  - 浏览器完全不可见（headless + stealth patches）
  - 多账号通过独立 profile_dir 隔离
  - CaptchaSolver 可注入，换平台只改一行

降级链:
  L0: 缓存 game_token 有效 → 直接返回
  L1: session 有效 → venue/launch API 刷新
    L2: 持久化 profile 有效 → Playwright 自动跳转截获
    L3: 无缓存 → 纯 HTTP 登录（验证码）
  L4: 抛出 TokenUnavailableError

用法:
    from hdata.auth.token_manager import TokenManager
    from hdata.auth.captcha_solver import JfbymSolver

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

from hdata.auth.params import (
    decode_jwt as _decode_jwt,
    decrypt_params as _decrypt_params,
    validate_game_token as _validate_game_token,
    token_remaining_hours as _token_remaining_hours,
    extract_params_from_url as _extract_params_from_url,
)
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
    """解码 JWT payload（不验证签名）。兼容旧引用，委托给 params.py。"""
    return _decode_jwt(token)


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
            from hdata.auth.captcha_solver import JfbymSolver
            jfbym_token = os.getenv("JFBYM_TOKEN", "")
            solver = JfbymSolver(api_token=jfbym_token) if jfbym_token else None
        self._solver = solver

        if user:
            self._user = user
        else:
            self._user = os.getenv("HDATA_USER", "")
            logger.debug("HDATA_USER not provided, using environment variable")
        if pwd:
            self._pwd = pwd
        else:
            self._pwd = os.getenv("HDATA_PWD", "")
            logger.debug("HDATA_PWD not provided, using environment variable")

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
            # 优先使用 session.py 的降级逻辑（L0 缓存 → L1 API）
            from hdata.auth.session import get_game_session, SessionError

            try:
                session = await get_game_session(self.account)
                game_token = session["game_token"]
                # 回写到本地的 WS-only 缓存
                cache = self._load() or {}
                cache["game_token"] = game_token
                cache["game_player_id"] = session.get("game_player_id", 0)
                cache["game_backend"] = session.get("game_backend", "")
                cache["game_exp"] = session.get("game_exp", 0)
                self._save(cache)
                return game_token
            except SessionError:
                chain.append(("L0/L1", "session.get_game_session",
                              "缓存无效且无完整 session 可刷新"))

            # ── L2: 持久化 profile → 浏览器自动刷新 ────────
            try:
                result = await self._refresh_via_headless(cache)
                if result:
                    token = result.get("game_token", "")
                    if token:
                        cache = cache or {}
                        cache["game_token"] = token
                        cache["game_player_id"] = int(result.get("game_player_id", 0) or 0)
                        cache["game_backend"] = result.get("game_backend", "")
                        cache["game_exp"] = int(result.get("game_exp", 0) or 0)
                        cache["source"] = result.get("source", "playwright")
                        self._update_game_meta(cache, token)
                        self._save(cache)
                        logger.info(f"[{self.account}] L2 成功: headless 自动刷新")
                        return token
                chain.append(("L2 浏览器刷新", 
                              "Playwright 自动跳转",
                              "无 params URL 截获 — browser profile 无有效 session"))
            except Exception as e:
                chain.append(("L2 浏览器刷新", 
                              "Playwright",
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
                        chain.append(("L3a 纯HTTP登录", 
                                      "venue/launch",
                                      "纯 HTTP 登录成功但 game JWT 获取失败"))
                    else:
                        chain.append(("L3a 纯HTTP登录", 
                                      "verify/validate/login",
                                      "verify 失败 — 坐标精度不足"))
                except Exception as e:
                    chain.append(("L3a 纯HTTP登录", 
                                  "http_login", 
                                  str(e)[:100]))
            else:
                chain.append(("L3a 纯HTTP登录", 
                              "检查凭据",
                              "跳过 — 缺 user/pwd/solver"))

            chain.append(("L3b 浏览器登录", 
                          "已移除",
                          "browser-act 相关链路已移除，请使用 --manual-capture 人工辅助登录"))
            raise TokenUnavailableError(self.account, chain)

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

        def check(name: str, ok: bool | None, detail: str, fix: str = ""):
            result["checks"].append({"name": name, "ok": ok, "detail": detail})
            if not ok:
                result["issues"].append(name)
                if fix:
                    result["fixes"].append(fix)

        # 1. 缓存状态
        cache = self._load()
        if cache:
            game_token = cache.get("game_token", "")
            if _validate_game_token(game_token):
                remaining = _token_remaining_hours(game_token)
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
        domain = domain or os.getenv("HDATA_DOMAIN", "")
        if domain:
            src = "缓存" if domain_cache.exists() else "环境变量 HDATA_DOMAIN"
            check("域名", True, f"{domain} (来源: {src})")
        else:
            check("域名", False, "未缓存且未设置 HDATA_DOMAIN",
                  "访问 leyu.me 完成一次登录，或设置 HDATA_DOMAIN=https://...")

        # 3. Playwright
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            check("Playwright", True, "已安装")
        except Exception as e:
            check("Playwright", False, f"不可用: {e}",
                  "运行: uv run playwright install chromium")

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

    def health(self) -> dict:
        """返回当前 token 状态（同步，不触发登录）。"""
        cache = self._load()
        if not cache:
            return {"account": self.account, "state": "empty", "token_remaining": "0h"}

        game_token = cache.get("game_token", "")
        if _validate_game_token(game_token):
            remaining = _token_remaining_hours(game_token)
            return {"account": self.account, "state": "ok",
                    "token_remaining": f"{remaining:.1f}h",
                    "login_method": cache.get("login_method", "unknown")}

        if cache.get("token"):
            return {"account": self.account, "state": "session_ok",
                    "token_remaining": "0h (需刷新)"}

        return {"account": self.account, "state": "expired", "token_remaining": "0h"}

    # ── L1: API 刷新 ─────────────────────────────────────

    async def _refresh_game_via_api(self, session: dict) -> str | None:
        """调用 venue/launch API 获取游戏 JWT。委托给 session.py。"""
        from hdata.auth.session import refresh_game_token

        try:
            token = await refresh_game_token(self.account, session)
            return token
        except Exception as e:
            logger.warning(f"[{self.account}] _refresh_game_via_api 失败: {e}")
            return None

    # ── L2: Playwright 自动刷新 ─────────────────────────

    async def _refresh_via_headless(self, cache: dict | None) -> dict | None:
        """用持久化 browser profile 自动刷新 JWT（Playwright）。"""
        domain = (cache or {}).get("domain", "")
        entry = f"{domain}/" if domain else "https://leyu.me"
        return await self._refresh_via_playwright(entry_url=entry, headless=True)

    async def _refresh_via_playwright(self, entry_url: str, headless: bool = True) -> dict | None:
        """使用 Playwright 持久化 profile 刷新 game token。"""
        try:
            from hdata.auth.browser_login import GameBrowserLogin
        except Exception:
            return None

        auth_cache_path = CACHE_DIR / f"{self.account}.auth_cache.json"

        # 通过 GameBrowserLogin 封装的 Playwright 自动化登录流程获取 game token
        bot = GameBrowserLogin(
            entry_url=entry_url,
            headless=headless,
            profile_dir=self._profile_dir,
            auth_cache_path=auth_cache_path,
        )
        decrypted = await bot.run()
        if not decrypted:
            return None

        token = decrypted.get("game_token", "")
        if not token:
            return None

        player_id = int(decrypted.get("game_player_id", 0) or 0)
        backend = decrypted.get("game_backend", "")
        if not backend:
            return None

        game_exp = int(decrypted.get("game_exp") or 0)
        if not game_exp:
            jwt = _decode_jwt(token)
            if jwt:
                game_exp = int(jwt.get("exp", 0) or 0)

        return {
            "game_token": token,
            "game_player_id": player_id,
            "game_backend": backend,
            "game_exp": game_exp,
            "source": "playwright",
        }

    async def manual_capture(self, entry_url: str = "https://leyu.me") -> str | None:
        """打开可见浏览器，人工完成登录后抓取 game token。"""
        result = await self._refresh_via_playwright(entry_url=entry_url, headless=False)
        if not result or not result.get("game_token"):
            return None

        self._save(result)
        return result["game_token"]

    def inject_tokens(
        self,
        game_token: str = "",
        game_player_id: int = 0,
        game_backend: str = "",
        game_exp: int = 0,
        source: str = "inject",
    ) -> dict:
        """注入当前最新认证快照。"""
        if not game_token:
            raise ValueError("game_token 不能为空")
        cache: dict = {
            "game_token": game_token,
            "game_player_id": int(game_player_id or 0),
            "game_backend": game_backend,
            "game_exp": int(game_exp or 0),
            "source": source,
        }
        if not cache["game_exp"]:
            jwt = _decode_jwt(game_token)
            if jwt:
                cache["game_exp"] = int(jwt.get("exp", 0) or 0)
        self._save(cache)
        return cache

    def import_token_file(self, file_path: str) -> dict:
        """从外部 JSON 文件导入当前最新 WS-only 认证快照。"""
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("token 文件必须是 JSON object")

        allowed = {
            "game_token", "game_player_id", "game_backend",
            "game_exp", "backend_domain_url_list", "device_id",
            "domain", "token", "uuid", "uuidToBase64", "cookies",
            "signatures", "source", "updated_at", "account",
        }
        unknown = set(data.keys()) - allowed
        if unknown:
            raise ValueError(f"不支持的字段: {sorted(unknown)}")

        game_token = data.get("game_token", "")
        if not game_token:
            raise ValueError("token 文件缺少 game_token")

        game_player_id = int(data.get("game_player_id", 0) or 0)
        game_backend = data.get("game_backend", "")
        game_exp = int(data.get("game_exp", 0) or 0)
        source = data.get("source", "import")

        return self.inject_tokens(
            game_token=game_token,
            game_player_id=game_player_id,
            game_backend=game_backend,
            game_exp=game_exp,
            source=source,
        )

    # ── L3a: 纯 HTTP 登录 ────────────────────────────────

    async def _login_via_http(self, user: str, pwd: str, solver) -> dict | None:
        """纯 HTTP 登录（无浏览器）。

        流程: fetch_captcha → solver → generate_w → verify → validate → login

        注意: verify 依赖坐标精度，当前 jfbym 坐标约 ±20px 偏移。
        如果 verify 持续返回 result=fail，可尝试 Capsolver 等替代打码平台。
        """
        from hdata.auth.captcha import fetch_captcha
        from hdata.auth.captcha_solver import CaptchaChallenge
        from hdata.auth.geetest_signer import generate_w
        from curl_cffi import requests as cr
        import hashlib, urllib.parse, re

        CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"

        # 1. 获取验证码
        ld = fetch_captcha()
        if not ld:
            logger.error(f"[{self.account}] fetch_captcha failed")
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
            logger.error(f"[{self.account}] solver failed: {e}")
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
            logger.error(f"[{self.account}] verify response parsing failed")
            return None
        vdata = json.loads(m.group(1))
        if vdata.get("data", {}).get("result") != "success":
            logger.warning(f"[{self.account}] verify failed: {vdata.get('data', {}).get('result')}")
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
            logger.error(f"[{self.account}] validateGeeCheckV2 failed: {vresp}")
            return None

        # 6. login
        pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()
        login_body = {
            "name": user,
            "password": pwd_md5,
            "Kaptchcate": 0,
            "codeId": ld["lot_number"],
        }
        resp = cr.post(f"{domain}/site/api/v1/user/login",
                       json=login_body,
                       headers={"Content-Type": "application/json",
                                "Referer": f"{domain}/"},
                       impersonate="chrome110", timeout=15)
        lresp = resp.json()
        token = (lresp.get("data", {}) or {}).get("token", "")
        if lresp.get("status_code") == 6000 and token:
            logger.info(f"[{self.account}] HTTP login successful")
            return {"token": token, "domain": domain, "lot_number": ld["lot_number"]}

        logger.error(f"[{self.account}] login failed: {lresp.get('message', '')}")
        return None

    async def _resolve_domain(self) -> str | None:
        """解析乐鱼域名。"""
        from hdata.auth.domain import resolve_domain, DomainCache
        cache = DomainCache()
        cached = cache.get()
        if cached:
            return cached
        domain = resolve_domain()
        if domain:
            return domain
        return os.getenv("HDATA_DOMAIN", None)

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
        """写入缓存。保存 game 字段 + session 字段。"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        token = data.get("game_token", "")
        payload: dict = {
            "game_token": token,
            "game_player_id": int(data.get("game_player_id", 0) or 0),
            "game_backend": data.get("game_backend", ""),
            "game_exp": int(data.get("game_exp", 0) or 0),
            "backend_domain_url_list": data.get("backend_domain_url_list", ""),
            "device_id": data.get("device_id", ""),
            "domain": data.get("domain", ""),
            "token": data.get("token", ""),
            "uuid": data.get("uuid", ""),
            "uuidToBase64": data.get("uuidToBase64", ""),
            "cookies": data.get("cookies", ""),
            "signatures": data.get("signatures", {}),
            "source": data.get("source", "manual_capture"),
            "updated_at": int(time.time()),
            "account": self.account,
        }
        if not payload["game_exp"] and token:
            jwt = _decode_jwt(token)
            if jwt:
                payload["game_exp"] = int(jwt.get("exp", 0) or 0)
        # 清理空值，保持文件干净
        payload = {k: v for k, v in payload.items() if v or v == 0}
        self._cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    def _touch_cache(self, cache: dict):
        """更新缓存时间戳（不阻塞的轻量写入）。"""
        cache["updated_at"] = int(time.time())
        try:
            self._cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
        except OSError:
            pass  # 写入失败不影响返回 token

    def _update_game_meta(self, cache: dict, token: str):
        """从 JWT 中提取 player_id 和 backend 写入缓存。"""
        jwt = _decode_jwt(token)
        if jwt:
            sub = jwt.get("sub", {})
            if isinstance(sub, dict):
                cache["game_player_id"] = sub.get("playerId", 0)
            cache["game_exp"] = jwt.get("exp", 0)
        cache.setdefault("source", "headless")

    # ── JWT 校验（委托给 params.py）───────────────

    @staticmethod
    def _game_token_valid(token: str) -> bool:
        """检查 game JWT 是否还有 >1h 有效期。委托给 params.py。"""
        return _validate_game_token(token)

    @staticmethod
    def _token_remaining_hours(token: str) -> float:
        """返回 token 剩余有效时间（小时）。委托给 params.py。"""
        return _token_remaining_hours(token)

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

    p = argparse.ArgumentParser(description="游戏 Token 管理器")
    p.add_argument("--account", default="default", help="账号名")
    p.add_argument("--user", help="用户名")
    p.add_argument("--pwd", help="密码")
    p.add_argument("--jfbym", dest="jfbym_token", help="jfbym API token")
    p.add_argument("--status", action="store_true", help="查看状态")
    p.add_argument("--health", action="store_true", help="健康检查")
    p.add_argument("--diagnose", action="store_true", help="自诊断")
    p.add_argument("--manual-capture", nargs="?", const="", help="可见浏览器手动登录并抓取 game token，可选入口 URL")
    p.add_argument("--import-token-file", help="从 JSON 文件导入外部提供的 token/session")
    p.add_argument("--inject-game-token", help="直接注入 game JWT（外部提供）")
    p.add_argument("--inject-player-id", type=int, help="注入 game_player_id")
    p.add_argument("--inject-backend", help="注入 game_backend，例如 txdzbjc.com:18034")
    p.add_argument("--inject-game-exp", type=int, help="注入 game_exp（Unix 时间戳）")
    p.add_argument("--inject-source", default="inject", help="注入来源标记")
    args = p.parse_args()

    from hdata.auth.captcha_solver import JfbymSolver

    solver = JfbymSolver(api_token=args.jfbym_token) if args.jfbym_token else None
    tm = TokenManager(account=args.account, 
                      solver=solver,
                      user=args.user or "", 
                      pwd=args.pwd or "")

    if args.import_token_file:
        try:
            cache = tm.import_token_file(args.import_token_file)
            print(f"✅ [{args.account}] 已导入 token 文件 -> {tm._cache_path}")
            print(f"   game_token={'有' if cache.get('game_token') else '无'}; player_id={cache.get('game_player_id', 0)}")
            return 0
        except Exception as e:
            print(f"❌ 导入失败: {e}")
            return 1

    if args.inject_game_token or args.inject_player_id is not None or args.inject_backend or args.inject_game_exp is not None:
        cache = tm.inject_tokens(
            game_token=args.inject_game_token or "",
            game_player_id=args.inject_player_id or 0,
            game_backend=args.inject_backend or "",
            game_exp=args.inject_game_exp or 0,
            source=args.inject_source,
        )
        print(f"✅ [{args.account}] 注入成功 -> {tm._cache_path}")
        print(f"   game_token={'有' if cache.get('game_token') else '无'}; player_id={cache.get('game_player_id', 0)}")
        return 0

    if args.manual_capture is not None:
        entry = args.manual_capture or "https://leyu.me"
        token = await tm.manual_capture(entry_url=entry)
        if token:
            logger.info(f"[{args.account}] manual capture success: {token[:80]}...")
            return 0
        logger.error(f"[{args.account}] manual capture failed")
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
