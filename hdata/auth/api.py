"""
乐鱼无浏览器登录 — 用户接口

提供最简单的 get_login(username, password) 函数，
自动处理缓存、HTTP打码登录、浏览器辅助登录等所有降级逻辑。

用法:
    import asyncio
    from hdata.auth.api import get_login

    async def main():
        session = await get_login("your_username", "your_password")
        print(session["token"])       # 主站 API token
        print(session["game_token"])  # 游戏 JWT

    asyncio.run(main())

环境变量:
    GEEPASS_TOKEN / JFBYM_TOKEN — 对应打码平台的 API token（用于纯 HTTP 打码登录）
    CAPTCHA_TOKEN — 兼容旧配置，作为 JFBYM_TOKEN 的回退
    LEYU_USER / LEYU_PWD — 默认用户名/密码
    HDATA_DOMAIN — 强制指定域名
"""

import asyncio
import os
from typing import Optional

from hdata.auth.session import get_login as _get_login
from hdata.auth.session import LoginError, get_game_session


async def get_login(
    username: str = "",
    password: str = "",
    *,
    captcha_token: str = "",
    geepass_token: str = "",
    jfbym_token: str = "",
    force_refresh: bool = False,
) -> dict:
    """统一登录接口 — 获取乐鱼平台所有认证参数。

    自动降级策略:
      1. 缓存有效 → 直接返回 (纯HTTP, <1s)
      2. 缓存有session但game_token过期 → HTTP刷新 (纯HTTP, ~2s)
      3. 提供 geepass_token/jfbym_token → HTTP 打码登录 (纯HTTP, ~15s, 成功率取决于打码平台)
      4. 兜底 → Playwright浏览器登录 (需要图形界面, ~30-120s)

    Args:
        username: 乐鱼账号
        password: 乐鱼密码
        captcha_token: 兼容旧参数，映射到 jfbym_token
        geepass_token: geepass API token（用于纯 HTTP 验证码识别）
        jfbym_token: jfbym API token（用于纯 HTTP 验证码识别）
        force_refresh: 跳过缓存，强制重新登录

    Returns:
        dict 包含以下字段:
            account:          账号名
            token:            主站 X-API-TOKEN
            uuid:             用户 UUID
            domain:           真实域名 (如 https://www.xxx.vip:端口)
            game_token:       游戏 JWT
            game_player_id:   玩家 ID
            game_backend:     游戏后端地址
            game_exp:         游戏 JWT 过期时间戳
            backend_domain_url_list: 备用后端地址列表
            signatures:       API 签名表 (用于 X-API-XXX 头)
            source:           数据来源 (cache/http_login/browser_login)

    Raises:
        LoginError: 所有登录方式均失败

    Example:
        >>> session = await get_login("user123", "pass456")
        >>> print(session["game_token"][:50])
        'eyJhbGciOiJIUzI1NiJ9...'
    """
    # 使用环境变量作为默认值
    user = username or os.getenv("LEYU_USER", "")
    pwd = password or os.getenv("LEYU_PWD", "")
    gp_token = geepass_token or os.getenv("GEEPASS_TOKEN", "")
    jf_token = (
        jfbym_token
        or captcha_token
        or os.getenv("JFBYM_TOKEN", "")
        or os.getenv("CAPTCHA_TOKEN", "")
    )

    if not user or not pwd:
        raise LoginError(
            "请提供用户名和密码。\n"
            "用法: get_login('username', 'password')\n"
            "或设置环境变量: LEYU_USER, LEYU_PWD"
        )

    # 使用账号名作为缓存 key
    account = user

    return await _get_login(
        account=account,
        password=pwd,
        captcha_token="",
        geepass_token=gp_token,
        jfbym_token=jf_token,
        force_refresh=force_refresh,
    )


# 同步版本（方便在非async环境中使用）
def get_login_sync(username: str = "", password: str = "", **kwargs) -> dict:
    """get_login 的同步包装器。"""
    return asyncio.run(get_login(username, password, **kwargs))


__all__ = ["get_login", "get_login_sync", "LoginError", "get_game_session"]
