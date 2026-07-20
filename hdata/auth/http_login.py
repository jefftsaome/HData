"""
纯HTTP乐鱼登录 — 完整无浏览器实现。

流程: fetch_captcha → geepass/jfbym solve → generate_w → verify → validateGeeCheckV2 → login → JWT

特性:
1. 完整的 validateGeeCheckV2 校验步骤
2. 自动域名解析
3. 从 verify 响应的 seccode 中提取 captcha_output/gen_time/pass_token
4. 完整的错误处理和重试机制
5. 支持 geepass(优先) 和 jfbym(备选) 双平台，各自独立的 API token

用法:
    from hdata.auth.http_login import login
    session = await login("username", "password", captcha_token="xxx")
    # 或分开指定:
    session = await login("username", "password", geepass_token="xxx", jfbym_token="yyy")
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
import hashlib
import json
import os
import re
import sys
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
from hdata.auth.api_sign import common_headers
from hdata.auth.fingerprint import leyu_finger

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"


class VerifyError(RuntimeError):
    def __init__(
        self,
        result: str,
        fail_count: int = 0,
        reason: str = "",
        *,
        diagnostics: dict | None = None,
    ):
        self.result = result
        self.fail_count = fail_count
        self.reason = reason
        self.diagnostics = diagnostics or {}
        super().__init__(f"verify {result}: {reason}; fail_count={fail_count}")


def _px(proxy: str) -> dict | None:
    """构造 curl_cffi proxies 参数（空串→None 直连，行为不变）。

    登录链路的所有平台请求必须走同一出口：token 绑定登录 IP
    （见 docs/代理接入.md），validateGeeCheckV2 返回的 user_ip
    也参与 X-API-FINGER 计算，混用出口会导致登录失败。
    """
    return {"http": proxy, "https": proxy} if proxy else None


def _get_domain() -> str:
    """获取乐鱼真实域名（缓存 + 探活 + 自动重解析）。"""
    domain = _resolve_domain(validate=True)
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


async def _verify_captcha(load_data: dict, coords: str,
                          proxy: str = "") -> dict:
    """Return a complete verify seccode or raise a typed failure."""
    diagnostics = {}
    w = generate_w(load_data, CAPTCHA_ID, coords, diagnostics=diagnostics)

    callback = f"botion_{int(time.time() * 1000)}"
    params = {
        "callback": callback,
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

    network_error = ""
    try:
        text = cr.get(url, impersonate="chrome110", headers=headers,
                      timeout=30, proxies=_px(proxy)).text
    except Exception as exc:
        network_error = type(exc).__name__
    if network_error:
        raise VerifyError(
            "network_error",
            reason=network_error,
            diagnostics=diagnostics,
        )

    match = re.search(r"^[^(]+\((.*)\)$", text, re.DOTALL)
    if not match:
        raise VerifyError("invalid_jsonp", diagnostics=diagnostics)
    payload = None
    json_error = False
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        json_error = True
    if json_error:
        raise VerifyError("invalid_jsonp", diagnostics=diagnostics)

    if not isinstance(payload, Mapping):
        raise VerifyError("invalid_jsonp", diagnostics=diagnostics)

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise VerifyError("invalid_jsonp", diagnostics=diagnostics)

    raw_result = data.get("result")
    result = (
        raw_result
        if isinstance(raw_result, str) and raw_result in {"success", "fail"}
        else "unexpected_result"
    )
    try:
        fail_count = int(data.get("fail_count") or 0)
    except (TypeError, ValueError):
        fail_count = 0
    if result != "success":
        raise VerifyError(result, fail_count, diagnostics=diagnostics)

    seccode = data.get("seccode")
    required = ("captcha_output", "gen_time", "pass_token")
    if not isinstance(seccode, Mapping) or any(
        not seccode.get(field) for field in required
    ):
        raise VerifyError(
            "incomplete_seccode",
            fail_count,
            diagnostics=diagnostics,
        )
    return {field: seccode[field] for field in required}


def _safe_status_code(value: object) -> int | str:
    return value if type(value) is int else "unexpected_status"


def _kaptchcate(domain: str, proxy: str = "") -> bool:
    """验证码预注册（浏览器在每次弹验证码前必调，status_code 6022 为成功）。"""
    try:
        resp = cr.post(
            f"{domain}/site/api/v1/user/member/kaptchcate",
            json={"kType": 4},
            headers=common_headers("/site/api/v1/user/member/kaptchcate",
                                   domain=domain),
            impersonate="chrome110",
            timeout=15,
            proxies=_px(proxy),
        )
        body = resp.json()
        ok = isinstance(body, Mapping) and body.get("status_code") == 6022
        print(f"  kaptchcate: {'success' if ok else 'unexpected ' + str(body.get('status_code'))}")
        return ok
    except Exception as exc:
        print(f"  kaptchcate: failed exception={type(exc).__name__}")
        return False


def _local_tz_offset() -> int:
    """等价于 JS new Date().getTimezoneOffset()（UTC+8 为 -480）。"""
    import datetime

    offset = datetime.datetime.now().astimezone().utcoffset()
    seconds = offset.total_seconds() if offset else 0
    return -int(seconds // 60)


def _validate_geecheck(domain: str, lot_number: str, seccode: dict,
                       proxy: str = "") -> str:
    """调用 validateGeeCheckV2 API 验证验证码。成功返回 user_ip，失败返回 ''。"""
    validate_url = f"{domain}/site/api/v1/user/member/validateGeeCheckV2"
    validate_body = {
        "validate_way": 1,
        "lot_number": lot_number,
        "captcha_output": seccode.get("captcha_output", ""),
        "gen_time": seccode.get("gen_time", ""),
        "pass_token": seccode.get("pass_token", ""),
    }

    try:
        resp = cr.post(
            validate_url,
            json=validate_body,
            headers=common_headers(
                "/site/api/v1/user/member/validateGeeCheckV2", domain=domain
            ),
            impersonate="chrome110",
            timeout=15,
            proxies=_px(proxy),
        )
    except Exception as exc:
        print(f"  validateGeeCheckV2: failed stage=validate exception={type(exc).__name__}")
        return ""

    try:
        vresp = resp.json()
    except Exception as exc:
        print(f"  validateGeeCheckV2: failed stage=validate exception={type(exc).__name__}")
        return ""
    raw_status_code = vresp.get("status_code") if isinstance(vresp, Mapping) else None
    status_code = _safe_status_code(raw_status_code)

    if status_code == 6000:
        # captcha_args.user_ip 是服务端看到的出口 IP，用于计算 X-API-FINGER
        data = vresp.get("data", {}) if isinstance(vresp, Mapping) else {}
        args = data.get("captcha_args", {}) if isinstance(data, Mapping) else {}
        user_ip = args.get("user_ip", "") if isinstance(args, Mapping) else ""
        print(f"  validateGeeCheckV2: success")
        return user_ip or "ok"

    print(f"  validateGeeCheckV2: failed stage=validate status={status_code}")
    return ""


def _do_login(domain: str, user: str, pwd_md5: str, lot_number: str, user_ip: str = "", proxy: str = "") -> Optional[str]:
    """调用 login API 获取 X-API-TOKEN。"""
    login_url = f"{domain}/site/api/v1/user/login"
    login_body = {
        "name": user,
        "password": pwd_md5,
        "Kaptchcate": 0,
        "codeId": lot_number,
    }

    # X-API-FINGER: fingerprintjs2 x64hash128（仅 login 携带）
    finger = ""
    if user_ip:
        try:
            finger = leyu_finger(user_ip, timezone_offset=_local_tz_offset())
        except Exception:
            finger = ""

    try:
        resp = cr.post(
            login_url,
            json=login_body,
            headers=common_headers(
                "/site/api/v1/user/login", domain=domain, finger=finger
            ),
            impersonate="chrome110",
            timeout=15,
            proxies=_px(proxy),
        )
    except Exception as exc:
        print(f"  login: failed stage=login exception={type(exc).__name__}")
        return None

    try:
        lresp = resp.json()
    except Exception as exc:
        print(f"  login: failed stage=login exception={type(exc).__name__}")
        return None

    raw_status_code = lresp.get("status_code") if isinstance(lresp, Mapping) else None
    status_code = _safe_status_code(raw_status_code)
    if status_code != 6000:
        print(f"  login: failed stage=login status={status_code}")
        return None

    login_data = lresp.get("data", {}) if isinstance(lresp, Mapping) else {}
    token = login_data.get("token", "") if isinstance(login_data, Mapping) else ""
    if not token:
        print("  login: no token in response")
        return None

    print(f"  login: success")
    return token


def _get_uuid(domain: str, api_token: str, proxy: str = "") -> str:
    """从 JWT API 获取 UUID。"""
    try:
        resp = cr.post(
            f"{domain}/site/api/v1/user/member/jwt",
            headers=common_headers(
                "/site/api/v1/user/member/jwt", token=api_token, domain=domain,
                referer_path="/",
            ),
            json={},
            impersonate="chrome110",
            timeout=15,
            proxies=_px(proxy),
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
    except Exception as exc:
        print(f"  UUID 获取失败: stage=uuid exception={type(exc).__name__}")

    return ""


async def login(
    user: str,
    pwd: str,
    captcha_token: str = "",
    *,
    geepass_token: str = "",
    jfbym_token: str = "",
    max_retries: int = 3,
    proxy: str = "",
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
        proxy: 代理 URL（如 http://user:pass@host:port），
               整条登录链路（验证码/校验/登录/JWT）走同一出口；
               空串走直连（默认，行为与之前一致）

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

        # 0. 验证码预注册（浏览器每次弹验证码前必调）
        print("[0/6] kaptchcate 预注册...")
        _kaptchcate(domain, proxy)  # 失败不阻断，与浏览器容错行为一致

        # 1. 获取验证码
        print("[1/6] 获取验证码...")
        load_data = _fetch_captcha(proxy=proxy)
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
        print("[3/6] 验证验证码...")
        try:
            seccode = await _verify_captcha(load_data, coords, proxy)
        except VerifyError as exc:
            fields = ",".join(exc.diagnostics.get("e_obj_fields", []))
            size = exc.diagnostics.get("e_obj_bytes", 0)
            print(
                f"  verify failed: result={exc.result}, "
                f"fail_count={exc.fail_count}, lot={load_data['lot_number'][:8]}..., "
                f"e_obj_bytes={size}, e_obj_fields={fields}"
            )
            continue

        if not seccode:
            continue

        print("  ✅ seccode obtained")

        # 4. Validate（返回 user_ip 用于计算 X-API-FINGER）
        print("[4/6] 校验验证码...")
        user_ip = _validate_geecheck(
            domain, load_data["lot_number"], seccode, proxy)
        if not user_ip:
            print("  ❌ validateGeeCheckV2 失败")
            continue

        # 5. Login
        print("[5/6] 登录...")
        api_token = _do_login(
            domain, user, pwd_md5, load_data["lot_number"],
            user_ip=user_ip if "." in user_ip else "",
            proxy=proxy,
        )
        if not api_token:
            print("  ❌ 登录失败")
            continue

        # 6. 获取 UUID
        print("[6/6] 获取 UUID...")
        uuid_val = _get_uuid(domain, api_token, proxy)

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
