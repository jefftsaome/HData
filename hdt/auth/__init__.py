"""hdt.auth — 乐鱼认证模块。

导出:
  - TokenManager: 多账号 Token 管理器（对外唯一入口）
  - resolve_domain: 从集团入口站解析真实域名
  - DomainCache: 域名缓存
  - HeadlessLogin: 无头浏览器登录引擎（内部使用）
  - CaptchaSolver / JfbymSolver: 验证码识别抽象
  - CaptchaChallenge / CaptchaSolution / SolverInfo: 数据类型
  - CaptchaSolveError / TokenUnavailableError: 异常
"""

from hdt.auth.token_manager import TokenManager, TokenUnavailableError
from hdt.auth.headless_login import HeadlessLogin
from hdt.auth.domain import resolve_domain, DomainCache
from hdt.auth.captcha_solver import (
    CaptchaSolver,
    JfbymSolver,
    CaptchaChallenge,
    CaptchaSolution,
    SolverInfo,
    CaptchaSolveError,
)

__all__ = [
    "TokenManager",
    "TokenUnavailableError",
    "HeadlessLogin",
    "resolve_domain",
    "DomainCache",
    "CaptchaSolver",
    "JfbymSolver",
    "CaptchaChallenge",
    "CaptchaSolution",
    "SolverInfo",
    "CaptchaSolveError",
]
