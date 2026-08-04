"""LoginOrchestrator — TokenManager 全部实现（Task 9 拆出）。

从原 hdata.auth.token_manager.TokenManager 整类迁入（除 _login_via_http、
静态 _decrypt_sign_table）。TokenManager 现为薄门面委托本类。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from htools.utils.logger import get_logger

from hdata.auth.api_sign import get_uuid
from hdata.auth.captcha_client import http_login_with_captcha
from hdata.auth.captcha_solver import JfbymSolver
from hdata.auth.domain import resolve_domain
from hdata.auth.fingerprint import get_ua
from hdata.auth.headers import resolve_api_xxx
from hdata.auth.params import (
    decode_jwt as _decode_jwt,
)
from hdata.auth.params import (
    token_remaining_hours as _token_remaining_hours,
)
from hdata.auth.params import (
    validate_game_token as _validate_game_token,
)
from hdata.auth.sign_table import decrypt_sign_table
from hdata.paths import cache_dir as _cache_dir

logger = get_logger("hdata.auth.token_manager")

# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

CACHE_DIR = _cache_dir()
PROFILE_ROOT = CACHE_DIR / "browser_profiles"


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
# LoginOrchestrator
# ═══════════════════════════════════════════════════════════


class LoginOrchestrator:
    """多账号 Token 管理器实现体（原 TokenManager 全部逻辑）。

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
            # 编排回环:保持函数内导入以避免 import 死锁,见 P4 拆 orchestrator 方案
            from hdata.auth.session import SessionError, get_game_session

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
                    session = await http_login_with_captcha(self.account, _user, _pwd, self._solver)
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
                    st = decrypt_sign_table(uuid_b64)
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
        # 编排回环:保持函数内导入以避免 import 死锁,见 P4 拆 orchestrator 方案
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
            # 编排回环:保持函数内导入以避免 import 死锁,见 P4 拆 orchestrator 方案
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

    async def _resolve_domain(self) -> str | None:
        """解析乐鱼域名(委托 domain.resolve_domain,失败回退 env)。"""
        return resolve_domain() or os.getenv("HDATA_DOMAIN", None)

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

    def _api_headers(self, session: dict, url: str) -> dict:
        """构造乐鱼 API 请求头（含 X-API-XXX 签名）。"""
        # 签名链（手动注入签名 → uuidToBase64 解密）抽到 headers.resolve_api_xxx，
        # enable_wasm=False 保留本版原无 wasm 层的行为差异
        xxx = resolve_api_xxx(session, url, enable_wasm=False)

        account = session.get("account", "") or self.account
        uuid_val = session.get("uuid", "")
        ua_val = ""
        if account:
            try:
                uuid_val = get_uuid(account) or uuid_val
                ua_val = get_ua(account)
            except Exception:
                pass
        if not ua_val:
            try:
                ua_val = get_ua("")
            except Exception:
                ua_val = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                )

        return {
            "X-API-TOKEN": session.get("token", ""),
            "X-API-UUID": uuid_val,
            "X-API-XXX": xxx,
            "X-API-CLIENT": "web",
            "X-API-SITE": "2001",
            "X-API-VERSION": "2.0.0",
            "Content-Type": "application/json",
            "Referer": session.get("domain", "") + "/",
            "User-Agent": ua_val,
            "Cookie": session.get("cookies", ""),
        }
