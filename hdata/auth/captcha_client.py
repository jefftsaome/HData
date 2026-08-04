"""纯 HTTP 验证码登录客户端 — 无浏览器登录（L3a 链路）。

收敛到 http_login 完整流水线（Task 10）：委托 http_login.login，含
kaptchcate 预注册/重试/域名失效切换/login_trace 埋点/user_ip→X-API-FINGER/uuid。
从 solver 提取平台与 token。
"""

from __future__ import annotations


async def http_login_with_captcha(account: str, user: str, pwd: str, solver) -> dict | None:
    """纯 HTTP 验证码登录 — 委托 http_login 完整流水线。

    从 solver 提取平台与 token 后调用 http_login.login(含 kaptchcate
    预注册/重试/域名失效切换/埋点/uuid)。返回完整 dict。
    """
    from hdata.auth.http_login import login as _login

    if solver is None:
        return None
    name = solver.info().name
    kwargs = (
        {"geepass_token": solver._token}
        if name == "geepass"
        else {"jfbym_token": solver._token}
    )
    return await _login(user, pwd, **kwargs)
