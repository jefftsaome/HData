"""纯 HTTP 乐鱼登录 — 无浏览器依赖。

流程: fetch_captcha → jfbym 31111 → w 参数 → verify → login → session

用法:
    from hdt.auth.http_login import login
    session = login("username", "password", "jfbym_token")
    # session 自动保存为 account 对应的缓存
"""

import hashlib
import json
import re
import time
import urllib.parse
from curl_cffi import requests as cr
from hdt.auth.captcha import fetch_captcha, solve
from hdt.auth.geetest_signer import generate_w
from htools.utils.time import now_ms

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"


def _get_domain() -> str:
    resp = cr.get("https://leyu.me", impersonate="chrome110",
                  timeout=10, allow_redirects=True)
    m = re.match(r"https://[^/]+", resp.url)
    return m.group(0) if m else ""


def login(user: str, pwd: str, jfbym_token: str) -> dict | None:
    """纯 HTTP 登录乐鱼，返回 session dict。

    注意: verify 结果依赖坐标精度。使用 CaptchaSolver 抽象类（hdt.auth.captcha_solver）
    替换 sdk_flow_captured.json 中的打码平台可提高成功率。

    Returns: {"token": X-API-TOKEN, "uuid": ..., "uuidToBase64": ..., "domain": ...}
    """
    pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()

    print("[1/4] 获取验证码 + jfbym 识别...")
    data = fetch_captcha()
    if not data: print("  ❌ GeeTest load"); return None
    result = solve(data["bg_url"], data["ques_urls"], jfbym_token)
    if not result: print("  ❌ jfbym solve"); return None
    coords = result["coords"]
    pts = [[int(x), int(y)] for x, y in [p.split(",") for p in coords.split("|")]]
    print(f"  coords: {pts}")

    print("[2/4] GeeTest verify...")
    w = generate_w(data, CAPTCHA_ID, coords)
    cb = f"botion_{now_ms()}"
    params = {
        "callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": data["lot_number"], "payload": data["payload"],
        "process_token": data["process_token"],
        "payload_protocol": data["payload_protocol"], "pt": data["pt"], "w": w,
    }
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/"}, timeout=30)
    text = resp.text
    if '"result":"success"' not in text:
        print(f"  ❌ verify: {text[:200]}"); return None
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if not m: print("  ❌ parse verify"); return None
    seccode = json.loads(m.group(1)).get("data", {}).get("seccode", "")
    print(f"  ✅ seccode: {seccode[:40]}...")

    print("[3/4] 登录...")
    domain = _get_domain()
    resp = cr.post(
        f"{domain}/site/api/v1/user/login",
        json={"name": user, "password": pwd_md5, "Kaptchcate": 0,
              "codeId": data["lot_number"]},
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0",
                 "Referer": f"{domain}/user/login"},
        impersonate="chrome110", timeout=15,
    )
    login_data = resp.json()
    if login_data.get("status_code") != 6000:
        print(f"  ❌ login: {resp.text[:200]}"); return None
    api_token = (login_data.get("data", {}) or {}).get("token", "")
    if not api_token: print("  ❌ no token"); return None
    print(f"  ✅ token: {api_token[:40]}...")

    print("[4/4] 提取 session...")
    resp = cr.post(
        f"{domain}/site/api/v1/user/member/jwt",
        headers={"X-API-TOKEN": api_token, "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0", "Referer": f"{domain}/"},
        json={}, impersonate="chrome110", timeout=15,
    )
    uuid_val = ""
    if resp.status_code == 200:
        site_jwt = (resp.json()).get("data", "")
        if site_jwt:
            parts = site_jwt.split(".")
            if len(parts) == 3:
                import base64 as _b64
                payload = json.loads(_b64.urlsafe_b64decode(parts[1] + "=="))
                uuid_val = payload.get("uuid", "")

    return {
        "token": api_token, "uuid": uuid_val, "uuidToBase64": "",
        "cookies": "", "domain": domain,
    }
