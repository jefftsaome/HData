#!/usr/bin/env python3
"""用人工点击成功的坐标调 verify，验证纯 HTTP 链路是否真的通了。
坐标从刚刚捕获的对比数据中提取。
"""
import json, os, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cr
from hdt.auth.captcha import fetch_captcha
from hdt.auth.geetest_signer import generate_w

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"

# 人类成功点击（缩放到 300x200）
# 人工 #1: 弹窗(126,67) → 300x200: (126*300/272, 67*200/235)
# 人工 #2: 弹窗(218,66) → 300x200: (218*300/272, 66*200/235)
# 人工 #3: 弹窗(50,78)  → 300x200: (50*300/272, 78*200/235)
# 但实际缩放应该用 botion_bg，这里用 botion_click 的数据

# 方法1: 直接用 jfbym 坐标（已被验证在X轴准确）
jfbym_coords = "139,77|238,76|54,86"
# 方法2: 人工坐标原始值（弹窗内）
human_raw = [(126, 67), (218, 66), (50, 78)]
popup_w, popup_h = 272, 235
human_coords = "|".join(f"{int(x*300/popup_w)},{int(y*200/popup_h)}" for x,y in human_raw)

# 方法3: 人工坐标但用 botion_bg 尺寸（估计 botion_bg 为 272x181）
bg_w, bg_h = popup_w, 181  # botion_bg: 同宽但更矮
human_bg_coords = "|".join(f"{int(x*300/bg_w)},{int(y*200/bg_h)}" for x,y in human_raw)

tests = [
    ("jfbym坐标", jfbym_coords),
    ("人工→botion_clickscaling", human_coords),
    ("人工→botion_bg scaling(272x181)", human_bg_coords),
]

for label, coords in tests:
    print(f"\n=== 测试: {label} ===")
    print(f"  坐标: {coords}")
    
    # 新 captcha（不能复用旧的 lot_number）
    data = fetch_captcha()
    if not data: print("  fetch_captcha失败"); continue
    print(f"  lot_number: {data['lot_number']}")
    
    w = generate_w(data, CAPTCHA_ID, coords)
    aes = (len(w)-256)//2
    print(f"  w: {len(w)} hex, AES={aes}B")
    
    cb = f"botion_{int(time.time()*1000)}"
    params = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
        "lot_number": data["lot_number"], "payload": data["payload"],
        "process_token": data["process_token"],
        "payload_protocol": data.get("payload_protocol","1"),
        "pt": data.get("pt","1"), "w": w}
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    
    resp = cr.get(url, impersonate="chrome110",
                  headers={"Referer": "https://www.leyu.me/"}, timeout=30)
    text = resp.text
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if m:
        d = json.loads(m.group(1))
        r = d.get("data", {})
        icon = "✅" if r.get("result") == "success" else "❌"
        print(f"  {icon} verify: status={d.get('status')} result={r.get('result')} "
              f"fail_count={r.get('fail_count')} score={r.get('score')}")
    else:
        print(f"  ❌ 解析失败: {text[:100]}")
