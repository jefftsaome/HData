"""验证码识别 — 纯 HTTP + jfbym 平台。

GeeTest v4 文字点选:
  fetch_captcha() → lot_number + bg_url + ques_urls
  ocr_ques()      → jfbym 10114 识别参考字顺序
  solve()         → jfbym 6246 返回背景图坐标
"""

import base64
import json
import re
import time
import uuid
from curl_cffi import requests as cr
from htools.utils.time import now_ms

BOTION_LOAD = "https://bcaptcha.botion.com/load"
BOTION_STATIC = "https://static.botion.com"
CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFB_API = "http://api.jfbym.com/api/YmServer/customApi"


def _get_domain() -> str:
    resp = cr.get("https://leyu.me", impersonate="chrome110",
                  timeout=10, allow_redirects=True)
    m = re.match(r"https://[^/]+", resp.url)
    return m.group(0) if m else ""


def fetch_captcha(page_url: str = "") -> dict | None:
    if not page_url:
        domain = _get_domain()
        if not domain: return None
        page_url = f"{domain}/user/login"

    challenge = str(uuid.uuid4())
    cb = f"geetest_{now_ms()}"
    risk_type = "word"
    url = f"{BOTION_LOAD}?captcha_id={CAPTCHA_ID}&challenge={challenge}&client_type=web&risk_type={risk_type}&lang=zh-cn&callback={cb}"

    resp = cr.get(url, impersonate="chrome110", headers={"Referer": page_url}, timeout=15)
    if resp.status_code != 200: return None
    m = re.search(r"\((.*)\)$", resp.text, re.DOTALL)
    if not m: return None
    outer = json.loads(m.group(1))
    if outer.get("status") != "success": return None
    data = outer.get("data", {})

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


def ocr_ques(ques_urls: list[str], token: str) -> list[str]:
    """jfbym 10118 识别参考字，返回按序文字列表。"""
    chars = []
    for url in ques_urls:
        img = cr.get(url, impersonate="chrome110", timeout=10).content
        b64 = base64.b64encode(img).decode()
        for attempt in range(3):
            try:
                r = cr.post(JFB_API,
                            json={"token": token, "type": "10118", "image": b64, "extra": "{}"},
                            headers={"Content-Type": "application/json"}, timeout=30).json()
                if r.get("code") == 10000:
                    c = (r.get("data", {}) or {}).get("data", "")
                    if c: chars.append(c)
                break
            except Exception:
                time.sleep(2)
    return chars


def solve(bg_url: str, ques_urls: list[str], token: str, bg_base64: str = "") -> dict | None:
    """jfbym 30112 识别背景图点击坐标。

    Args:
        bg_url: 背景图 URL（GeeTest CDN 原图）
        ques_urls: 参考字图 URL 列表
        token: jfbym API token
        bg_base64: 若提供，直接使用此 base64 代替下载 bg_url
    """
    """jfbym 30112 一步到位——传背景图+三张参考字图，直接返回坐标。

    无需单独 OCR，jfbym 内部处理参考字识别+坐标定位。
    """
    if bg_base64:
        bg_b64 = bg_base64
    else:
        bg_b64 = base64.b64encode(
            cr.get(bg_url, impersonate="chrome110", timeout=15).content).decode()

    body = {"token": token, "type": "31111", "image": bg_b64, "extra": "je4_click"}
    for i, url in enumerate(ques_urls):
        ref_b64 = base64.b64encode(
            cr.get(url, impersonate="chrome110", timeout=10).content).decode()
        body[f"image_label{i+1}"] = ref_b64

    for attempt in range(6):
        r = cr.post(JFB_API, json=body,
                    headers={"Content-Type": "application/json"}, timeout=60).json()
        if r.get("code") == 10000:
            d = r.get("data", {})
            return {"coords": d.get("data", ""), "again_tag": d.get("again_tag", 0)}
        elif r.get("code") == 10009:
            time.sleep(3); continue
        else:
            break
    return None
