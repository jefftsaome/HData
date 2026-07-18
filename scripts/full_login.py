"""
一键完整登录 — 清理缓存后首次登录，自动捕获所有认证数据。

流程:
  1. 打开可见浏览器 → 乐鱼登录页
  2. 自动填表（用户名+密码）
  3. CDP Network 拦截 botion.com/verify（捕获真实w参数）
  4. 用户手动完成GeeTest点选验证码
  5. 自动检测登录成功 → 提取 X-API-TOKEN/uuid/uuidToBase64
  6. 自动调用 venue/launch → 获取 game_token
  7. 保存到 .cache/{account}.json

用法:
    uv run python scripts/full_login.py --user xxx --pwd xxx
"""

import asyncio, base64, json, os, re, sys, time, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJ_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJ_ROOT / ".cache"
DATA_DIR = PROJ_ROOT / "data"
PROFILE_DIR = CACHE_DIR / "browser_profiles"

CAPTURED_DATA = {
    "w_params": [],       # 捕获的w参数
    "verify_responses": [],  # verify API响应
    "login_response": None,
    "token": None,
    "uuid": None,
    "uuidToBase64": None,
}

async def main(user, pwd, entry_url="https://leyu.me"):
    from playwright.async_api import async_playwright
    
    account = user
    profile = PROFILE_DIR / account
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print(f"  乐鱼一键登录 - {account}")
    print("=" * 60)
    
    async with async_playwright() as p:
        # ── 启动浏览器 ──
        print("\n[1/6] 启动浏览器...")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,  # 必须可见，否则GeeTest不加载
            args=['--disable-blink-features=AutomationControlled'],
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        
        # ── CDP 拦截 ──
        cdp = await page.context.new_cdp_session(page)
        
        def on_request_sent(params):
            url = params.get('request', {}).get('url', '')
            if 'botion.com/verify' in url and 'w=' in url:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                w = qs.get('w', [''])[0]
                lot = qs.get('lot_number', [''])[0]
                CAPTURED_DATA["w_params"].append({
                    "url": url,
                    "w": w,
                    "w_len": len(w),
                    "lot_number": lot,
                    "time": time.time(),
                })
                print(f"\n🎯 捕获真实w参数! len={len(w)} hex ({len(w)//2} bytes)")
        
        cdp.on('Network.requestWillBeSent', on_request_sent)
        await cdp.send('Network.enable')
        
        # 也监听响应
        def on_response_received(params):
            resp = params.get('response', {})
            url = resp.get('url', '')
            if 'botion.com/verify' in url:
                CAPTURED_DATA["verify_responses"].append({
                    "url": url,
                    "status": resp.get('status'),
                    "time": time.time(),
                })
        
        cdp.on('Network.responseReceived', on_response_received)
        
        # ── 域名解析 ──
        print("\n[2/6] 解析域名...")
        await page.goto(entry_url, wait_until="commit", timeout=15000)
        await asyncio.sleep(3)
        domain = re.match(r"https://[^/]+", page.url).group(0)
        print(f"  域名: {domain}")
        
        # ── 登录页 + 填表 ──
        print(f"\n[3/6] 打开登录页 + 自动填表...")
        await page.goto(f"{domain}/user/login", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        # 自动填表
        inputs = page.locator("input")
        count = await inputs.count()
        if count >= 2:
            # 用原生setter绕过React/Vue监听
            await page.evaluate(f"""
                (function(){{
                    var inputs = document.querySelectorAll('input');
                    var sv = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    for (var inp of inputs) {{
                        if (inp.type === 'password') {{
                            sv.call(inp, '{pwd}');
                            inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                        }} else if (inp.type !== 'hidden') {{
                            sv.call(inp, '{user}');
                            inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                        }}
                    }}
                }})()
            """)
            print(f"  ✅ 已填表: {user} / {'*'*len(pwd)}")
        else:
            print(f"  ⚠️ 仅找到 {count} 个输入框，请手动填表")
        
        # ── 等待用户完成验证码 ──
        print(f"\n[4/6] ⏳ 请在浏览器中操作:")
        print(f"  ① 点击「登录」按钮")
        print(f"  ② 手动完成GeeTest文字点选验证码")
        print(f"  ③ 等待自动跳转...")
        print(f"\n  (脚本会自动检测登录成功)")
        
        login_detected = False
        for i in range(120):
            await asyncio.sleep(1)
            
            # 检测登录成功：读取localStorage中的token
            try:
                has_token = await page.evaluate(
                    "!!localStorage.getItem('X-API-TOKEN')"
                )
            except:
                has_token = False
            
            if has_token and not login_detected:
                login_detected = True
                print(f"\n  ✅ 检测到登录成功! (第{i+1}秒)")
                
                # 提取所有localStorage数据
                ls = await page.evaluate("""
                    JSON.stringify({
                        token: localStorage.getItem('X-API-TOKEN') || '',
                        uuid: localStorage.getItem('_uuid') || '',
                        uuidB64: localStorage.getItem('uuidToBase64') || '',
                    })
                """)
                ls_data = json.loads(ls)
                CAPTURED_DATA["token"] = ls_data["token"]
                CAPTURED_DATA["uuid"] = ls_data["uuid"]
                CAPTURED_DATA["uuidToBase64"] = ls_data["uuidB64"]
                
                print(f"  X-API-TOKEN: {ls_data['token'][:30]}...")
                print(f"  UUID: {ls_data['uuid']}")
                print(f"  uuidToBase64: {len(ls_data['uuidB64'])} bytes")
                break
            
            if i % 15 == 14:
                print(f"  等待中... ({i+1}s)")
        
        await context.close()
        
        if not CAPTURED_DATA["token"]:
            print("\n❌ 未检测到登录成功（超时120s）")
            return False
        
        # ── 保存捕获的w参数 ──
        print(f"\n[5/6] 保存捕获数据...")
        if CAPTURED_DATA["w_params"]:
            for i, wp in enumerate(CAPTURED_DATA["w_params"]):
                out = {
                    "timestamp": int(time.time()),
                    "w": wp["w"],
                    "w_length": len(wp["w"]),
                    "w_bytes": len(wp["w"]) // 2,
                    "lot_number": wp["lot_number"],
                    "domain": domain,
                }
                out_path = DATA_DIR / f"real_w_{i+1}.json"
                out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
                print(f"  真实w参数 → {out_path} ({out['w_length']} hex)")
        
        # ── 刷新game_token ──
        print(f"\n[6/6] 刷新 game_token...")
        session = {
            "token": CAPTURED_DATA["token"],
            "uuid": CAPTURED_DATA["uuid"],
            "uuidToBase64": CAPTURED_DATA["uuidToBase64"],
            "domain": domain,
            "account": account,
        }
        
        # 解密签名表
        try:
            from hdata.auth.token_manager import TokenManager
            st = TokenManager._decrypt_sign_table(CAPTURED_DATA["uuidToBase64"])
            session["signatures"] = st
            print(f"  签名表: {list(st.keys())}")
        except Exception as e:
            print(f"  签名解密失败: {e}")
        
        # 保存session
        cache_path = CACHE_DIR / f"{account}.json"
        cache_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))
        print(f"  Session → {cache_path}")
        
        # 刷新game_token
        try:
            from hdata.auth.session import refresh_game_token, decode_jwt, save_session
            new_token = await refresh_game_token(account, session)
            session["game_token"] = new_token
            jwt_info = decode_jwt(new_token)
            if jwt_info:
                session["game_exp"] = jwt_info.get("exp", 0)
                sub = jwt_info.get("sub", {})
                if isinstance(sub, dict):
                    session["game_player_id"] = sub.get("playerId", 0)
            print(f"  ✅ game_token: {new_token[:50]}...")
            
            # 完整保存
            save_session(account, session)
        except Exception as e:
            print(f"  ⚠️ game_token刷新失败: {e}")
            print(f"  (可能需要等几秒后手动刷新)")
        
        # ── 最终输出 ──
        print("\n" + "=" * 60)
        print("  ✅ 登录完成!")
        print("=" * 60)
        safe = {k: v for k, v in session.items() if k != 'token'}
        safe['token'] = session.get('token', '')[:20] + '...'
        print(json.dumps(safe, indent=2, ensure_ascii=False))
        
        return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="乐鱼一键登录")
    p.add_argument("--user", default=os.getenv("LEYU_USER", ""), help="用户名")
    p.add_argument("--pwd", default=os.getenv("LEYU_PWD", ""), help="密码")
    args = p.parse_args()
    
    if not args.user or not args.pwd:
        print("请提供用户名和密码:")
        print("  uv run python scripts/full_login.py --user xxx --pwd xxx")
        print("  或设置环境变量: LEYU_USER, LEYU_PWD")
        sys.exit(1)
    
    success = asyncio.run(main(args.user, args.pwd))
    sys.exit(0 if success else 1)
