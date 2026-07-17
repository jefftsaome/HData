"""
纯HTTP乐鱼登录 — 完整无浏览器实现。

流程: fetch_captcha → geepass/jfbym solve → generate_w → verify → validateGeeCheckV2 → login → JWT

这是对 http_login.py 的完全重写，修复了以下问题:
1. 补全了 validateGeeCheckV2 步骤（原版缺少）
2. 使用正确的域名解析
3. 从 verify 响应的 seccode 中提取 captcha_output/gen_time/pass_token
4. 添加了完整的错误处理和重试机制
5. 支持 geepass(优先) 和 jfbym(备选) 双平台
6. geepass 和 jfbym 使用各自独立的 API token

用法:
    from hdata.auth.http_login_v2 import login
    session = await login("username", "password", captcha_token="xxx")
    # 或分开指定:
    session = await login("username", "password", geepass_token="xxx", jfbym_token="yyy")
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import urllib.parse
from typing import Optional

from curl_cffi import requests as cr

from hdata.auth.captcha import fetch_captcha as _fetch_captcha
from hdata.auth.captcha_solver import (
    CaptchaChallenge,
    CaptchaSolution,
    CaptchaSolveError,
    CaptchaSolver,
    GeepassSolver,
    JfbymSolver,
)
from hdata.auth.domain import resolve_domain as _resolve_domain
from hdata.auth.geetest_signer import generate_w

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"


class VerifyError(RuntimeError):
    def __init__(self, result: str, fail_count: int = 0):
        self.result = result
        self.fail_count = fail_count
        super().__init__(f"verify {result}; fail_count={fail_count}")


def _get_domain() -> str:
    """获取乐鱼真实域名（缓存优先）。"""
    domain = _resolve_domain()
    if domain:
        return domain

    # 兜底：直接从入口站重定向获取
    resp = cr.get("https://leyu.me", impersonate="chrome110",
                  timeout=10, allow_redirects=True)
    m = re.match(r"https://[^/]+", resp.url)
    return m.group(0) if m else ""


def _build_solvers(geepass_token: str, jfbym_token: str) -> list[CaptchaSolver]:
    solvers: list[CaptchaSolver] = []
    if geepass_token:
        solvers.append(GeepassSolver(api_token=geepass_token))
    if jfbym_token:
        solvers.append(JfbymSolver(api_token=jfbym_token))
    return solvers


async def _solve_captcha(
    challenge: CaptchaChallenge,
    solvers: list[CaptchaSolver],
) -> CaptchaSolution:
    failures = []
    for solver in solvers:
        name = solver.info().name
        try:
            solution = await solver.solve(challenge)
            solution.solver_name = name
            return solution
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
    raise CaptchaSolveError("solver-chain", "all solvers failed", "; ".join(failures))


async def _verify_captcha(load_data: dict, coords: str) -> Optional[dict]:
    """调用 GeeTest verify API，返回 seccode dict 或 None。

    Returns:
        {"captcha_output": ..., "gen_time": ..., "pass_token": ...} 或 None
    """
    w = generate_w(load_data, CAPTCHA_ID, coords)

    cb = f"botion_{int(time.time() * 1000)}"
    params = {
        "callback": cb,
        "captcha_id": CAPTCHA_ID,
        "client_type": "web",
        "lot_number": load_data["lot_number"],
        "payload": load_data["payload"],
        "process_token": load_data["process_token"],
        "payload_protocol": load_data.get("payload_protocol", "1"),
        "pt": load_data.get("pt", "1"),
        "w": w,
    }

    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)

    headers = {
        "Referer": "https://www.leyu.me/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    resp = cr.get(url, impersonate="chrome110", headers=headers, timeout=30)
    text = resp.text

    # 检查是否成功
    if '"result":"success"' not in text:
        m = re.search(r"\((.*)\)$", text, re.DOTALL)
        if m:
            vdata = json.loads(m.group(1))
            result = vdata.get("data", {}).get("result", "unknown")
            fail_count = vdata.get("data", {}).get("fail_count", 0)
            print(f"  verify: result={result}, fail_count={fail_count}")
            raise VerifyError(result, fail_count)
        raise VerifyError("invalid-response")

    # 解析 JSONP 响应
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if not m:
        print("  verify: 无法解析 JSONP 响应")
        return None

    vdata = json.loads(m.group(1))
    seccode = vdata.get("data", {}).get("seccode", {})

    if not seccode:
        print("  verify: 响应中无 seccode")
        return None

    return {
        "captcha_output": seccode.get("captcha_output", ""),
        "gen_time": seccode.get("gen_time", ""),
        "pass_token": seccode.get("pass_token", ""),
    }


def _validate_geecheck(domain: str, lot_number: str, seccode: dict) -> bool:
    """调用 validateGeeCheckV2 API 验证验证码。"""
    validate_url = f"{domain}/site/api/v1/user/member/validateGeeCheckV2"
    validate_body = {
        "validate_way": 1,
        "lot_number": lot_number,
        "captcha_output": seccode.get("captcha_output", ""),
        "gen_time": seccode.get("gen_time", ""),
        "pass_token": seccode.get("pass_token", ""),
    }

    headers = {
        "Content-Type": "application/json",
        "Referer": f"{domain}/user/login",
        "User-Agent": "Mozilla/5.0",
        "X-API-CLIENT": "web",
        "X-API-SITE": "2001",
        "X-API-VERSION": "2.0.0",
    }

    resp = cr.post(
        validate_url,
        json=validate_body,
        headers=headers,
        impersonate="chrome110",
        timeout=15,
    )

    vresp = resp.json()
    status_code = vresp.get("status_code")

    if status_code == 6000:
        print(f"  validateGeeCheckV2: success")
        return True

    print(f"  validateGeeCheckV2: failed ({status_code}): {vresp.get('message', '')}")
    return False


def _do_login(domain: str, user: str, pwd_md5: str, lot_number: str) -> Optional[str]:
    """调用 login API 获取 X-API-TOKEN。"""
    login_url = f"{domain}/site/api/v1/user/login"
    login_body = {
        "name": user,
        "password": pwd_md5,
        "Kaptchcate": 0,
        "codeId": lot_number,
    }

    headers = {
        "Content-Type": "application/json",
        "Referer": f"{domain}/user/login",
        "User-Agent": "Mozilla/5.0",
        "X-API-CLIENT": "web",
        "X-API-SITE": "2001",
        "X-API-VERSION": "2.0.0",
    }

    resp = cr.post(
        login_url,
        json=login_body,
        headers=headers,
        impersonate="chrome110",
        timeout=15,
    )

    lresp = resp.json()

    if lresp.get("status_code") != 6000:
        print(f"  login: failed: {lresp.get('message', '')}")
        return None

    token = (lresp.get("data", {}) or {}).get("token", "")
    if not token:
        print("  login: no token in response")
        return None

    print(f"  login: success")
    return token


def _get_uuid(domain: str, api_token: str) -> str:
    """从 JWT API 获取 UUID。"""
    try:
        resp = cr.post(
            f"{domain}/site/api/v1/user/member/jwt",
            headers={
                "X-API-TOKEN": api_token,
                "Content-Type": "application/json",
                "Referer": f"{domain}/",
            },
            json={},
            impersonate="chrome110",
            timeout=15,
        )
        if resp.status_code == 200:
            site_jwt = resp.json().get("data", "")
            if site_jwt:
                parts = site_jwt.split(".")
                if len(parts) == 3:
                    payload = json.loads(
                        base64.urlsafe_b64decode(parts[1] + "==")
                    )
                    return payload.get("uuid", "")
    except Exception as e:
        print(f"  UUID 获取失败: {e}")

    return ""


async def login(
    user: str,
    pwd: str,
    captcha_token: str = "",
    *,
    geepass_token: str = "",
    jfbym_token: str = "",
    max_retries: int = 3,
) -> Optional[dict]:
    """纯 HTTP 乐鱼登录。

    打码平台 token 的解析方式:
      - geepass 仅使用 geepass_token 或 GEEPASS_TOKEN。
      - jfbym 使用 jfbym_token、旧版 captcha_token、JFBYM_TOKEN，
        再回退到旧版 CAPTCHA_TOKEN。
      - 旧版 captcha_token 和 CAPTCHA_TOKEN 不会用于 geepass。

    Args:
        user: 用户名
        pwd: 密码（明文，内部会做 MD5）
        captcha_token: 仅用于 jfbym 的旧版打码平台 token
        geepass_token: geepass 专用 token
        jfbym_token: jfbym 专用 token
        max_retries: 最大重试次数

    Returns:
        {"token": ..., "uuid": ..., "domain": ..., "lot_number": ...} 或 None
    """
    pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()
    domain = _get_domain()

    if not domain:
        print("❌ 无法解析域名")
        return None

    # 解析 token：显式专用 token 优先；旧版 token 仅映射到 jfbym。
    gp_token = geepass_token or os.getenv("GEEPASS_TOKEN", "")
    jf_token = (
        jfbym_token
        or captcha_token
        or os.getenv("JFBYM_TOKEN", "")
        or os.getenv("CAPTCHA_TOKEN", "")
    )
    solvers = _build_solvers(gp_token, jf_token)

    if not gp_token and not jf_token:
        print("❌ 没有提供任何打码平台 token")
        return None

    print(f"域名: {domain}")

    for retry in range(max_retries):
        print(f"\n--- 第 {retry + 1}/{max_retries} 次尝试 ---")

        # 1. 获取验证码
        print("[1/5] 获取验证码...")
        load_data = _fetch_captcha()
        if not load_data:
            print("  ❌ fetch_captcha 失败")
            continue

        print(f"  ✅ lot_number: {load_data['lot_number'][:20]}...")

        # 2. 识别坐标
        print("[2/5] 识别验证码...")
        challenge = CaptchaChallenge(
            lot_number=load_data["lot_number"],
            payload=load_data["payload"],
            process_token=load_data["process_token"],
            bg_url=load_data["bg_url"],
            ques_urls=load_data["ques_urls"],
            captcha_id=CAPTCHA_ID,
            pow_detail=load_data.get("pow_detail", {}),
            pt=load_data.get("pt", "1"),
            payload_protocol=load_data.get("payload_protocol", "1"),
        )

        try:
            solution = await _solve_captcha(challenge, solvers)
        except CaptchaSolveError:
            continue
        coords = solution.coords
        if not coords:
            print("  ❌ 验证码识别失败")
            continue

        print(f"  ✅ 坐标: {coords}")

        # 3. Verify
        print("[3/5] 验证验证码...")
        try:
            seccode = await _verify_captcha(load_data, coords)
        except VerifyError:
            print("  ❌ verify 失败")
            continue

        if not seccode:
            continue

        print("  ✅ seccode obtained")

        # 4. Validate
        print("[4/5] 校验验证码...")
        if not _validate_geecheck(domain, load_data["lot_number"], seccode):
            print("  ❌ validateGeeCheckV2 失败")
            continue

        # 5. Login
        print("[5/5] 登录...")
        api_token = _do_login(domain, user, pwd_md5, load_data["lot_number"])
        if not api_token:
            print("  ❌ 登录失败")
            continue

        # 6. 获取 UUID
        uuid_val = _get_uuid(domain, api_token)

        result = {
            "token": api_token,
            "uuid": uuid_val,
            "domain": domain,
            "lot_number": load_data["lot_number"],
        }

        print(f"\n✅ 登录成功!")
        print(f"   UUID:  {uuid_val}")

        return result

    print(f"\n❌ 所有 {max_retries} 次尝试均失败")
    return None


# 保持向后兼容的同步版本
def login_sync(user: str, pwd: str, **kwargs) -> Optional[dict]:
    """login() 的同步包装器。"""
    return asyncio.run(login(user, pwd, **kwargs))


if __name__ == "__main__":
    user_ = os.getenv("LEYU_USER", "")
    pwd_ = os.getenv("LEYU_PWD", "")
    gp_tok = os.getenv("GEEPASS_TOKEN", "")
    jf_tok = os.getenv("JFBYM_TOKEN", "")
    captcha_tok = os.getenv("CAPTCHA_TOKEN", "")

    if not user_ or not pwd_:
        print("请设置 LEYU_USER 和 LEYU_PWD 环境变量")
        print("打码平台 token（至少选一个）:")
        print("  GEEPASS_TOKEN  — geepass API token (api.geepass.cn)")
        print("  JFBYM_TOKEN    — jfbym API token (api.jfbym.com)")
        print("  CAPTCHA_TOKEN  — 旧版 jfbym token")
        sys.exit(1)

    result = asyncio.run(login(
        user_, pwd_,
        geepass_token=gp_tok,
        jfbym_token=jf_tok,
        captcha_token=captcha_tok,
    ))
    if result:
        safe = {k: v for k, v in result.items() if k != 'token'}
        print(json.dumps(safe, indent=2, ensure_ascii=False))
        sys.exit(0)
    else:
        sys.exit(1)
