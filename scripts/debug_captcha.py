#!/usr/bin/env python3
"""用 Playwright 观察 GeeTest 验证码的网络请求，提取图片 URL。

目标：拿到验证码图片 → jfbym 识别 → 纯 HTTP 复刻登录。

用法:
    uv run python scripts/debug_captcha.py
"""

import asyncio, json, re, sys, time
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
OUTDIR = _PROJ_ROOT / "data"


async def main():
    from playwright.async_api import async_playwright
    from curl_cffi import requests as cr

    OUTDIR.mkdir(parents=True, exist_ok=True)

    # 收集所有请求和响应
    all_requests = []
    all_responses = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # 监听所有网络请求
        async def on_request(request):
            all_requests.append({
                "method": request.method,
                "url": request.url,
                "headers": dict(request.headers),
                "post_data": request.post_data or "",
            })
        async def on_response(response):
            all_responses.append({
                "url": response.url,
                "status": response.status,
                "mime": response.headers.get("content-type", ""),
            })
        page.on("request", on_request)
        page.on("response", on_response)

        # 导航
        print("Step 1: 导航到登录页...")
        await page.goto("https://leyu.me", wait_until="commit", timeout=15000)
        await asyncio.sleep(2)
        dm = re.match(r"https://[^/]+", page.url).group(0)
        print(f"   域名: {dm}")
        await page.goto(f"{dm}/user/login", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # 填表 + Enter
        print("Step 2: 填表触发验证码...")
        inputs = page.locator("input")
        if await inputs.count() >= 2:
            await inputs.nth(0).click(); await page.keyboard.type("test", delay=50)
            await inputs.nth(1).click(); await page.keyboard.type("test", delay=50)
            await page.keyboard.press("Enter")

        # 等待弹窗出现
        print("Step 3: 等待验证码加载 (10s)...")
        for i in range(10):
            await asyncio.sleep(1)
            box = await page.evaluate(
                "document.querySelector('[class*=botion_box_btn]')?.getBoundingClientRect() || null")
            if box and box.get("height", 0) > 50:
                print(f"   弹窗出现: {box['width']:.0f}x{box['height']:.0f}")
                # 再等 2 秒让图片加载完成
                await asyncio.sleep(2)
                break

        await browser.close()

    # 分析结果
    botion_reqs = [r for r in all_requests if "botion" in r["url"] or "geetest" in r["url"]]
    botion_resps = [r for r in all_responses if "botion" in r["url"] or "geetest" in r["url"]]

    print(f"\n{'='*60}")
    print(f"📡 botion/geetest 请求 ({len(botion_reqs)} 个)")
    print(f"{'='*60}")
    for r in botion_reqs:
        print(f"\n  {r['method']} {r['url'][:200]}")
        if r["post_data"]:
            print(f"  POST body: {r['post_data'][:300]}")
        # 打印关键 headers
        for h in ["referer", "origin"]:
            if h in r.get("headers", {}):
                print(f"  {h}: {r['headers'][h][:150]}")

    print(f"\n{'='*60}")
    print(f"🖼️ 图片 URL:")
    print(f"{'='*60}")
    img_urls = [r["url"] for r in botion_reqs
                if any(r["url"].lower().endswith(ext) for ext in (".jpg", ".png", ".jpeg", ".gif", ".webp"))]
    for url in img_urls:
        print(f"  {url}")

    # 下载图片
    if img_urls:
        print(f"\n📥 下载图片 ({len(img_urls)} 个)...")
        for i, url in enumerate(img_urls):
            try:
                resp = cr.get(url, impersonate="chrome110", timeout=15)
                ext = url.split(".")[-1].split("?")[0]
                fname = OUTDIR / f"captcha_img_{i}.{ext}"
                fname.write_bytes(resp.content)
                print(f"  ✅ {fname} ({len(resp.content)} bytes)")
            except Exception as e:
                print(f"  ❌ {e}")

    # 检查是否有 verify 请求
    verify_reqs = [r for r in botion_reqs if "verify" in r["url"].lower()]
    if verify_reqs:
        print(f"\n🔐 Verify 请求:")
        for r in verify_reqs:
            print(f"  {r['url'][:300]}")

    # 保存
    OUTDIR.joinpath("captcha_network.json").write_text(json.dumps(
        {"requests": botion_reqs, "responses": botion_resps, "images": img_urls},
        indent=2, ensure_ascii=False, default=str))
    print(f"\n💾 data/captcha_network.json")


if __name__ == "__main__":
    asyncio.run(main())
