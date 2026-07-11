#!/usr/bin/env python3
"""验证 jfbym 坐标精度：下载验证码 → jfbym 识别 → 标记坐标 → 你肉眼判断。

用法:
    JFBYM_TOKEN=xxx uv run python scripts/verify_jfbym.py
"""
import asyncio, base64, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"

from PIL import Image, ImageDraw, ImageFont

async def main():
    if not JFBYM_TOKEN:
        print("需要 JFBYM_TOKEN")
        return
    
    from hdt.auth.captcha import fetch_captcha
    from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
    from curl_cffi import requests as cr
    
    # 1. 获取验证码
    print("[1] 获取验证码...")
    data = fetch_captcha()
    if not data: print("失败"); return
    print(f"  lot_number: {data['lot_number']}")
    print(f"  背景图: {data['bg_url']}")
    for i, u in enumerate(data['ques_urls']):
        print(f"  字图{i+1}: {u}")
    
    # 2. 下载图片
    print("\n[2] 下载图片...")
    bg = cr.get(data['bg_url'], impersonate='chrome110').content
    (DATA_DIR / "verify_bg.jpg").write_bytes(bg)
    ques = []
    for i, u in enumerate(data['ques_urls']):
        img = cr.get(u, impersonate='chrome110').content
        path = DATA_DIR / f"verify_ques_{i+1}.png"
        path.write_bytes(img)
        ques.append(img)
        print(f"  字图{i+1}: {len(img)} bytes")
    print(f"  背景图: {len(bg)} bytes")
    
    # 3. jfbym 识别
    print("\n[3] jfbym 识别...")
    solver = JfbymSolver(api_token=JFBYM_TOKEN)
    challenge = CaptchaChallenge(
        lot_number=data["lot_number"], payload=data["payload"],
        process_token=data["process_token"], bg_url=data["bg_url"],
        ques_urls=data["ques_urls"], captcha_id=CAPTCHA_ID,
    )
    sol = await solver.solve(challenge)
    pts = sol.pts
    print(f"  坐标: {pts}")
    print(f"  字符串: {sol.coords}")
    
    # 4. 在背景图上标记坐标
    print("\n[4] 生成标记图...")
    try:
        bg_img = Image.open(DATA_DIR / "verify_bg.jpg")
        draw = ImageDraw.Draw(bg_img)
        
        # 画圆圈标记每个点击点
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        labels = ["1", "2", "3"]
        for i, (x, y) in enumerate(pts):
            r = 8
            draw.ellipse([x-r, y-r, x+r, y+r], outline=colors[i], width=3)
            draw.text((x+r+3, y-r-3), labels[i], fill=colors[i])
        
        # 画十字线
        for i, (x, y) in enumerate(pts):
            draw.line([x-15, y, x+15, y], fill=colors[i], width=1)
            draw.line([x, y-15, x, y+15], fill=colors[i], width=1)
        
        marked_path = DATA_DIR / "verify_marked.jpg"
        bg_img.save(marked_path, quality=95)
        print(f"  已保存: {marked_path}")
        
        # 也保存原始图（未标记）
        print(f"  原始图: {DATA_DIR / 'verify_bg.jpg'}")
        
        # 把字图也复制出来便于对比
        for i in range(len(ques)):
            q_img = Image.open(DATA_DIR / f"verify_ques_{i+1}.png")
            q_img.save(DATA_DIR / f"verify_ques_{i+1}.png")
        
    except Exception as e:
        print(f"  标记失败: {e}")
        import traceback; traceback.print_exc()
    
    print(f"\n✅ 已生成验证文件:")
    print(f"  {DATA_DIR / 'verify_marked.jpg'}  — 标记了点击位置的背景图")
    print(f"  {DATA_DIR / 'verify_bg.jpg'}      — 原始背景图")
    print(f"  {DATA_DIR / 'verify_ques_1.png'}  — 参考字图1")
    print(f"  {DATA_DIR / 'verify_ques_2.png'}  — 参考字图2")
    print(f"  {DATA_DIR / 'verify_ques_3.png'}  — 参考字图3")
    print(f"\njfbym 坐标: {pts}")
    print("请打开 verify_marked.jpg，对照原始字图判断点击位置是否正确。")

if __name__ == "__main__":
    asyncio.run(main())
