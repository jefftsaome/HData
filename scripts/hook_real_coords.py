#!/usr/bin/env python3
"""Hook GeeTest SDK 截获真实 e-object（含正确坐标）。

用法: export JFB_TOKEN=... && uv run python scripts/hook_real_coords.py
"""

import asyncio, json, re, sys, os
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))
OUT = _PROJ / "data" / "real_coords"
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    from playwright.async_api import async_playwright
    from hdata.auth.captcha import fetch_captcha, solve

    JFB = os.environ.get("JFB_TOKEN", "")
    if not JFB: print("❌ JFB_TOKEN"); return

    real_e = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # 拦截 bcaptcha.js 注入 hook
        async def handle_route(route):
            resp = await route.fetch()
            body = await resp.text()
            if "bcaptcha.js" in route.request.url:
                # Hook JSON.stringify 来捕获 e-object
                inject = """
                ;(function(){
                    var origStringify = JSON.stringify;
                    JSON.stringify = function(obj) {
                        if (obj && obj.userresponse && obj.lot_number) {
                            window.__REAL_EOBJ__ = JSON.parse(origStringify(obj));
                            console.log('[HOOK] Captured e-object with userresponse:', JSON.stringify(obj.userresponse));
                        }
                        return origStringify.apply(this, arguments);
                    };
                })();
                """
                body = body + inject
            await route.fulfill(body=body, headers=resp.headers)
        await page.route("**/bcaptcha.js", handle_route)

        await page.goto("https://leyu.me", wait_until="commit", timeout=15000)
        await asyncio.sleep(2)
        dm = re.match(r"https://[^/]+", page.url).group(0)
        print(f"域名: {dm}")
        await page.goto(f"{dm}/user/login", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)

        USER = os.environ.get("LEYU_USER", "lds003")
        PASS = os.environ.get("LEYU_PASS", "lds19830413")
        inputs = page.locator("input")
        await inputs.nth(0).click(); await page.keyboard.type(USER, delay=80)
        await inputs.nth(1).click(); await page.keyboard.type(PASS, delay=80)
        await page.keyboard.press("Enter")

        # 等弹窗
        for i in range(15):
            await asyncio.sleep(1)
            box = await page.evaluate("""() => {
                let e = document.querySelector('[class*=botion_box_layer]');
                if(e){let b=e.getBoundingClientRect();if(b.width>50&&b.height>50)return{x:b.x,y:b.y,w:b.width,h:b.height};}
                return null;
            }""")
            if box:
                print(f"弹窗 ({i+1}s): {box['w']:.0f}x{box['h']:.0f}")
                break

        # jfbym 坐标
        data = fetch_captcha(f"{dm}/user/login")
        result = solve(data["bg_url"], data["ques_urls"], JFB)
        jfbym_pts = [[int(x), int(y)] for x,y in [p.split(",") for p in result["coords"].split("|")]] if result else []
        print(f"jfbym: {jfbym_pts}")

        print(f"""
{'='*60}
  现在手动点击正确位置！点击正确弹窗消失。
  Hook 会自动捕获 GeeTest SDK 加密前的真实 e-object。
{'='*60}
""")
        # 同时捕获 verify 请求（比 JSON hook 更可靠）
        verify_w = {}
        async def on_req(req):
            if "bcaptcha.botion.com/verify" in req.url:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(req.url).query)
                verify_w["w"] = qs.get("w", [""])[0]
                verify_w["url"] = req.url
        page.on("request", on_req)

        for _ in range(60):
            await asyncio.sleep(1)
            if verify_w.get("w"):
                print(f"\n✅ 捕获真实 w ({len(verify_w['w'])} chars)")
                (OUT / "real_w.txt").write_text(verify_w["w"])
                (OUT / "real_verify_url.txt").write_text(verify_w["url"])
                break

        if verify_w.get("w"):
            print(f"   jfbym: {jfbym_pts}")
            # 真实 w 短 vs 我们生成的长 → 对比分析
            from hdata.auth.geetest_signer import _rand_uid, _encrypt_aes, _encrypt_rsa
            import hashlib, binascii
            data2 = fetch_captcha(f"https://www.1d8e47.vip:9249/user/login")
            pd = data2['pow_detail']
            msg = pd['version'] + '|' + str(pd['bits']) + '|' + pd['hashfunc'] + '|' + pd['datetime'] + '|eaffad4f65a38a259ae369faf0c2f1a3|' + data2['lot_number'] + '||'
            h = _rand_uid()
            for label, pts in [("jfbym原序", jfbym_pts),
                                ("按y升序", sorted(jfbym_pts, key=lambda p: p[1])),
                                ("按y降序", sorted(jfbym_pts, key=lambda p: -p[1])),
                                ("按x升序", sorted(jfbym_pts, key=lambda p: p[0]))]:
                e = {'lot_number':data2['lot_number'],'pow_msg':msg+h,
                     'pow_sign':hashlib.md5((msg+h).encode()).hexdigest(),'userresponse':pts,
                     'biht':'1426265548','ep':'123','lang':'zh'}
                key = _rand_uid()
                w = binascii.hexlify(_encrypt_aes(json.dumps(e,separators=(',',':')), key)).decode() + _encrypt_rsa(key)
                print(f"   {label}: w={len(w)} chars (真实w={len(verify_w['w'])} chars)")
            print(f"💾 {OUT}/")
        else:
            print("❌ 未捕获 verify —— 点击未被 GeeTest 接受")

        await browser.close()

asyncio.run(main())
