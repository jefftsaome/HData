#!/usr/bin/env python3
"""调试纯 HTTP verify 链路 — 对比我们的 w 与真实 w，定位 result=fail 的根因。

用法:
    uv run python scripts/debug_http_verify.py [--solver <jfbym|none|manual>] [--save]

分析输出:
    1. 我们的 w 结构 (AES-CBC 段 + RSA 段长度)
    2. verify API 响应
    3. e_obj JSON 完整内容
    4. (可选) 与参考 w 对比差异
"""

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

# 确保能找到 hdt
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════


def w_decompose(w: str) -> dict:
    """分解 w 参数的各段。"""
    # w = hex(AES-CBC(e_obj, key)) + hex(RSA-1024(key))
    # RSA-1024 输出固定 128 bytes = 256 hex chars
    rsa_hex_len = 256
    
    if len(w) <= rsa_hex_len:
        return {"error": f"w too short ({len(w)} hex chars)"}
    
    aes_hex = w[:-rsa_hex_len]
    rsa_hex = w[-rsa_hex_len:]
    
    return {
        "total_len": len(w),
        "total_bytes": len(w) // 2,
        "aes_hex_len": len(aes_hex),
        "aes_bytes": len(aes_hex) // 2,
        "rsa_hex_len": len(rsa_hex),
        "rsa_bytes": len(rsa_hex) // 2,
        "aes_hex_start": aes_hex[:40],
        "aes_hex_end": aes_hex[-40:],
        "rsa_hex": rsa_hex[:40] + "..." + rsa_hex[-40:],
    }


def verify_request(load_data: dict, w: str) -> dict:
    """调用 GeeTest verify API，返回完整响应。"""
    from curl_cffi import requests as cr
    
    cb = f"botion_{int(time.time() * 1000)}"
    
    params = {
        "callback": cb,
        "captcha_id": CAPTCHA_ID,
        "client_type": "web",
        "lot_number": load_data["lot_number"],
        "payload": load_data["payload"],
        "process_token": load_data["process_token"],
        "payload_protocol": load_data.get("payload_protocol", "1"),
        "pt": load_data.get("pt", "1"),
        "w": w,
    }
    
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    
    headers = {
        "Referer": "https://www.leyu.me/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }
    
    t0 = time.time()
    resp = cr.get(url, impersonate="chrome110", headers=headers, timeout=30)
    latency = (time.time() - t0) * 1000
    
    text = resp.text
    
    # 解析 JSONP
    result = {
        "status_code": resp.status_code,
        "latency_ms": f"{latency:.0f}",
        "raw": text[:500],
        "has_success": '"result":"success"' in text,
        "has_fail": '"result":"fail"' in text,
    }
    
    # 尝试解析 JSONP
    m = re.search(r"\((.*)\)$", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            result["parsed"] = {
                "status": parsed.get("status"),
                "result": parsed.get("data", {}).get("result"),
                "fail_count": parsed.get("data", {}).get("fail_count"),
                "score": parsed.get("data", {}).get("score"),
                "seccode_keys": list(parsed.get("data", {}).get("seccode", {}).keys()) if parsed.get("data", {}).get("seccode") else [],
            }
            seccode = parsed.get("data", {}).get("seccode", {})
            if seccode:
                result["parsed"]["seccode"] = {
                    k: v[:40] + "..." if isinstance(v, str) and len(v) > 40 else v
                    for k, v in seccode.items()
                }
        except Exception as e:
            result["parse_error"] = str(e)
    
    return result


def w_to_e_obj(w: str) -> dict | None:
    """如果知道 random_key，解密 w 的 AES-CBC 段返回 e_obj。
    
    这里只做结构分析，不实际解密（我们没有 RSA 私钥）。
    """
    return {"note": "需要 RSA 私钥才能解密真实 w 的 AES-CBC 段"}


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════


def main():
    import argparse
    p = argparse.ArgumentParser(description="Debug HTTP verify")
    p.add_argument("--solver", choices=["jfbym", "none", "manual"], 
                   default="none", help="打码方案: jfbym需要 JFBYM_TOKEN, none=用固定坐标")
    p.add_argument("--save", action="store_true", help="保存本次完整数据")
    args = p.parse_args()
    
    # ── 1. 获取验证码 ──
    print("=" * 60)
    print("  纯 HTTP verify 调试")
    print("=" * 60)
    
    from hdt.auth.captcha import fetch_captcha
    
    print("\n[1/4] 获取验证码...")
    data = fetch_captcha()
    if not data:
        print("  ❌ fetch_captcha 失败")
        return 1
    print(f"  ✅ lot_number: {data['lot_number'][:20]}...")
    print(f"  ✅ bg: {data['bg_url'][:60]}...")
    print(f"  ✅ ques: {[q.split('/')[-1][:20] for q in data['ques_urls']]}")
    print(f"  ✅ pow: {json.dumps(data['pow_detail'])}")
    
    # ── 2. 获取坐标 ──
    print("\n[2/4] 获取点击坐标...")
    coords = ""
    pts = []
    
    if args.solver == "jfbym":
        if not JFBYM_TOKEN:
            print("  ❌ 需要设置 JFBYM_TOKEN 环境变量")
            return 1
        from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
        solver = JfbymSolver(api_token=JFBYM_TOKEN)
        challenge = CaptchaChallenge(
            lot_number=data["lot_number"],
            payload=data["payload"],
            process_token=data["process_token"],
            bg_url=data["bg_url"],
            ques_urls=data["ques_urls"],
            captcha_id=CAPTCHA_ID,
            pow_detail=data.get("pow_detail", {}),
            pt=data.get("pt", "1"),
            payload_protocol=data.get("payload_protocol", "1"),
        )
        try:
            solution = solver.solve(challenge)
            # JfbymSolver.solve 是 async，需要 await
            import asyncio
            solution = asyncio.run(solver.solve(challenge))
            coords = solution.coords
            pts = solution.pts
            print(f"  ✅ jfbym: {coords}")
        except Exception as e:
            print(f"  ❌ jfbym: {e}")
            return 1
    else:
        # 固定坐标（仅供调试，实际验证会fail）
        coords = "74,124|235,132|176,65"
        pts = [[74, 124], [235, 132], [176, 65]]
        print(f"  ⚠️  固定坐标: {pts} (仅供结构测试)")
    
    # ── 3. 生成 w 参数 ──
    print("\n[3/4] 生成 w 参数...")
    from hdt.auth.geetest_signer import generate_w
    
    w = generate_w(data, CAPTCHA_ID, coords)
    w_info = w_decompose(w)
    print(f"  ✅ 生成 w: {w_info['total_len']} hex chars")
    print(f"     AES-CBC段: {w_info['aes_hex_len']} hex ({w_info['aes_bytes']} bytes)")
    print(f"     RSA段:     {w_info['rsa_hex_len']} hex ({w_info['rsa_bytes']} bytes)")
    
    # ── 4. 输出 e_obj ──
    # 我们自己生成的 w，我们知道 random_key，可以直接解密
    print("\n  e_obj 结构分析 (我们的 w):")
    print(f"     userresponse: {pts}")
    print(f"     captcha_id: {CAPTCHA_ID}")
    print(f"     lot_number: {data['lot_number'][:20]}...")
    
    # ── 5. 调用 verify ──
    print(f"\n[4/4] 调用 verify API...")
    result = verify_request(data, w)
    print(f"  HTTP {result['status_code']} ({result['latency_ms']}ms)")
    print(f"  result=success: {result['has_success']}")
    print(f"  result=fail:    {result['has_fail']}")
    
    if "parsed" in result:
        p = result["parsed"]
        print(f"  status: {p.get('status')}")
        print(f"  result: {p.get('result')}")
        print(f"  fail_count: {p.get('fail_count')}")
        print(f"  score: {p.get('score')}")
        if "seccode" in p:
            print(f"  seccode keys: {p.get('seccode_keys')}")
    
    # 如果有 fail 详情
    if result.get("has_fail"):
        print("\n  ❌ verify result=fail — 开始深度分析...")
        print(f"  原始响应前 300 字符:")
        print(f"  {result['raw'][:300]}")
    
    # ── 6. 与参考数据对比 ──
    print("\n" + "=" * 60)
    print("  与参考 w 对比")
    print("=" * 60)
    
    # 加载参考数据
    ref_w = ""
    ref_params = {}
    
    ref_file = DATA_DIR / "real_w.txt"
    if ref_file.exists():
        ref_w = ref_file.read_text().strip()
        
    ref_params_file = DATA_DIR / "real_verify_params.json"
    if ref_params_file.exists():
        ref_params = json.loads(ref_params_file.read_text())
    
    if ref_w:
        ref_info = w_decompose(ref_w)
        print(f"\n  真实 w (from {ref_file.name}):")
        print(f"    length: {ref_info['total_len']} hex chars ({ref_info['total_bytes']} bytes)")
        print(f"    AES段:  {ref_info['aes_hex_len']} hex ({ref_info['aes_bytes']} bytes)")
        print(f"    RSA段:  {ref_info['rsa_hex_len']} hex ({ref_info['rsa_bytes']} bytes)")
        
        diff = w_info['total_len'] - ref_info['total_len']
        aes_diff = w_info['aes_hex_len'] - ref_info['aes_hex_len']
        print(f"\n  差异 (我们的 - 真实):")
        print(f"    总长度: {diff:+d} hex chars ({diff//2:+d} bytes)")
        print(f"    AES段:  {aes_diff:+d} hex chars ({aes_diff//2:+d} bytes)")
        
        if ref_info.get("rsa_hex_len") and w_info.get("rsa_hex_len"):
            if ref_info["rsa_hex_len"] == w_info["rsa_hex_len"]:
                print(f"    RSA段长度一致: {ref_info['rsa_hex_len']} hex chars ✅")
            else:
                print(f"    RSA段长度不同: 真实={ref_info['rsa_hex_len']}, 我们={w_info['rsa_hex_len']} ❌")
    
    # 保存本次数据
    if args.save:
        out = {
            "timestamp": int(time.time()),
            "load_data_keys": list(data.keys()),
            "lot_number": data["lot_number"],
            "our_w": {
                "length": len(w),
                "aes_hex_len": w_info["aes_hex_len"],
                "rsa_hex_len": w_info["rsa_hex_len"],
            },
            "coords": coords,
            "verify_result": {
                "has_success": result.get("has_success"),
                "has_fail": result.get("has_fail"),
                "parsed": result.get("parsed", {}),
                "raw_preview": result.get("raw", "")[:500],
            },
        }
        save_path = DATA_DIR / f"debug_verify_{int(time.time())}.json"
        save_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print(f"\n  数据已保存: {save_path}")
    
    return 0 if result.get("has_success") else 1


if __name__ == "__main__":
    sys.exit(main())
