#!/usr/bin/env python3
"""从 botion.com GeeTest SDK 提取 biht、ep、ZAhG、gee_guard 等静态参数。

用法: uv run python scripts/extract_static_params.py
"""

import asyncio, json, re, sys, os
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))

INJECT = """
// Hook JSON.stringify 来捕获 GeeTest SDK 构造的 e-object
(function(){
    var _orig = JSON.stringify;
    JSON.stringify = function(obj) {
        if (obj && obj.userresponse && obj.lot_number && obj.biht) {
            window.__CAUGHT__ = JSON.parse(_orig(obj));
            console.log('[CAUGHT] biht=' + obj.biht + ' ep=' + obj.ep + ' ZAhG=' + (obj.ZAhG||'N/A'));
        }
        return _orig.apply(this, arguments);
    };
})();
"""

async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.add_init_script(INJECT)

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

        for _ in range(15):
            await asyncio.sleep(1)
            b = await page.evaluate("""()=>{let e=document.querySelector('[class*=botion_box_layer]');if(e){let r=e.getBoundingClientRect();if(r.width>50&&r.height>50)return{x:r.x,y:r.y};}return null}""")
            if b: print(f"弹窗出现在 ({b['x']:.0f},{b['y']:.0f})"); break

        print("""
============================================================
  手动点击正确位置！Hook 捕获真实 e-object。
============================================================
""")
        for _ in range(60):
            await asyncio.sleep(1)
            caught = await page.evaluate("window.__CAUGHT__ ? JSON.stringify(window.__CAUGHT__) : null")
            if caught:
                obj = json.loads(caught)
                print(f"\n✅ 捕获真实 e-object 静态参数:")
                for k in ['biht', 'ep', 'ZAhG', 'device_id', 'geetest', 'lang', 'gee_guard', 'em']:
                    v = obj.get(k, 'N/A')
                    print(f"  {k}: {json.dumps(v)}")
                print(f"\n完整 keys: {list(obj.keys())}")
                out = _PROJ / "data" / "real_static_params.json"
                out.parent.mkdir(exist_ok=True)
                out.write_text(json.dumps(obj, indent=2))
                print(f"💾 {out}")
                break
        else:
            print("❌ 未捕获")

        await browser.close()

asyncio.run(main())
