"""纯 HTTP 验证码登录客户端 — 无浏览器登录（L3a 链路）。

流程: fetch_captcha → solver → generate_w → verify → validate → login

从 TokenManager._login_via_http 逐行迁出（Task 9），仅把 self.account →
account 参数、self._resolve_domain() → resolve_domain() or HDATA_DOMAIN。
"""

from __future__ import annotations

import json
import os
import time

from hdata.auth.captcha import fetch_captcha
from hdata.auth.captcha_solver import CaptchaChallenge
from hdata.auth.domain import resolve_domain
from hdata.auth.fingerprint import get_impersonate
from hdata.auth.geetest_signer import generate_w
from htools.utils.logger import get_logger

logger = get_logger(__name__)


async def http_login_with_captcha(account: str, user: str, pwd: str, solver) -> dict | None:
    """纯 HTTP 登录（无浏览器）。

    流程: fetch_captcha → solver → generate_w → verify → validate → login

    注意: verify 依赖坐标精度，当前 jfbym 坐标约 ±20px 偏移。
    如果 verify 持续返回 result=fail，可尝试 Capsolver 等替代打码平台。
    """
    from curl_cffi import requests as cr
    import hashlib, urllib.parse, re

    CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"

    # 1. 获取验证码
    ld = fetch_captcha()
    if not ld:
        logger.error(f"[{account}] fetch_captcha failed")
        return None

    # 2. 坐标识别
    challenge = CaptchaChallenge(
        lot_number=ld["lot_number"], payload=ld["payload"],
        process_token=ld["process_token"], bg_url=ld["bg_url"],
        ques_urls=ld["ques_urls"], captcha_id=CAPTCHA_ID,
    )
    try:
        solution = await solver.solve(challenge)
    except Exception as e:
        logger.error(f"[{account}] solver failed: {e}")
        return None

    # 3. 生成 w
    w = generate_w(ld, CAPTCHA_ID, solution.coords)

    # 4. verify
    cb = f"botion_{int(time.time() * 1000)}"
    params = {
        "callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": ld["lot_number"], "payload": ld["payload"],
        "process_token": ld["process_token"],
        "payload_protocol": ld.get("payload_protocol", "1"),
        "pt": ld.get("pt", "1"), "w": w,
    }
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    resp = cr.get(url, impersonate=get_impersonate(account),
                  headers={"Referer": "https://www.leyu.me/"}, timeout=30)
    text = resp.text

    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if not m:
        logger.error(f"[{account}] verify response parsing failed")
        return None
    vdata = json.loads(m.group(1))
    if vdata.get("data", {}).get("result") != "success":
        logger.warning(f"[{account}] verify failed: {vdata.get('data', {}).get('result')}")
        return None

    seccode = vdata.get("data", {}).get("seccode", {})

    # 5. validateGeeCheckV2
    domain = resolve_domain() or os.getenv("HDATA_DOMAIN", None)
    if not domain:
        return None
    validate_url = f"{domain}/site/api/v1/user/member/validateGeeCheckV2"
    validate_body = {
        "validate_way": 1,
        "lot_number": ld["lot_number"],
        "captcha_output": seccode.get("captcha_output", ""),
        "gen_time": seccode.get("gen_time", ""),
        "pass_token": seccode.get("pass_token", ""),
    }
    resp = cr.post(validate_url, json=validate_body,
                   headers={"Content-Type": "application/json",
                            "Referer": f"{domain}/"},
                   impersonate=get_impersonate(account), timeout=15)
    vresp = resp.json()
    if vresp.get("status_code") != 6000:
        logger.error(f"[{account}] validateGeeCheckV2 failed: {vresp}")
        return None

    # 6. login
    pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()
    login_body = {
        "name": user,
        "password": pwd_md5,
        "Kaptchcate": 0,
        "codeId": ld["lot_number"],
    }
    resp = cr.post(f"{domain}/site/api/v1/user/login",
                   json=login_body,
                   headers={"Content-Type": "application/json",
                            "Referer": f"{domain}/"},
                   impersonate=get_impersonate(account), timeout=15)
    lresp = resp.json()
    token = (lresp.get("data", {}) or {}).get("token", "")
    if lresp.get("status_code") == 6000 and token:
        logger.info(f"[{account}] HTTP login successful")
        return {"token": token, "domain": domain, "lot_number": ld["lot_number"]}

    logger.error(f"[{account}] login failed: {lresp.get('message', '')}")
    return None
