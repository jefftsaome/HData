#!/usr/bin/env python3
"""捕获真实 e-object：固定 Math.random → 已知 AES key → 解密 w → 得到真实 userresponse。

用法: export JFB_TOKEN=... && uv run python scripts/capture_e_object.py
"""

import asyncio, json, re, sys, os, binascii
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))
OUT = _PROJ / "data" / "real_e"
OUT.mkdir(parents=True, exist_ok=True)

FIXED_RANDOM = "aaaaaaaabbbbbbbb"  # 16 bytes = AES-128 key

async def main():
    from playwright.async_api import async_playwright
    from hdata.auth.captcha import fetch_captcha, solve

    JFB = os.environ.get("JFB_TOKEN", "")
    if not JFB: print("❌ JFB_TOKEN"); return

    captured = {}
    real_w = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # 拦截 bcaptcha.js，注入固定随机数
        async def handle_route(route):
            resp = await route.fetch()
            body = await resp.text()
            if "bcaptcha.js" in route.request.url and len(body) > 50000:
                inject = """
                ;(function(){
                    // 固定 Math.random → 使 AES key 可预测
                    var _counter = 0;
                    var _fixed = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9,
                                  0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95,
                                  0.12, 0.23, 0.34, 0.56, 0.67, 0.78, 0.89, 0.91];
                    Math.random = function() {
                        var v = _fixed[_counter % _fixed.length];
                        _counter++;
                        return v;
                    };
                })();
                """
                body = body + inject
            await route.fulfill(body=body, headers=resp.headers)
        await page.route("**/bcaptcha.js", handle_route)

        # 拦截 verify 请求
        async def on_req(req):
            if "bcaptcha.botion.com/verify" in req.url:
                qs = parse_qs(urlparse(req.url).query)
                real_w["w"] = qs.get("w", [""])[0]
                real_w["url"] = req.url
        page.on("request", on_req)

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

        for i in range(15):
            await asyncio.sleep(1)
            box = await page.evaluate("""() => {
                let e = document.querySelector('[class*=botion_box_layer]');
                if(e){let b=e.getBoundingClientRect();if(b.width>50&&b.height>50)return{x:b.x,y:b.y,w:b.width,h:b.height};}
                return null;
            }""")
            if box: print(f"弹窗 ({i+1}s): {box['w']:.0f}x{box['h']:.0f}"); break

        # jfbym 坐标
        data = fetch_captcha(f"{dm}/user/login")
        result = solve(data["bg_url"], data["ques_urls"], JFB)
        jfbym_pts = [[int(x),int(y)] for x,y in [p.split(',') for p in result['coords'].split('|')]] if result else []
        print(f"jfbym: {jfbym_pts}")

        print("""
============================================================
  现在手动点击正确位置！Math.random 已固定。
  点击后脚本自动解密 w 得到真实 e-object。
============================================================
""")
        for _ in range(60):
            await asyncio.sleep(1)
            if real_w.get("w"): break

        if real_w.get("w"):
            w_hex = real_w["w"]
            print(f"w={len(w_hex)} chars")
            (OUT / "real_w.txt").write_text(w_hex)
            print(f"💾 {OUT}/real_w.txt")

            # 尝试解密：用 Math.random 固定序列推导的所有可能 key
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            # rand_uid() = 4次 hex(int(65536*(1+Math.random())))[2:].zfill(4)[-4:]
            fixed_seq = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9,
                         0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95,
                         0.12, 0.23, 0.34, 0.56, 0.67, 0.78, 0.89, 0.91]
            aes_ct = bytes.fromhex(w_hex[:960])

            for start_idx in range(len(fixed_seq) - 3):
                vals = fixed_seq[start_idx:start_idx + 4]
                key_str = ''
                for v in vals:
                    key_str += hex(int(65536 * (1 + v)))[2:].zfill(4)[-4:]
                if len(key_str) != 16: continue
                try:
                    cipher = Cipher(algorithms.AES(key_str.encode()), modes.CBC(b'\x00'*16))
                    padded = cipher.decryptor().update(aes_ct) + cipher.decryptor().finalize()
                    plain = padded[:-padded[-1]].decode('utf-8')
                    obj = json.loads(plain)
                    print('\n✅ DECRYPTED! start_idx=%d key=%s' % (start_idx, key_str))
                    print(json.dumps(obj, indent=2, ensure_ascii=False)[:3000])
                    (OUT / 'real_e_object.json').write_text(json.dumps(obj, indent=2))
                    ur = obj.get('userresponse')
                    if ur:
                        print('\n真实 userresponse: %s' % ur)
                        print('jfbym userresponse:  %s' % jfbym_pts)
                    break
                except Exception:
                    continue
            else:
                print('❌ 所有 key 都解密失败，SDK 可能用了 crypto.getRandomValues')
                # 保存 RSA 部分供分析
                rsa_hex = w_hex[960:]
                print(f'RSA 加密的 key: {rsa_hex[:40]}...')
        else:
            print("❌ 未捕获 verify")

        await browser.close()

asyncio.run(main())
