#!/usr/bin/env python3
"""用固定 getRandomValues 让 SDK 产生可预测的 AES key，解密 w 得真实 e-object。

用法: export JFB_TOKEN=... && uv run python scripts/hook_crypto.py
"""

import asyncio, json, re, sys, os, binascii
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))
OUT = _PROJ / "data" / "crypto_hook"
OUT.mkdir(parents=True, exist_ok=True)

INJECT = """
// 固定 Math.random，记录调用序列
var _fixedRand = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9,
                  0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95,
                  0.12, 0.23, 0.34, 0.56, 0.67, 0.78, 0.89, 0.91];
var _randIdx = 0;
var _randLog = [];
Math.random = function() {
    var v = _fixedRand[_randIdx % _fixedRand.length];
    _randLog.push(v);
    _randIdx++;
    return v;
};
window.__getRandLog__ = function() { return _randLog; };
"""

async def main():
    from playwright.async_api import async_playwright
    from hdata.auth.captcha import fetch_captcha, solve

    JFB = os.environ.get("JFB_TOKEN", "")
    if not JFB: print("❌ JFB_TOKEN"); return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # add_init_script 在页面 JS 之前注入
        await page.add_init_script(INJECT)

        verify_w = {}
        async def on_req(req):
            if "bcaptcha.botion.com/verify" in req.url:
                qs = parse_qs(urlparse(req.url).query)
                verify_w["w"] = qs.get("w", [""])[0]
                verify_w["url"] = req.url
                print(f"\n🎯 verify w={len(verify_w['w'])} chars")
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

        data = fetch_captcha(f"{dm}/user/login")
        result = solve(data["bg_url"], data["ques_urls"], JFB)
        jfbym_pts = [[int(x),int(y)] for x,y in [p.split(',') for p in result['coords'].split('|')]] if result else []
        print(f"jfbym: {jfbym_pts}")

        print("""
============================================================
  现在手动点击正确位置！getRandomValues 已固定。
  点击成功后，脚本自动用已知 key 解密 w。
============================================================
""")
        for _ in range(60):
            await asyncio.sleep(1)
            if verify_w.get("w"): break

        if verify_w.get("w"):
            w_hex = verify_w["w"]
            print(f"w={len(w_hex)} chars")
            (OUT / "fixed_w.txt").write_text(w_hex)

        if verify_w.get("w"):
            w_hex = verify_w["w"]
            print(f"w={len(w_hex)} chars")
            (OUT / "fixed_w.txt").write_text(w_hex)

            # 获取随机数调用记录
            rand_log = await page.evaluate("window.__getRandLog__()")
            print(f"Math.random 调用次数: {len(rand_log)}")
            (OUT / "rand_log.json").write_text(json.dumps(rand_log))

            # 生成可能的 key 列表 (rand_uid = 4 次连续调用)
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            aes_ct = bytes.fromhex(w_hex[:960])

            for start in range(len(rand_log) - 3):
                vals = rand_log[start:start+4]
                key_str = ''.join(hex(int(65536*(1+v)))[2:].zfill(4)[-4:] for v in vals)
                for offset in range(len(key_str) - 15):
                    k = key_str[offset:offset+16].encode()
                    if len(k) != 16: continue
                    try:
                        c = Cipher(algorithms.AES(k), modes.CBC(b'\x00'*16))
                        p = c.decryptor().update(aes_ct) + c.decryptor().finalize()
                        plain = p[:-p[-1]].decode('utf-8')
                        obj = json.loads(plain)
                        print(f'\n✅ start={start} offset={offset} key={key_str[offset:offset+16]}')
                        print(json.dumps(obj, indent=2, ensure_ascii=False)[:3000])
                        (OUT / 'real_e_object.json').write_text(json.dumps(obj, indent=2))
                        if obj.get('userresponse'):
                            print('\n真实 userresponse: %s' % obj['userresponse'])
                            print('jfbym userresponse:  %s' % jfbym_pts)
                        return
                    except: continue
            print('❌ 所有 key 尝试失败')
        else:
            print("❌ 未捕获 verify")

        await browser.close()

asyncio.run(main())
