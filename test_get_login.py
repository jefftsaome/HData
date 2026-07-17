"""get_login 接口测试脚本。

用法:
    # 1. 纯缓存（第一次需要先手动登录过一次）:
    uv run python test_get_login.py

    # 2. HTTP 打码登录（从环境变量传入平台 token）:
    uv run python test_get_login.py --http --geepass-token "$GEEPASS_TOKEN"

    # 3. 强制浏览器登录:
    uv run python test_get_login.py --browser

    # 4. 指定账号:
    uv run python test_get_login.py --account my_account --password my_pwd
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


async def test_cache_hit(account: str) -> bool:
    """测试缓存命中路径。"""
    from hdata.auth.session import get_login, LoginError

    print(f"\n{'=' * 60}")
    print(f"[test] cache hit: get_login('{account}')")
    print(f"{'=' * 60}")

    try:
        result = await get_login(account)
        print(f"  [OK] cache hit")
        _print_result(result)
        return True
    except LoginError as e:
        print(f"  [SKIP] cache miss: {e}")
        print(f"  -> Run once with --browser or --http first.")
        return True  # cache miss is not a failure, just needs init


async def test_http_login(
    account: str,
    password: str,
    captcha_token: str,
    geepass_token: str,
    jfbym_token: str,
) -> bool:
    """测试 HTTP 打码登录路径。"""
    from hdata.auth.session import get_login, LoginError

    print(f"\n{'=' * 60}")
    print(f"[test] HTTP captcha login: get_login('{account}', password, platform tokens)")
    print(f"{'=' * 60}")

    try:
        result = await get_login(
            account,
            password,
            geepass_token=geepass_token,
            jfbym_token=jfbym_token or captcha_token,
        )
        print(f"  [OK] HTTP login success")
        _print_result(result)
        return True
    except LoginError as e:
        print(f"  [FAIL] HTTP login: {e}")
        return False


async def test_browser_login(account: str, password: str) -> bool:
    """测试浏览器辅助登录路径。"""
    from hdata.auth.session import get_login, LoginError

    print(f"\n{'=' * 60}")
    print(f"[test] browser login: get_login('{account}', password, force_refresh=True)")
    print(f"{'=' * 60}")
    print(f"  [INFO] Browser will open. Login to leyu.me, enter a game table.")

    try:
        result = await get_login(account, password, force_refresh=True)
        print(f"  [OK] browser login success")
        _print_result(result)
        return True
    except LoginError as e:
        print(f"  [FAIL] browser login: {e}")
        return False


async def test_missing_password() -> bool:
    """测试无密码、无缓存时的错误提示。"""
    from hdata.auth.session import get_login, LoginError

    print(f"\n{'=' * 60}")
    print(f"[test] error path: get_login('__nonexistent__')")
    print(f"{'=' * 60}")

    try:
        await get_login("__nonexistent__")
        print(f"  [FAIL] should have raised LoginError")
        return False
    except LoginError as e:
        print(f"  [OK] LoginError raised: {e}")
        return True


async def test_validate_result(account: str) -> bool:
    """测试返回结果的字段完整性。"""
    from hdata.auth.session import get_login, LoginError
    from hdata.auth.params import validate_game_token, token_remaining_hours

    print(f"\n{'=' * 60}")
    print(f"[test] result validation: get_login('{account}')")
    print(f"{'=' * 60}")

    try:
        result = await get_login(account)
    except LoginError:
        print(f"  [SKIP] cache miss, cannot validate")
        return True

    required_game = ["game_token", "game_player_id", "game_backend"]
    required_session = ["domain", "token", "uuid"]
    optional = ["game_exp", "backend_domain_url_list", "device_id",
                "uuidToBase64", "cookies", "signatures"]

    all_ok = True
    for field in required_game + required_session:
        ok = field in result and result[field]
        tag = "[OK]" if ok else "[MISS]"
        if not ok:
            all_ok = False
        print(f"  {tag} {field}: {'yes' if result.get(field) else 'MISSING'}")

    for field in optional:
        has = field in result and result[field]
        tag = "[OK]" if has else "[-]"
        print(f"  {tag} {field}: {'yes' if has else 'no (optional)'}")

    if result.get("game_token"):
        valid = validate_game_token(result["game_token"])
        hours = token_remaining_hours(result["game_token"])
        tag = "[OK]" if valid else "[WARN]"
        print(f"  {tag} game_token TTL: {hours:.1f}h")

    if result.get("account") == account:
        print(f"  [OK] account: {account}")
    else:
        print(f"  [FAIL] account: expected {account}, got {result.get('account')}")
        all_ok = False

    return all_ok


def _print_result(result: dict):
    """精简打印返回值。"""
    print(f"  account:    {result.get('account', 'N/A')}")
    print(f"  domain:     {result.get('domain', 'N/A')}")
    t = result.get('token', '')
    print(f"  token:      {t[:30]}..." if t else "  token:      none")
    print(f"  uuid:       {'yes' if result.get('uuid') else 'no'}")
    gt = result.get('game_token', '')
    print(f"  game_token: {gt[:30]}..." if gt else "  game_token: none")
    print(f"  player_id:  {result.get('game_player_id', 'N/A')}")
    print(f"  backend:    {result.get('game_backend', 'N/A')}")
    sigs = result.get('signatures', {})
    print(f"  signatures: {len(sigs)} entries")
    c = result.get('cookies', '')
    print(f"  cookies:    {len(c)} chars")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="get_login interface test")
    parser.add_argument("--account", default="lidongsen1")
    parser.add_argument("--password", default="")
    parser.add_argument(
        "--captcha-token", default="", help="deprecated jfbym token alias"
    )
    parser.add_argument("--geepass-token", default="")
    parser.add_argument("--jfbym-token", default="")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser


async def main():
    args = build_parser().parse_args()

    print("get_login interface test")
    print(f"account: {args.account}")
    print()

    results: list[bool] = []

    if args.http:
        if not args.password or not (
            args.captcha_token or args.geepass_token or args.jfbym_token
        ):
            print(
                "[ERROR] HTTP login requires --password and at least one "
                "captcha platform token"
            )
            return 1
        results.append(await test_http_login(
            args.account,
            args.password,
            args.captcha_token,
            args.geepass_token,
            args.jfbym_token,
        ))

    if args.browser:
        if not args.password:
            print("[ERROR] Browser login requires --password")
            return 1
        results.append(await test_browser_login(args.account, args.password))

    if args.all:
        results.append(await test_cache_hit(args.account))
        results.append(await test_missing_password())
    else:
        results.append(await test_cache_hit(args.account))
        results.append(await test_validate_result(args.account))
        results.append(await test_missing_password())

    print(f"\n{'=' * 60}")
    passed = sum(results)
    total = len(results)
    print(f"Result: {passed}/{total} passed")
    print(f"{'=' * 60}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
