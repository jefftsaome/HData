"""验证码获取 — 纯 HTTP。

GeeTest v4 文字点选:
  fetch_captcha() → lot_number + bg_url + ques_urls
"""

import json
import re
import time
import uuid
from curl_cffi import requests as cr
from htools.utils.time import now_ms
from loguru import logger

from hdata.auth import login_trace
from hdata.auth.fingerprint import get_impersonate

BOTION_LOAD = "https://bcaptcha.botion.com/load"
BOTION_STATIC = "https://static.botion.com"
CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"


def _get_domain() -> str:
    resp = cr.get("https://leyu.me", impersonate=get_impersonate(),
                  timeout=10, allow_redirects=True)
    m = re.match(r"https://[^/]+", resp.url)
    return m.group(0) if m else ""


def fetch_captcha(page_url: str = "", proxy: str = "") -> dict | None:
    if not page_url:
        domain = _get_domain()
        if not domain: return None
        page_url = f"{domain}/user/login"

    challenge = str(uuid.uuid4())
    cb = f"geetest_{now_ms()}"
    risk_type = "word"
    url = f"{BOTION_LOAD}?captcha_id={CAPTCHA_ID}&challenge={challenge}&client_type=web&risk_type={risk_type}&lang=zh-cn&callback={cb}"

    proxies = {"http": proxy, "https": proxy} if proxy else None
    t0 = time.monotonic()
    try:
        resp = cr.get(url, impersonate=get_impersonate(), headers={"Referer": page_url},
                      timeout=15, proxies=proxies)
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.debug("fetch_captcha 请求异常: {}: {} ({}ms, proxy={})",
                     type(exc).__name__, exc, elapsed, proxy or "直连")
        login_trace.emit(
            "captcha_load", method="GET", url=url,
            elapsed_ms=elapsed, ok=False,
            summary={"error": type(exc).__name__}, source="http_login")
        return None
    if resp.status_code != 200:
        logger.debug("fetch_captcha HTTP {} (proxy={})", resp.status_code, proxy or "直连")
        login_trace.emit(
            "captcha_load", method="GET", url=url, status=resp.status_code,
            elapsed_ms=int((time.monotonic() - t0) * 1000), ok=False,
            summary={"error": "bad_status"}, source="http_login")
        return None
    m = re.search(r"\((.*)\)$", resp.text, re.DOTALL)
    if not m:
        logger.debug("fetch_captcha JSONP 解析失败: {} (proxy={})", resp.text[:200], proxy or "直连")
        login_trace.emit(
            "captcha_load", method="GET", url=url, status=resp.status_code,
            elapsed_ms=int((time.monotonic() - t0) * 1000), ok=False,
            summary={"error": "invalid_jsonp"}, source="http_login")
        return None
    outer = json.loads(m.group(1))
    if outer.get("status") != "success":
        logger.debug("fetch_captcha 业务失败: {} (proxy={})", outer, proxy or "直连")
        login_trace.emit(
            "captcha_load", method="GET", url=url, status=resp.status_code,
            elapsed_ms=int((time.monotonic() - t0) * 1000), ok=False,
            summary=outer, source="http_login")
        return None
    data = outer.get("data", {})

    login_trace.emit(
        "captcha_load", method="GET", url=url, status=resp.status_code,
        elapsed_ms=int((time.monotonic() - t0) * 1000), ok=True,
        summary={"lot_number": data.get("lot_number", ""),
                 "captcha_type": data.get("captcha_type", "")},
        source="http_login")
    return {
        "lot_number": data.get("lot_number", ""),
        "payload": data.get("payload", ""),
        "process_token": data.get("process_token", ""),
        "pow_detail": data.get("pow_detail", {}),
        "pt": data.get("pt", "1"),
        "payload_protocol": data.get("payload_protocol", "1"),
        "captcha_type": data.get("captcha_type", "word"),
        "bg_url": f"{BOTION_STATIC}/{data.get('imgs', '')}",
        "ques_urls": [f"{BOTION_STATIC}/{p}" for p in data.get("ques", [])],
    }
