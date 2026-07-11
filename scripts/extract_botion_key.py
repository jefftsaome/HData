#!/usr/bin/env python3
"""拦截 bcaptcha.js 注入 RSA 公钥提取 + 捕获真实 w 参数。

用法: export JFB_TOKEN=... && uv run python scripts/extract_botion_key.py
"""

import asyncio, json, re, sys, os
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))

async def main():
    from playwright.async_api import async_playwright
    from hdata.auth.captcha import fetch_captcha, solve

    JFB = os.environ.get("JFB_TOKEN", "")
    if not JFB: print("❌ JFB_TOKEN not set"); return
    captured = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        async def on_request(req):
            if "bcaptcha.botion.com/verify" in req.url:
                qs = parse_qs(urlparse(req.url).query)
                captured["w"] = qs.get("w", [""])[0]
                print(f"\n🎯 真实 w ({len(captured['w'])} chars): {captured['w'][:150]}...")
                (_PROJ / "data/real_w.txt").write_text(captured["w"])
                print("💾 data/real_w.txt")

        page.on("request", on_request)

        # 拦截 bcaptcha.js 插入密钥提取
        async def handle_route(route):
            resp = await route.fetch()
            body = await resp.text()
            if "bcaptcha.js" in route.request.url or "gct4." in route.request.url:
                inject = """
                ;(function(){
                    try {
                        // Hook RSA encrypt 函数
                        var origEncrypt = window.__botion_encrypt;
                        // 遍历查找包含 10001 的函数
                        var found = [];
                        for (var k in window) {
                            try {
                                var v = window[k];
                                if (typeof v === 'function') {
                                    var s = v.toString();
                                    if (s.indexOf('10001') > 0 && s.length < 3000) {
                                        found.push({key: k, src: s.substring(0, 2000)});
                                    }
                                }
                                if (typeof v === 'object' && v && k.length < 20) {
                                    var ks = Object.keys(v);
                                    if (ks.some(x => x.toLowerCase().includes('encrypt') || x.toLowerCase().includes('rsa'))) {
                                        found.push({key: k, methods: ks.slice(0, 10)});
                                    }
                                }
                            } catch(e) {}
                        }
                        window.__EXTRACTED__ = found;
                    } catch(e) { window.__EXTRACTED__ = ['error: '+e.message]; }
                })();
                """
                body = body.replace("function udBgW", inject + "\nfunction udBgW")
            await route.fulfill(body=body, headers=resp.headers)

        await page.route("**/*.js", handle_route)

        await page.goto("https://leyu.me", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)
        dm = re.match(r"https://[^/]+", page.url).group(0)
        await page.goto(f"{dm}/user/login", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        inputs = page.locator("input")
        await inputs.nth(0).click(); await page.keyboard.type("test", delay=80)
        await inputs.nth(1).click(); await page.keyboard.type("test", delay=80)
        await page.keyboard.press("Enter")

        box = None
        for _ in range(15):
            await asyncio.sleep(1)
            box = await page.evaluate(
                '()=>{let e=document.querySelector("[class*=botion_box_btn]");if(!e)return null;let r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}}')
            if box and box["height"] > 50:
                print(f"弹窗: {box['width']:.0f}x{box['height']:.0f}")
                break

        if not box: print("弹窗未出现"); return

        # 提取 JS 注入的结果
        extracted = await page.evaluate("JSON.stringify(window.__EXTRACTED__ || [])")
        print(f"\nJS 提取结果: {extracted[:2000]}")

        # jfbym 坐标 + 点击
        data = fetch_captcha(f"{dm}/user/login")
        result = solve(data["bg_url"], data["ques_urls"], JFB)
        if not result: print("solve failed"); return
        coords = result["coords"]
        print(f"jfbym: {coords}")
        for pt in coords.split("|"):
            x, y = pt.split(",")
            await page.mouse.click(box["x"] + int(x), box["y"] + int(y))
            await asyncio.sleep(0.3)

        await asyncio.sleep(5)
        if captured.get("w"):
            print(f"\n✅ w captured!")
        else:
            print("\n❌ w not captured")
        await browser.close()

asyncio.run(main())
