"""
捕获真实 GeeTest w 参数 — 使用 Playwright headless + CDP Network 拦截。

策略:
  1. 启动 headless Chromium（持久化context，复用登录态）
  2. 导航到乐鱼登录页
  3. 自动填表 + 触发GeeTest弹窗
  4. CDP Network 拦截 botion.com/verify 请求，提取真实 w 参数
  5. 同时提取 load API 返回的验证码数据（lot_number, payload等）
  6. 保存到 data/captured_real_w.json 供后续对比

用法:
    uv run python scripts/capture_real_w.py [--headed]
"""
import asyncio, base64, json, os, re, sys, time, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CAPTURED_W = None
CAPTURED_LOAD = None

async def main(headed=False):
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        page = await browser.new_page()
        
        # ── 1. 导航到登录页 ──
        print("[1] 获取域名...")
        await page.goto("https://leyu.me", wait_until="commit", timeout=15000)
        await asyncio.sleep(3)
        domain = re.match(r"https://[^/]+", page.url).group(0)
        print(f"    域名: {domain}")
        
        print("[2] 打开登录页...")
        await page.goto(f"{domain}/user/login", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        # ── 2. CDP 拦截 botion.com/verify ──
        cdp = await page.context.new_cdp_session(page)
        
        captured_verify_urls = []
        
        def on_request_sent(params):
            url = params.get('request', {}).get('url', '')
            if 'botion.com/verify' in url and 'w=' in url:
                captured_verify_urls.append(url)
        
        cdp.on('Network.requestWillBeSent', on_request_sent)
        await cdp.send('Network.enable')
        print("[3] CDP Network 监听已启用，等待 verify 请求...")
        
        # ── 3. 尝试自动填表触发验证码 ──
        print("[4] 自动填表...")
        inputs = page.locator("input")
        count = await inputs.count()
        if count >= 2:
            await inputs.nth(0).click()
            await page.keyboard.type(os.getenv("LEYU_USER", "lidongsen1"), delay=50)
            await inputs.nth(1).click()
            await page.keyboard.type(os.getenv("LEYU_PWD", ""), delay=50)
            print("    填表完成，点击登录...")
            await page.keyboard.press("Enter")
        else:
            print(f"    仅找到 {count} 个输入框，可能需要手动操作")
        
        # ── 4. 等待验证码弹窗出现 ──
        print("[5] 等待GeeTest弹窗...")
        captcha_appeared = False
        for i in range(20):
            await asyncio.sleep(1)
            # 检测验证码弹窗
            has_captcha = await page.evaluate("""
                (function(){
                    var el = document.querySelector('[class*=botion_box_btn], [class*=geetest_box]');
                    return el ? el.getBoundingClientRect().height > 50 : false;
                })()
            """)
            if has_captcha:
                captcha_appeared = True
                print(f"    验证码弹窗已出现!")
                break
            if i % 5 == 4:
                print(f"    等待中...({i+1}s)")
        
        if not captcha_appeared:
            print("    ⚠️ 验证码弹窗未出现（可能headless被检测）")
            if not headed:
                print("    建议加 --headed 参数重试")
        
        # ── 5. 等待 verify 请求被捕获 ──
        print("[6] 等待 botion.com/verify 请求...")
        # 如果弹窗出现了但还没点击，SDK不会发verify
        # 需要用户手动点击或等待超时
        for i in range(60):
            await asyncio.sleep(1)
            if captured_verify_urls:
                break
            if i % 10 == 9:
                print(f"    等待中...({i+1}s), 已捕获 {len(captured_verify_urls)} 个URL")
        
        # ── 6. 解析捕获的 w 参数 ──
        if captured_verify_urls:
            print(f"\n✅ 捕获到 {len(captured_verify_urls)} 个 verify 请求!")
            for i, url in enumerate(captured_verify_urls):
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                w = qs.get('w', [''])[0]
                lot = qs.get('lot_number', [''])[0]
                payload_val = qs.get('payload', [''])[0]
                pt_val = qs.get('pt', [''])[0]
                
                print(f"\n  请求 {i+1}:")
                print(f"    lot_number: {lot[:30]}...")
                print(f"    pt: {pt_val}")
                print(f"    payload[:50]: {payload_val[:50]}...")
                print(f"    w length: {len(w)} hex chars ({len(w)//2} bytes)")
                print(f"    w[:100]: {w[:100]}...")
                
                # 保存
                output = {
                    "timestamp": int(time.time()),
                    "w": w,
                    "w_length": len(w),
                    "w_bytes": len(w) // 2,
                    "lot_number": lot,
                    "payload": payload_val,
                    "pt": pt_val,
                    "captcha_id": CAPTCHA_ID,
                    "domain": domain,
                }
                out_path = DATA_DIR / f"captured_real_w_{i+1}.json"
                out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
                print(f"\n    已保存到: {out_path}")
        else:
            print("\n❌ 未捕获到 verify 请求")
            print("   可能原因:")
            print("   1. 验证码弹窗未出现（headless被检测）")
            print("   2. GeeTest SDK未触发验证")
            print("   3. 网络问题")
            print("   建议: 使用 --headed 参数在可见浏览器中手动完成验证码")
        
        await browser.close()
        return len(captured_verify_urls) > 0

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true", help="使用可见浏览器")
    args = p.parse_args()
    
    success = asyncio.run(main(headed=args.headed))
    sys.exit(0 if success else 1)
