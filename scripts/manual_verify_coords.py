#!/usr/bin/env python3
"""保存弹窗图 + jfbym 坐标 + 标记截图，手动验证坐标准确性。

用法: export JFB_TOKEN=... && uv run python scripts/manual_verify_coords.py
"""

import asyncio, base64, json, re, sys, os, io, time
from pathlib import Path
from urllib.parse import urlparse

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))
OUT = _PROJ / "data" / "coord_check"
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    from playwright.async_api import async_playwright
    from hdt.auth.captcha import fetch_captcha, solve
    from curl_cffi import requests as cr
    from PIL import Image, ImageDraw

    JFB = os.environ.get("JFB_TOKEN", "")
    if not JFB: print("❌ JFB_TOKEN"); return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # 拦截 GeeTest load 响应，获取弹窗实际使用的图片 URL
        geetest_data = {}
        async def on_resp(resp):
            if "bcaptcha.botion.com/load" in resp.url and resp.status == 200:
                try:
                    text = await resp.text()
                    m = re.search(r"\((.*)\)$", text, re.DOTALL)
                    if m:
                        geetest_data["load"] = json.loads(m.group(1))["data"]
                except: pass
        page.on("response", on_resp)

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

        # 等弹窗 — 等动画结束，尺寸 > 0
        box = None
        for i in range(15):
            await asyncio.sleep(1)
            info = await page.evaluate("""
                (() => {
                    let r = {};
                    let layer = document.querySelector('[class*=botion_box_layer]');
                    let btn = document.querySelector('[class*=botion_box_btn]');
                    if (layer) { let b = layer.getBoundingClientRect(); if (b.width>50 && b.height>50) r.layer = {x:b.x,y:b.y,w:b.width,h:b.height}; }
                    if (btn) { let b = btn.getBoundingClientRect(); if (b.width>50 && b.height>50) r.btn = {x:b.x,y:b.y,w:b.width,h:b.height}; }
                    return r;
                })()
            """)
            if info.get("btn") and info["btn"]["w"] > 0:
                box = info["btn"]
                print(f"弹窗 btn ({i+1}s): {box['w']:.0f}x{box['h']:.0f}")
                break
            elif info.get("layer") and info["layer"]["w"] > 0:
                box = info["layer"]
                print(f"弹窗 layer ({i+1}s): {box['w']:.0f}x{box['h']:.0f}")
                break
        if not box: print("弹窗未出现"); return

        # 保存弹窗区域截图
        clip = {"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"]}
        popup_ss = await page.screenshot(clip=clip, type="png")
        (OUT / "popup_screenshot.png").write_bytes(popup_ss)
        print(f"💾 popup_screenshot.png ({len(popup_ss)} bytes)")

        # 全屏截图（后面标记坐标点）
        full_ss = await page.screenshot(type="png")

        # 使用弹窗实际加载的图片（不是 fetch_captcha 的新挑战）
        if not geetest_data.get("load"):
            print("⚠️ 未拦截到 GeeTest load，降级用 fetch_captcha")
            data = fetch_captcha(f"{dm}/user/login")
            bg_url = data["bg_url"]
            ques_urls = data["ques_urls"]
        else:
            gd = geetest_data["load"]
            bg_url = f"https://static.botion.com/{gd['imgs']}"
            ques_urls = [f"https://static.botion.com/{p}" for p in gd["ques"]]
            print(f"✅ 使用弹窗实际图片")

        bg_bytes = cr.get(bg_url, impersonate="chrome110", timeout=15).content
        (OUT / "bg_original.jpg").write_bytes(bg_bytes)
        print(f"💾 bg_original.jpg ({len(bg_bytes)} bytes)")

        for qi, url in enumerate(ques_urls):
            qb = cr.get(url, impersonate="chrome110", timeout=10).content
            (OUT / f"ques_{qi+1}.png").write_bytes(qb)
            print(f"💾 ques_{qi+1}.png ({len(qb)} bytes)")

        # 找弹窗内 img 元素的真实渲染位置
        img_info = await page.evaluate("""
            (() => {
                let btn = document.querySelector('[class*=botion_box_btn]');
                let img = btn ? btn.querySelector('img') : null;
                let canvas = document.querySelector('[class*=botion_box_layer] canvas');
                let r = {};
                if (img) {
                    let b = img.getBoundingClientRect();
                    r.img = {x:b.x, y:b.y, w:b.width, h:b.height, naturalW:img.naturalWidth, naturalH:img.naturalHeight};
                }
                if (canvas) {
                    let b = canvas.getBoundingClientRect();
                    r.canvas = {x:b.x, y:b.y, w:b.width, h:b.height};
                }
                return r;
            })()
        """)
        print(f"img/canvas info: {img_info}")

        # 确定实际图片显示区域
        if img_info.get("img"):
            display = img_info["img"]
        elif img_info.get("canvas"):
            display = img_info["canvas"]
        else:
            display = box
        print(f"实际图片显示: {display['w']:.0f}x{display['h']:.0f} at ({display['x']:.0f},{display['y']:.0f})")

        # jfbym 坐标（用弹窗实际图片）
        result = solve(bg_url, ques_urls, JFB)
        if not result: print("solve failed"); return
        coords = result["coords"]
        pts = [[int(x), int(y)] for x, y in [p.split(",") for p in coords.split("|")]]
        print(f"\njfbym 原始坐标: {pts}")

        # 背景图真实尺寸 vs 实际显示尺寸
        from PIL import Image as PILImage
        bg_img = PILImage.open(io.BytesIO(bg_bytes))
        bg_w, bg_h = bg_img.size
        disp_w, disp_h = display["w"], display["h"]
        disp_x, disp_y = display["x"], display["y"]
        # 计算等比缩放+居中偏移
        scale = box["w"] / bg_w  # 宽度填满，等比缩放
        disp_w = box["w"]
        disp_h = bg_h * scale
        disp_x = box["x"]
        disp_y = box["y"] + (box["h"] - disp_h) / 2  # 垂直居中
        print(f"背景原图: {bg_w}x{bg_h}")
        print(f"推定显示: {disp_w:.0f}x{disp_h:.0f} at ({disp_x:.0f},{disp_y:.0f})")
        scale_x = scale_y = scale
        print(f"等比缩放: {scale:.3f}, 垂直居中偏移: {(box['h']-disp_h)/2:.0f}px")

        # 用 Pillow 在原图上标记坐标
        bg_marked = bg_img.copy()
        draw = ImageDraw.Draw(bg_marked)
        for i, (x, y) in enumerate(pts):
            r = 5
            draw.ellipse([x-r, y-r, x+r, y+r], fill="red", outline="white", width=2)
            draw.text((x+8, y-8), str(i+1), fill="red")
        bg_marked.save(OUT / "bg_marked.jpg")
        print(f"💾 bg_marked.jpg")

        # 在全屏截图上标记（基于实际显示区域）
        full_img = PILImage.open(io.BytesIO(full_ss))
        draw_f = ImageDraw.Draw(full_img)
        for i, (x, y) in enumerate(pts):
            sx = disp_x + int(x * scale_x)
            sy = disp_y + int(y * scale_y)
            r = 8
            draw_f.ellipse([sx-r, sy-r, sx+r, sy+r], fill="red", outline="white", width=3)
            draw_f.text((sx+10, sy-10), str(i+1), fill="red")
            print(f"  坐标{i+1}: ({x},{y}) → 显示({int(x*scale_x)},{int(y*scale_y)}) → 屏幕({sx:.0f},{sy:.0f})")
        full_img.save(OUT / "fullscreen_marked.png")
        print(f"💾 fullscreen_marked.png")

        print(f"\n📁 所有文件保存在 {OUT}/")
        print("请打开 bg_marked.jpg 和 fullscreen_marked.png 对比坐标是否准确")
        await browser.close()

asyncio.run(main())
