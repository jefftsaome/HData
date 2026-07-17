"""多账号参数管理层 — 域名/会话/Token/WS 配置的统一获取。

本模块封装了从域名解析到 WS 连接配置的完整参数链。
每个函数单一职责：获取特定参数，失败时抛具体异常。

用法:
    from hdata.auth.session import get_game_session, build_ws_config

    # 获取完整游戏会话
    session = await get_game_session("lidongsen1")
    # session = {game_token, player_id, backend_domain_url, backend_domain_url_list}

    # 构造 WS 连接配置
    ws_cfg = build_ws_config(session)
    # ws_cfg = {ws_url, host, port, player_id, jwt_token}
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from curl_cffi import requests

from hdata.auth.domain import resolve_domain, DomainCache
from hdata.auth.params import (
    decode_jwt,
    decrypt_params,
    extract_params_from_url,
    validate_game_token,
)
from htools.utils.logger import get_logger

logger = get_logger(__name__)

_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = _PROJ_ROOT / ".cache"

# WS 端口（当前写死，未来可能从 backendDomainUrlList 动态获取）
DEFAULT_WS_PORT = 18026


# ── 异常 ──────────────────────────────────────────────


class SessionError(RuntimeError):
    """会话相关错误的基类。"""


class DomainError(SessionError):
    """域名解析失败。"""


class TokenRefreshError(SessionError):
    """Token 刷新失败。"""


def _refresh_error(
    stage: str,
    *,
    status: int | None = None,
    exc: BaseException | None = None,
) -> TokenRefreshError:
    fields = [f"stage={stage}"]
    if status is not None:
        fields.append(f"status={status}")
    if exc is not None:
        fields.append(f"exception={type(exc).__name__}")
    return TokenRefreshError("refresh " + " ".join(fields))


# ── 域名 ──────────────────────────────────────────────


def get_real_domain(entry_url: str = "") -> str:
    """从集团入口站获取真实主站域名。

    缓存优先（.cache/domain.json），缓存未命中则从入口站 HTML 提取。

    Args:
        entry_url: 入口站 URL，如 "https://leyu.me"。为空时使用默认入口。

    Returns:
        真实域名 URL，如 "https://www.5qk8bt.vip:3962"

    Raises:
        DomainError: 所有入口均无法解析域名
    """
    # 缓存优先
    cache = DomainCache()
    cached = cache.get(entry_url)
    if cached:
        return cached

    # 从入口获取
    domain = resolve_domain(entry_url)
    if domain:
        return domain

    # 环境变量兜底
    import os

    env = os.getenv("HDATA_DOMAIN", "")
    if env:
        return env

    raise DomainError(
        f"无法解析域名 (entry={entry_url or 'default'})。"
        "请先访问 leyu.me 完成一次登录，或设置 HDATA_DOMAIN 环境变量。"
    )


# ── 会话缓存 ──────────────────────────────────────────


def _cache_path(account: str) -> Path:
    """返回账号的 WS-only 缓存路径。"""
    return CACHE_DIR / f"{account}.json"


def get_cached_session(account: str) -> dict | None:
    """读取账号的本地缓存。

    Args:
        account: 账号标识

    Returns:
        缓存 dict（包含 game_token, game_player_id, game_backend, game_exp 等），
        缓存不存在或损坏返回 None。
    """
    path = _cache_path(account)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[{account}] 缓存读取失败 ({e})，自动清理")
        path.unlink(missing_ok=True)
        return None


def save_session(account: str, data: dict) -> dict:
    """将 WS-only 认证数据写入缓存。

    写入的字段：game_token, game_player_id, game_backend, game_exp, source。

    Args:
        account: 账号标识
        data: 包含 game_token, game_player_id, game_backend 等字段的 dict

    Returns:
        写入的缓存 dict
    """
    from hdata.auth.params import build_auth_snapshot

    token = data.get("game_token") or data.get("token", "")
    player_id = data.get("game_player_id") or data.get("playerId", 0)
    backend = data.get("game_backend") or data.get("backendDomainUrl", "")
    if not backend:
        backend = data.get("backendDomainUrlList", "").split(",")[0].strip()
    source = data.get("source", "session")

    snapshot = build_auth_snapshot(token, int(player_id or 0), backend, source=source)
    # 合并 session 级别字段
    for field in ("domain", "token", "uuid", "uuidToBase64", "cookies",
                  "backend_domain_url_list", "device_id", "signatures"):
        val = data.get(field)
        if val:
            snapshot[field] = val
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(account).write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return snapshot


# ── API 签名头 ────────────────────────────────────────


def _api_headers(session: dict, url: str) -> dict:
    """构造乐鱼 API 请求头（含 X-API-XXX 签名）。"""
    xxx = ""
    manual_sigs = session.get("signatures", {})
    if manual_sigs:
        for k in sorted(manual_sigs.keys(), key=lambda x: -len(x)):
            if k in url:
                xxx = manual_sigs[k]
                break

    # 兜底：从 uuidToBase64 解密签名表
    if not xxx:
        uuid_b64 = session.get("uuidToBase64", "")
        if uuid_b64:
            from hdata.auth.token_manager import TokenManager

            try:
                st = TokenManager._decrypt_sign_table(uuid_b64)
                xxx = next(
                    (v for k, v in sorted(st.items(), key=lambda x: -len(x[0])) if k in url),
                    "",
                )
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


# ── Token 刷新 ───────────────────────────────────────


async def refresh_game_token(account: str, session: dict) -> str:
    """调用 venue/launch API 获取并返回新的 game JWT。

    Args:
        account: 账号标识（仅用于日志）
        session: 完整 session dict（必须包含 token, domain, signatures 等）

    Returns:
        新的 game JWT 字符串

    Raises:
        TokenRefreshError: API 调用或解密失败
    """
    domain = session.get("domain", "")
    if not domain:
        raise TokenRefreshError(f"[{account}] session 缺少 domain")

    url = f"{domain}/game/api/v1/venue/launch"
    headers = _api_headers(session, url)

    request_error = None
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"enName": "YBZR"},
            impersonate="chrome110",
            timeout=15,
        )
    except Exception as exc:
        request_error = _refresh_error("venue_launch", exc=exc)
    if request_error:
        raise request_error

    if resp.status_code != 200:
        raise _refresh_error("venue_launch", status=resp.status_code)

    parse_error = None
    try:
        data = resp.json()
        game_url = data.get("data", {}).get("url", "")
    except Exception as exc:
        parse_error = _refresh_error("venue_launch_parse", exc=exc)
    if parse_error:
        raise parse_error

    if not game_url or "params=" not in game_url:
        raise _refresh_error("venue_launch_params")

    # 提取并解密 params
    params_b64, ttl = extract_params_from_url(game_url)
    if not params_b64 or not ttl:
        raise _refresh_error("params_extract")

    decrypt_error = None
    try:
        decrypted = decrypt_params(params_b64, ttl)
    except Exception as exc:
        decrypt_error = _refresh_error("params_decrypt", exc=exc)
    if decrypt_error:
        raise decrypt_error

    token = decrypted.get("token", "")
    if not token:
        raise _refresh_error("params_token")

    logger.info(f"[{account}] game JWT refresh succeeded")
    return token


# ── 完整游戏会话 ─────────────────────────────────────


async def get_game_session(account: str) -> dict:
    """获取账号的完整游戏会话（自动降级：缓存 → API 刷新）。

    返回 dict 包含:
        game_token: str          — 游戏 JWT
        game_player_id: int      — 玩家 ID
        game_backend: str        — 后端地址（如 "txdzbjc.com:18034"）
        backend_domain_url_list: str — 备用后端地址列表（如 "txdzbjc.com:18034,64vlwlq.com:18026"）
        game_exp: int            — JWT 过期时间戳

    Raises:
        SessionError: 无法获取有效会话
    """
    # L0: 缓存 game_token 有效 → 直接返回
    cache = get_cached_session(account)
    if cache and validate_game_token(cache.get("game_token", "")):
        logger.info(f"[{account}] L0 hit: cached game_token is valid")
        return {
            "game_token": cache["game_token"],
            "game_player_id": cache.get("game_player_id", 0),
            "game_backend": cache.get("game_backend", ""),
            "backend_domain_url_list": cache.get("backend_domain_url_list", ""),
            "game_exp": cache.get("game_exp", 0),
        }

    # L0b: 缓存有 game_token 但快过期 → 先用着，后台输出警告
    if cache and cache.get("game_token"):
        logger.warning(
            f"[{account}] cached game_token expiring soon, will attempt refresh"
        )

    # L1: 缓存有完整 session（token + domain + signatures）→ venue/launch 刷新
    full_session_path = CACHE_DIR / f"{account}.session.json"
    full_session = None
    if full_session_path.exists():
        try:
            full_session = json.loads(full_session_path.read_text())
        except Exception:
            pass

    if full_session and full_session.get("token") and full_session.get("domain"):
        try:
            new_token = await refresh_game_token(account, full_session)
            # 从 JWT 中提取 player_id
            jwt_info = decode_jwt(new_token)
            player_id = 0
            if jwt_info:
                sub = jwt_info.get("sub", {})
                if isinstance(sub, dict):
                    player_id = sub.get("playerId", 0)

            result = {
                "game_token": new_token,
                "game_player_id": player_id,
                "game_backend": cache.get("game_backend", "") if cache else "",
                "backend_domain_url_list": cache.get("backend_domain_url_list", "") if cache else "",
                "game_exp": jwt_info.get("exp", 0) if jwt_info else 0,
            }
            # 回写到 WS-only 缓存
            save_session(account, result)
            logger.info(f"[{account}] L1 success: API refreshed game JWT")
            return result
        except TokenRefreshError as e:
            logger.warning(f"[{account}] L1 refresh failed: {e}")

    # 无有效缓存 → 抛错，让上层决定如何获取完整 session
    raise SessionError(
        f"[{account}] 无法获取有效 game token。\n"
        f"  请先运行以下命令完成登录:\n"
        f"    uv run python -m hdata.auth.token_manager "
        f"--account {account} --manual-capture\n"
        f"  或注入已有的 game token:\n"
        f"    uv run python -m hdata.auth.token_manager "
        f"--account {account} --inject-game-token <token>"
    )


# ── WS 配置构造 ──────────────────────────────────────


def build_ws_config(game_session: dict) -> dict:
    """从游戏会话构造 WebSocket 连接配置。

    Args:
        game_session: get_game_session() 返回的 dict

    Returns:
        {
            "ws_url": "wss://wsproxy.txdzbjc.com:18026/?playerId=...&jwtToken=...",
            "host": "txdzbjc.com",
            "port": 18026,
            "player_id": 105452510,
            "jwt_token": "eyJhbG...",
        }
    """
    token = game_session.get("game_token", "")
    player_id = game_session.get("game_player_id", 0)
    backend = game_session.get("game_backend", "")
    device_id = game_session.get("device_id", "")

    # 从 backend 提取 host（去掉端口部分）
    host = backend.split(":")[0] if backend else ""

    # 构造 WS URL
    ws_url = (
        f"wss://wsproxy.{host}:{DEFAULT_WS_PORT}/"
        f"?playerId={player_id}"
        f"&jwtToken={token}"
        f"&deviceType=2&platform=6"
    )
    if device_id:
        ws_url += f"&deviceId={device_id}"

    return {
        "ws_url": ws_url,
        "host": host,
        "port": DEFAULT_WS_PORT,
        "player_id": player_id,
        "jwt_token": token,
        "device_id": device_id,
        "backend": backend,
        "backend_domain_url_list": game_session.get("backend_domain_url_list", ""),
    }


# ── 统一登录接口 ──────────────────────────────────────


class LoginError(SessionError):
    """登录失败。"""


async def get_login(account: str, password: str = "",
                    entry_url: str = "", force_refresh: bool = False,
                    captcha_token: str = "", geepass_token: str = "",
                    jfbym_token: str = "") -> dict:
    """统一登录接口：提供账号密码，返回所有登录参数。

    内部自动处理: 缓存 → HTTP 打码登录 → 浏览器辅助登录。
    调用方不需要关心后端实现。

    Args:
        account: 账号标识
        password: 密码（缓存有效时可为空；force_refresh 时必填）
        entry_url: 入口站 URL，默认 leyu.me
        force_refresh: 跳过缓存，强制重新登录
        captcha_token: 兼容旧参数，映射到 jfbym_token
        geepass_token: geepass API token，传入则优先尝试纯 HTTP 登录
        jfbym_token: jfbym API token，传入则优先尝试纯 HTTP 登录

    Returns:
        {"account","domain","token","uuid","uuidToBase64","cookies",
         "game_token","game_player_id","game_backend","game_exp",
         "backend_domain_url_list","device_id","signatures"}

    Raises:
        LoginError: 所有登录方式均失败
    """
    # ── 1. 读缓存 ──
    if not force_refresh:
        cache = get_cached_session(account)
        if cache and cache.get("domain") and cache.get("token"):
            game_token = cache.get("game_token", "")
            if game_token and validate_game_token(game_token):
                logger.info(f"[{account}] get_login: cache hit")
                cache["account"] = account
                return cache
            # 缓存有 session 但 game_token 过期 → 尝试刷新
            if game_token:
                logger.info(f"[{account}] get_login: cached session valid, refreshing game_token")
                try:
                    new_token = await refresh_game_token(account, cache)
                    cache["game_token"] = new_token
                    jwt_info = decode_jwt(new_token)
                    if jwt_info:
                        cache["game_exp"] = jwt_info.get("exp", 0)
                        sub = jwt_info.get("sub", {})
                        if isinstance(sub, dict):
                            cache["game_player_id"] = sub.get("playerId", 0)
                    save_session(account, cache)
                    cache["account"] = account
                    return cache
                except TokenRefreshError:
                    logger.warning(f"[{account}] get_login: refresh failed, fallback to browser")

    legacy_jfbym_token = jfbym_token or captcha_token

    # ── 2. HTTP 打码登录（如果提供了平台 token）──
    if password and (geepass_token or legacy_jfbym_token):
        try:
            from hdata.auth.http_login_v2 import login as http_login_v2
            logger.info(f"[{account}] get_login: trying HTTP login with captcha")
            http_session = await http_login_v2(
                account,
                password,
                geepass_token=geepass_token,
                jfbym_token=legacy_jfbym_token,
            )
            if http_session and http_session.get("token"):
                # HTTP login 返回的是 session-level 数据，需要补 game 字段
                result = dict(http_session)
                result["account"] = account
                result["source"] = "http_login"
                # 补 game 字段：用 session 去刷新 game_token
                game_token_ok = False
                try:
                    new_token = await refresh_game_token(account, result)
                    result["game_token"] = new_token
                    jwt_info = decode_jwt(new_token)
                    if jwt_info:
                        result["game_exp"] = jwt_info.get("exp", 0)
                        sub = jwt_info.get("sub", {})
                        if isinstance(sub, dict):
                            result["game_player_id"] = sub.get("playerId", 0)
                    game_token_ok = True
                except TokenRefreshError:
                    logger.warning(f"[{account}] HTTP login OK but game_token refresh failed")
                if game_token_ok:
                    save_session(account, result)
                    logger.info(f"[{account}] get_login: HTTP login success")
                    return result
                # game_token 刷新失败 → 降级到浏览器登录
                logger.info(f"[{account}] get_login: game_token refresh failed, fall to browser")
        except Exception as e:
            logger.warning(
                f"[{account}] get_login: HTTP login stage failed "
                f"({type(e).__name__}), fall to browser"
            )

    # ── 3. 浏览器登录 ──
    if not password:
        raise LoginError(
            f"[{account}] 缓存无效且未提供密码。"
            f"请调用 get_login(account, password='your_pwd') 或先手动登录一次。"
        )

    # 获取真实域名
    try:
        domain = get_real_domain(entry_url)
    except DomainError:
        domain = ""
    logger.info(f"[{account}] get_login: launching browser, domain={domain or '(will auto-detect)'}")

    from hdata.auth.browser_login import GameBrowserLogin
    from pathlib import Path as _Path

    PROJ_ROOT = _Path(__file__).resolve().parent.parent.parent
    profile_dir = PROJ_ROOT / ".cache" / "browser_profiles" / account
    auth_cache_path = _cache_path(account)

    bot = GameBrowserLogin(
        entry_url=entry_url or "https://leyu.me",
        headless=False,
        profile_dir=profile_dir,
        auth_cache_path=auth_cache_path,
    )
    result = await bot.run()
    if not result:
        raise LoginError(f"[{account}] 浏览器登录失败：未捕获到认证数据")

    # result 现在已包含 game 字段 + session 字段（由 _enrich_with_session 补充）
    if not result.get("game_token"):
        raise LoginError(f"[{account}] 浏览器登录失败：缺少 game_token")

    # 补充缺失字段
    if not result.get("domain"):
        result["domain"] = domain or get_real_domain(entry_url)
    result["account"] = account
    result["source"] = "browser_login"

    # 解密 uuidToBase64 → signatures
    if result.get("uuidToBase64") and not result.get("signatures"):
        try:
            from hdata.auth.token_manager import TokenManager
            st = TokenManager._decrypt_sign_table(result["uuidToBase64"])
            result["signatures"] = st
        except Exception:
            pass

    # 保存
    save_session(account, result)
    logger.info(f"[{account}] get_login: saved to cache ({len(result)} fields)")
    return result


# ── 账号管理 ──────────────────────────────────────────


def get_accounts() -> list[str]:
    """返回所有有缓存的账号名列表。"""
    if not CACHE_DIR.exists():
        return []
    accounts = []
    for f in CACHE_DIR.iterdir():
        if f.suffix == ".json" and f.stem not in ("domain",):
            accounts.append(f.stem)
    return sorted(accounts)


async def batch_refresh(accounts: list[str]) -> dict[str, str]:
    """批量刷新多个账号的 game token。

    注意：仅在账号有完整 session（.session.json）时有效。
    没有 session 的账号会被跳过并记录警告。

    Args:
        accounts: 账号名列表

    Returns:
        {account: game_token} 映射（仅包含刷新成功的账号）
    """
    results: dict[str, str] = {}
    for account in accounts:
        try:
            session = await get_game_session(account)
            results[account] = session["game_token"]
            logger.info(f"[{account}] batch refresh OK")
        except SessionError as e:
            logger.warning(f"[{account}] batch refresh skipped: {e}")
        except Exception as e:
            logger.error(f"[{account}] batch refresh error: {e}")
    return results
