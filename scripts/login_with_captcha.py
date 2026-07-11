#!/usr/bin/env python3
"""Playwright + jfbym 30112 自动登录。

流程: Playwright 填表 → GeeTest 弹窗 → jfbym 30112 拿坐标 → 点击 → SDK 自动验证登录

用法:
    uv run python scripts/login_with_captcha.py \
      --username "lds003" --password "xxx" --jfbym-token "xxx"
"""

import asyncio, hashlib, json, re, sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
SESSION_CACHE = _PROJ_ROOT / ".session_cache.json"


async def login(user: str, pwd: str, jfbym_token: str) -> dict | None:
    from playwright.async_api import async_playwright
    from hdata.auth.captcha import fetch_captcha

    pwd_md5 = hashlib.md5(pwd.encode()).hexdigest()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        login_body = {}
        async def on_resp(resp):
            if "/user/login" in resp.url and resp.method == "POST":
                login_body["text"] = await resp.text()
        page.on("response", on_resp)

        # 1. 域名
        print("1. 获取域名...")
        await page.goto("https://leyu.me", wait_until="commit", timeout=15000)
        await asyncio.sleep(2)
        dm = re.match(r"https://[^/]+", page.url).group(0)
        print(f"   {dm}")

        # 2. 登录页 + 填表
        print("2. 填表 Enter...")
        await page.goto(f"{dm}/user/login", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        inputs = page.locator("input")
        if await inputs.count() < 2: print("❌ 无输入框"); return None
        await inputs.nth(0).click(); await page.keyboard.type(user, delay=80)
        await inputs.nth(1).click(); await page.keyboard.type(pwd, delay=80)
        await page.keyboard.press("Enter")

        # 3. 等弹窗
        print("3. 等验证码弹窗...")
        for _ in range(10):
            await asyncio.sleep(1)
            box = await page.evaluate(
                'document.querySelector("[class*=botion_box_btn]")?.getBoundingClientRect() || null')
            if box and box.get("height", 0) > 50:
                print(f"   弹窗: {box['width']:.0f}x{box['height']:.0f}")
                break
        else: print("❌ 弹窗未出现"); return None

        # 4. jfbym 30112 拿坐标
        print("4. jfbym 30112...")
        from hdata.auth.captcha import solve
        data = fetch_captcha(f"{dm}/user/login")
        if not data: print("❌ fetch_captcha 失败"); return None
        result = solve(data["bg_url"], data["ques_urls"], jfbym_token)
        if not result: print("❌ solve 失败"); return None
        coords = result["coords"]
        pts = [x.split(",") for x in coords.split("|")]
        print(f"   坐标: {len(pts)} 个 — {coords}")

        # 5. 点击 - 坐标是相对于 botion_box_btn 的
        print("5. 点击坐标...")
        box_el = page.locator("[class*=botion_box_btn]").first
        popup_box = await box_el.bounding_box()
        if not popup_box:
            # fallback: 用 page 上的 box 数据
            popup_box = box
        for i, (x, y) in enumerate(pts):
            abs_x = popup_box["x"] + int(x)
            abs_y = popup_box["y"] + int(y)
            print(f"   点击 {i+1}: ({abs_x:.0f}, {abs_y:.0f})")
            await page.mouse.click(abs_x, abs_y)
            await asyncio.sleep(0.4)

        # 6. 等 SDK 验证 + 登录
        print("6. 等登录...")
        for _ in range(30):
            await asyncio.sleep(1)
            if login_body:
                data = json.loads(login_body["text"])
                print(f"   login: {str(data)[:200]}")
                if data.get("status_code") == 6000:
                    api_token = (data.get("data", {}) or {}).get("token", "")
                    if api_token:
                        print(f"   ✅ token: {api_token[:40]}...")
                        # 提取 session
                        ls = await page.evaluate(
                            "JSON.stringify({u:localStorage.getItem('uuidToBase64')||'',"
                            "t:localStorage.getItem('X-API-TOKEN')||'',"
                            "id:localStorage.getItem('_uuid')||''})")
                        ls = json.loads(ls)
                        session = {"token": ls.get("t", api_token),
                                   "uuid": ls.get("id", ""),
                                   "uuidToBase64": ls.get("u", ""),
                                   "cookies": "", "domain": dm}
                        SESSION_CACHE.write_text(json.dumps(session, indent=2,
                                                            ensure_ascii=False))
                        print("💾 Session 已缓存")
                        await browser.close(); return session
                print(f"   ❌ 登录失败"); break
        await browser.close(); return None


async def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--jfbym-token", required=True)
    args = p.parse_args()
    s = await login(args.username, args.password, args.jfbym_token)
    if s:
        print("\n✅ 登录成功！")
        if s.get("uuidToBase64"):
            from hdata.auth.token_manager import get_token
            try: get_token(s); print("✅ JWT 已刷新")
            except Exception as e: print(f"⚠️ JWT: {e}")
        return 0
    print("\n❌ 登录失败"); return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
