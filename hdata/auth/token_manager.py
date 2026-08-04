"""乐鱼 Token 管理器门面 — 委托给 LoginOrchestrator（Task 9 拆薄）。

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

实现（全部逻辑）位于 hdata.auth.login_orchestrator.LoginOrchestrator，
HTTP 验证码登录位于 hdata.auth.captcha_client.http_login_with_captcha。

用法:
    from hdata.auth.token_manager import TokenManager
    from hdata.auth.captcha_solver import JfbymSolver

    tm = TokenManager(account="lds003", solver=JfbymSolver(token="xxx"))
    jwt = await tm.get_token()  # 一切自动
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hdata.auth.captcha_solver import JfbymSolver
from hdata.auth.login_orchestrator import (
    LoginOrchestrator,
    TokenUnavailableError,
)
from hdata.auth.params import decode_jwt as _decode_jwt
from hdata.auth.sign_table import decrypt_sign_table
from htools.utils.logger import get_logger

logger = get_logger(__name__)


def decode_jwt(token: str) -> dict | None:
    """解码 JWT payload（不验证签名）。兼容旧引用，委托给 params.py。"""
    return _decode_jwt(token)


# ═══════════════════════════════════════════════════════════
# TokenManager（薄门面）
# ═══════════════════════════════════════════════════════════


class TokenManager:
    """多账号 Token 管理器门面 — 委托给 LoginOrchestrator。

    Attributes:
        account: 账号标识（用于缓存隔离和日志）
    """

    def __init__(self, account: str = "default",
                 solver=None,  # CaptchaSolver | None
                 user: str = "",
                 pwd: str = ""):
        self._orch = LoginOrchestrator(account, solver=solver, user=user, pwd=pwd)

    @property
    def account(self) -> str:
        return self._orch.account

    @property
    def _cache_path(self) -> Path:
        return self._orch._cache_path

    # ── 对外 API ──────────────────────────────────────────

    async def get_token(self, user: str = "", pwd: str = "") -> str:
        """获取有效的游戏 JWT token。内部自动降级。"""
        return await self._orch.get_token(user, pwd)

    def diagnose(self) -> dict:
        """自诊断：检查所有依赖和状态，返回可操作的修复建议。"""
        return self._orch.diagnose()

    def health(self) -> dict:
        """返回当前 token 状态（同步，不触发登录）。"""
        return self._orch.health()

    async def manual_capture(self, entry_url: str = "https://leyu.me") -> str | None:
        """打开可见浏览器，人工完成登录后抓取 game token。"""
        return await self._orch.manual_capture(entry_url)

    def inject_tokens(
        self,
        game_token: str = "",
        game_player_id: int = 0,
        game_backend: str = "",
        game_exp: int = 0,
        source: str = "inject",
    ) -> dict:
        """注入当前最新认证快照。"""
        return self._orch.inject_tokens(game_token, game_player_id,
                                        game_backend, game_exp, source)

    def import_token_file(self, file_path: str) -> dict:
        """从外部 JSON 文件导入当前最新 WS-only 认证快照。"""
        return self._orch.import_token_file(file_path)

    @staticmethod
    def _decrypt_sign_table(b64: str) -> dict[str, str]:
        """AES-CBC 解密签名表。委托给 sign_table 叶子模块。"""
        return decrypt_sign_table(b64)


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
