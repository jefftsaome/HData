"""纯 HTTP 乐鱼登录 — 完整链路: fetch_captcha → jfbym → w → verify → validate → login.

用法:
    JFBYM_TOKEN=xxx LEYU_USER=xxx LEYU_PWD=xxx uv run python scripts/pure_http_login.py

依赖:
    - JFBYM_TOKEN: 打码平台 API token
    - LEYU_USER / LEYU_PWD: 乐鱼登录凭据

注意事项:
    - verify 依赖坐标精度，当前 jfbym 的 GeeTest v4 文字点选坐标精度约 ±20px
    - 如果 verify 持续返回 result=fail，请更换打码服务（如 capsolver/2captcha）
    - 已有有效 session 时，TokenManager L0/L1 路径可直接使用，无需走完整登录
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "we4578")


def get_domain() -> str:
    """获取乐鱼真实域名。"""
    from curl_cffi import requests as cr
    resp = cr.get("https://leyu.me", impersonate="chrome110",
                  timeout=10, allow_redirects=True)
    m = re.match(r"https://[^/]+", resp.url)
    return m.group(0) if m else ""


async def main() -> dict | None:
    if not JFBYM_TOKEN:
        print("❌ 需要设置 JFBYM_TOKEN")
        return None
    
    # ── 1. 获取验证码数据 ──
    print("\n[1/6] 获取验证码挑战数据...")
    from hdt.auth.captcha import fetch_captcha
    ld = fetch_captcha()
    if not ld:
        print("  ❌ fetch_captcha 失败")
        return None
    print(f"  ✅ lot_number: {ld['lot_number']}")
    
    # ── 2. jfbym 识别 ──
    print("\n[2/6] jfbym 识别坐标...")
    from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
    solver = JfbymSolver(api_token=JFBYM_TOKEN)
    challenge = CaptchaChallenge(
        lot_number=ld["lot_number"], payload=ld["payload"],
        process_token=ld["process_token"], bg_url=ld["bg_url"],
        ques_urls=ld["ques_urls"], captcha_id=CAPTCHA_ID,
    )
    try:
        sol = await solver.solve(challenge)
        print(f"  ✅ 坐标: {sol.coords}")
    except Exception as e:
        print(f"  ❌ jfbym 失败: {e}")
        return None
    
    # ── 3. 生成 w 参数 ──
    print("\n[3/6] 生成 w 参数...")
    from hdt.auth.geetest_signer import generate_w
    w = generate_w(ld, CAPTCHA_ID, sol.coords)
    print(f"  ✅ w: {len(w)} hex chars (AES: {(len(w)-256)//2}B, RSA: 128B)")
    
    # ── 4. verify API ──
    print("\n[4/6] 调用 botion verify API...")
    from curl_cffi import requests as cr
    
    cb = f"botion_{int(time.time() * 1000)}"
    params = {
        "callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": ld["lot_number"], "payload": ld["payload"],
        "process_token": ld["process_token"],
        "payload_protocol": ld.get("payload_protocol", "1"),
        "pt": ld.get("pt", "1"), "w": w,
    }
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/",
                           "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                  timeout=30)
    text = resp.text
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if not m:
        print(f"  ❌ 无法解析 verify 响应: {text[:200]}")
        return None
    
    vdata = json.loads(m.group(1))
    vstatus = vdata.get("status")
    vresult = vdata.get("data", {}).get("result")
    vfail = vdata.get("data", {}).get("fail_count")
    print(f"  status={vstatus}, result={vresult}, fail_count={vfail}")
    
    if vresult != "success":
        print(f"  ❌ verify 失败, 完整响应: {json.dumps(vdata, ensure_ascii=False)[:300]}")
        return None
    
    seccode = vdata.get("data", {}).get("seccode", {})
    captcha_output = seccode.get("captcha_output", "")
    pass_token = seccode.get("pass_token", "")
    gen_time = seccode.get("gen_time", "")
    print(f"  ✅ captcha_output: {captcha_output[:40]}...")
    print(f"  ✅ pass_token: {pass_token[:40]}...")
    
    # ── 5. validateGeeCheckV2 ──
    print("\n[5/6] validateGeeCheckV2...")
    domain = get_domain()
    if not domain:
        print("  ❌ 域名解析失败")
        return None
    print(f"  域名: {domain}")
    
    validate_url = f"{domain}/site/api/v1/user/member/validateGeeCheckV2"
    validate_body = {
        "validate_way": 1,
        "lot_number": ld["lot_number"],
        "captcha_output": captcha_output,
        "gen_time": gen_time,
        "pass_token": pass_token,
    }
    
    resp = cr.post(validate_url, json=validate_body,
                   headers={
                       "Content-Type": "application/json",
                       "Referer": f"{domain}/user/login",
                       "User-Agent": "Mozilla/5.0",
                   },
                   impersonate="chrome110", timeout=15)
    
    vresp = resp.json()
    vcode = vresp.get("status_code")
    vmsg = vresp.get("message", "")
    vresult = vresp.get("data", {}).get("result", "")
    print(f"  status_code={vcode}, message={vmsg}, result={vresult}")
    if vcode != 6000:
        print(f"  ❌ validate 失败: {json.dumps(vresp, ensure_ascii=False)[:300]}")
        return None
    print(f"  ✅ validate 成功")
    
    # ── 6. Login ──
    print("\n[6/6] 登录...")
    pwd_md5 = hashlib.md5(LEYU_PWD.encode()).hexdigest()
    login_url = f"{domain}/site/api/v1/user/login"
    login_body = {
        "name": LEYU_USER,
        "password": pwd_md5,
        "Kaptchcate": 0,
        "codeId": ld["lot_number"],
    }
    
    resp = cr.post(login_url, json=login_body,
                   headers={
                       "Content-Type": "application/json",
                       "Referer": f"{domain}/user/login",
                       "User-Agent": "Mozilla/5.0",
                   },
                   impersonate="chrome110", timeout=15)
    
    lresp = resp.json()
    lcode = lresp.get("status_code")
    lmsg = lresp.get("message", "")
    token = (lresp.get("data", {}) or {}).get("token", "")
    print(f"  status_code={lcode}, message={lmsg}")
    if lcode == 6000 and token:
        print(f"\n✅✅✅ 登录成功!")
        print(f"   TOKEN: {token[:80]}...")
        
        # Return session
        session = {
            "token": token,
            "lot_number": ld["lot_number"],
            "domain": domain,
        }
        
        # Try to get uuid from jwt API
        try:
            jwt_resp = cr.post(
                f"{domain}/site/api/v1/user/member/jwt",
                headers={"X-API-TOKEN": token, "Content-Type": "application/json",
                         "Referer": f"{domain}/"},
                json={}, impersonate="chrome110", timeout=15,
            )
            if jwt_resp.status_code == 200:
                site_jwt = jwt_resp.json().get("data", "")
                if site_jwt:
                    parts = site_jwt.split(".")
                    if len(parts) == 3:
                        payload = json.loads(
                            base64.urlsafe_b64decode(parts[1] + "=="))
                        session["uuid"] = payload.get("uuid", "")
                        print(f"   UUID: {session.get('uuid', '')}")
        except Exception as e:
            print(f"   UUID 获取失败: {e}")
        
        return session
    else:
        print(f"  ❌ 登录失败: {json.dumps(lresp, ensure_ascii=False)[:300]}")
        return None


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print(f"\n✅ 最终 session:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0)
    else:
        sys.exit(1)
