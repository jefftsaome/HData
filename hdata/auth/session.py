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

from collections.abc import Mapping
import json
import re
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

# WS 端口：不再写死。游戏前端（egret index release js）的拼接规则是
#   socketServer = `wss://wsproxy.${params.backendDomainUrl.trim()}`
# 即直接使用 venue/launch 下发的 backendDomainUrl（自带端口，如 6pwn4i.com:4999）。
DEFAULT_WS_PORT = 18026  # 兼容旧引用，仅作兜底

# 游戏前端 Q9.STATIC_KEY_PREFIX + Q9.KEY_VERSION：
#   initServerUrl(e) => this._url = e + Q9.STATIC_KEY_PREFIX + Q9.KEY_VERSION
# 其中 STATIC_KEY_PREFIX = "&platformId=1&applicationId=5&version=v"，KEY_VERSION = "1.0.5"。
# wsproxy 校验该后缀，缺失时握手直接返回 HTTP 500。
WS_STATIC_KEY_SUFFIX = "&platformId=1&applicationId=5&version=v1.0.5"


def generate_device_id() -> str:
    """生成与浏览器端一致格式的 deviceId。

    浏览器逻辑（assets release js）:
        fixedDeviceId = Date.now().toString() + Math.floor(1e5*(9*Math.random()+1))
        DEVICE_ID = fixedDeviceId + "-" + Math.floor(1e7*(9*Math.random()+1))
    即 "{13位毫秒时间戳}{6位随机数}-{8位随机数}"，例如 1783928954151253203-87228064。
    """
    import random
    import time

    fixed = f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"
    suffix = random.randint(10000000, 99999999)
    return f"{fixed}-{suffix}"


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

    域名是动态资源（可能小时级轮换），因此：
      1. 缓存带 TTL（DomainCache.DEFAULT_TTL，默认 30 分钟）
      2. 命中缓存后先探活，死了自动 invalidate 并从入口站重解析
      3. 解析失败回退环境变量 HDATA_DOMAIN

    Args:
        entry_url: 入口站 URL，如 "https://leyu.me"。为空时使用默认入口。

    Returns:
        真实域名 URL，如 "https://www.5qk8bt.vip:3962"

    Raises:
        DomainError: 所有入口均无法解析域名
    """
    # 从入口获取（带缓存 + 探活 + 自动重解析）
    domain = resolve_domain(entry_url, validate=True)
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
    """构造乐鱼 API 请求头（含 X-API-XXX 签名）。

    优先使用 wasm 动态签名（api_sign.sign_path，2026-07-17 逆向落地）；
    失败时回退旧的静态签名表（手动捕获 / uuidToBase64 解密）。
    """
    xxx = ""

    # 首选：wasm 动态签名（每请求唯一，服务端对 /game/api 等强制校验）
    try:
        from hdata.auth.api_sign import sign_path

        m = re.search(r"/(\w+)/api", url)
        if m:
            xxx = sign_path(f"/{m.group(1)}/api")
    except Exception:
        xxx = ""

    # 兜底 1：缓存中的手动捕获签名
    if not xxx:
        manual_sigs = session.get("signatures", {})
        if manual_sigs:
            for k in sorted(manual_sigs.keys(), key=lambda x: -len(x)):
                if k in url:
                    xxx = manual_sigs[k]
                    break

    # 兜底 2：从 uuidToBase64 解密签名表
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


async def refresh_game_session(account: str, session: dict) -> dict:
    """调用 venue/launch API，返回解密后的完整游戏参数。

    Returns:
        解密 params dict（含 token/backendDomainUrl/backendDomainUrlList/playerId 等）

    Raises:
        TokenRefreshError: API 调用或解密失败
    """
    domain = session.get("domain", "")
    if not domain:
        raise TokenRefreshError(f"[{account}] session 缺少 domain")

    url = f"{domain}/game/api/v1/venue/launch"
    headers = _api_headers(session, url)
    # token 绑定登录 IP：刷新必须走会话绑定的同一出口
    proxy = session.get("proxy") or ""
    proxies = {"http": proxy, "https": proxy} if proxy else None

    request_error = None
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"enName": "YBZR"},
            impersonate="chrome110",
            timeout=15,
            proxies=proxies,
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

    if not isinstance(decrypted, Mapping):
        raise _refresh_error("params_decrypt_parse")

    if not decrypted.get("token"):
        raise _refresh_error("params_token")

    logger.info(f"[{account}] game JWT refresh succeeded")
    return dict(decrypted)


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
    params = await refresh_game_session(account, session)
    return params["token"]


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
            params = await refresh_game_session(account, full_session)
            new_token = params["token"]
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
                "game_backend": params.get("backendDomainUrl", ""),
                "backend_domain_url_list": params.get("backendDomainUrlList", ""),
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

    与浏览器端（egret release js）完全一致的拼接规则：
        wss://wsproxy.{backendDomainUrl}/?playerId=..&jwtToken=..&deviceId=..
    backendDomainUrl 自带端口（如 6pwn4i.com:4999），不再额外覆盖端口。

    Args:
        game_session: get_game_session() 返回的 dict

    Returns:
        {
            "ws_url": "wss://wsproxy.6pwn4i.com:4999/?playerId=...&jwtToken=...&deviceId=...",
            "host": "wsproxy.6pwn4i.com",
            "port": 4999,
            "player_id": 105452510,
            "jwt_token": "eyJhbG...",
            "device_id": "1783928954151253203-87228064",
        }
    """
    token = game_session.get("game_token", "")
    player_id = game_session.get("game_player_id", 0)
    backend = (game_session.get("game_backend", "") or "").strip()
    device_id = game_session.get("device_id", "") or generate_device_id()

    # backend 形如 "6pwn4i.com:4999"；wsproxy 子域 + 原端口
    backend_host = backend.split(":")[0] if backend else ""
    backend_port = int(backend.split(":")[1]) if ":" in backend else DEFAULT_WS_PORT

    # 浏览器端 initServerUrl 会追加 STATIC_KEY_PREFIX + KEY_VERSION；
    # 缺少这段时 wsproxy 直接拒绝握手（HTTP 500）。
    ws_url = (
        f"wss://wsproxy.{backend}/"
        f"?playerId={player_id}"
        f"&jwtToken={token}"
        f"&deviceId={device_id}"
        f"{WS_STATIC_KEY_SUFFIX}"
    )

    return {
        "ws_url": ws_url,
        "host": f"wsproxy.{backend_host}",
        "port": backend_port,
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
                    jfbym_token: str = "",
                    proxy: str | None = None) -> dict:
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
        proxy: 代理 URL（可选）。token 绑定登录 IP——传入后登录、
               刷新、后续 WS 连接必须使用同一代理出口；proxy 作为
               会话属性写入返回的 session["proxy"]，由下游自动继承

    Returns:
        {"account","domain","token","uuid","uuidToBase64","cookies",
         "game_token","game_player_id","game_backend","game_exp",
         "backend_domain_url_list","device_id","signatures","proxy"}

    Raises:
        LoginError: 所有登录方式均失败
    """
    # ── 1. 读缓存 ──
    if not force_refresh:
        cache = get_cached_session(account)
        if cache and cache.get("domain") and cache.get("token"):
            cache["proxy"] = proxy or ""     # 运行时属性，不落盘
            game_token = cache.get("game_token", "")
            if game_token and validate_game_token(game_token):
                logger.info(f"[{account}] get_login: cache hit")
                cache["account"] = account
                return cache
            # 缓存有 session 但 game_token 过期 → 尝试刷新
            if game_token:
                logger.info(f"[{account}] get_login: cached session valid, refreshing game_token")
                try:
                    params = await refresh_game_session(account, cache)
                    new_token = params["token"]
                    cache["game_token"] = new_token
                    if params.get("backendDomainUrl"):
                        cache["game_backend"] = params["backendDomainUrl"]
                    if params.get("backendDomainUrlList"):
                        cache["backend_domain_url_list"] = params["backendDomainUrlList"]
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
            from hdata.auth.http_login import login as http_login
            logger.info(f"[{account}] get_login: trying HTTP login with captcha")
            http_session = await http_login(
                account,
                password,
                geepass_token=geepass_token,
                jfbym_token=legacy_jfbym_token,
                proxy=proxy or "",
            )
            if http_session and http_session.get("token"):
                # HTTP login 返回的是 session-level 数据，需要补 game 字段
                result = dict(http_session)
                result["account"] = account
                result["source"] = "http_login"
                result["proxy"] = proxy or ""
                # 补 game 字段：用 session 去刷新 game_token
                game_token_ok = False
                try:
                    params = await refresh_game_session(account, result)
                    new_token = params["token"]
                    result["game_token"] = new_token
                    if params.get("backendDomainUrl"):
                        result["game_backend"] = params["backendDomainUrl"]
                    if params.get("backendDomainUrlList"):
                        result["backend_domain_url_list"] = params["backendDomainUrlList"]
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

    if proxy:
        # 浏览器走直连：登录产物会绑定本机 IP，与代理出口不一致，
        # 后续 WS 必然被 10026 拒绝——直接报错而不是产出不可用的会话
        raise LoginError(
            f"[{account}] 浏览器兜底登录不支持代理（token 绑定登录 IP，"
            f"浏览器走直连会得到与代理不匹配的会话）。"
            f"请确认打码 token 有效、使用纯 HTTP 登录通道。"
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
