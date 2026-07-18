"""
乐鱼统一登录接口 — get_login(username, password)

提供完全无浏览器的模拟登录方案，支持自动降级:
  1. 缓存会话 → HTTP API 刷新 game_token (纯HTTP，最快)
  2. HTTP 验证码登录 (纯HTTP，依赖打码平台)
  3. 浏览器辅助登录 (需要Playwright，作为兜底)

用法:
    from hdata.auth.session import get_login

    # 最简单用法（自动处理缓存/刷新/降级）
    session = await get_login("username", "password")

    # 使用打码平台（优先尝试纯HTTP登录）
    import os

    session = await get_login(
        "username",
        "password",
        geepass_token=os.getenv("GEEPASS_TOKEN", ""),
        jfbym_token=os.getenv("JFBYM_TOKEN", ""),
    )

    # 返回结构:
    {
        "account": "username",
        "token": "X-API-TOKEN...",       # 主站 API token
        "uuid": "...",                    # 用户 UUID
        "domain": "https://...",          # 真实域名
        "game_token": "eyJ...",           # 游戏 JWT
        "game_player_id": 123456,         # 玩家 ID
        "game_backend": "host:port",      # 游戏后端
        "signatures": {...},              # API 签名表
        ...
    }
"""
from importlib import import_module

from .session import get_login, LoginError, get_game_session

_LEGACY_EXPORTS = {
    "TokenManager": (".token_manager", "TokenManager"),
    "TokenUnavailableError": (".token_manager", "TokenUnavailableError"),
    "resolve_domain": (".domain", "resolve_domain"),
    "DomainCache": (".domain", "DomainCache"),
    "CaptchaSolver": (".captcha_solver", "CaptchaSolver"),
    "JfbymSolver": (".captcha_solver", "JfbymSolver"),
    "CaptchaChallenge": (".captcha_solver", "CaptchaChallenge"),
    "CaptchaSolution": (".captcha_solver", "CaptchaSolution"),
    "SolverInfo": (".captcha_solver", "SolverInfo"),
    "CaptchaSolveError": (".captcha_solver", "CaptchaSolveError"),
}

__all__ = ["get_login", "LoginError", "get_game_session", *_LEGACY_EXPORTS]


def __getattr__(name: str):
    try:
        module_name, attribute_name = _LEGACY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
