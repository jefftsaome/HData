#!/usr/bin/env python3
"""通过 CDP 连到现有 Chrome，捕获真实 SDK 的 w 参数。

用户需要先启动 Chrome（可以是普通 Chrome，不需要 browser-act）：

    /Applications/Google\\ Chrome.app/Contents/MacOS/Google Chrome \\
        --remote-debugging-port=9222

然后运行本脚本：

    uv run python scripts/capture_real_w.py

脚本会自动:
  1. 连到 Chrome CDP (port 9222)
  2. 打开 leyu.me/user/login
  3. 自动填账号密码
  4. 等待验证码弹窗
  5. 通过 CDP 点击验证码（使用 jfbym 坐标）
  6. 捕获 SDK 的 verify 请求（含 w 参数）
  7. 保存到 data/real_w_captured.json
"""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ctrl+C 优雅退出
_shutdown = False

def _setup_signal():
    def handler(sig, frame):
        global _shutdown
        if _shutdown:
            print("\n⏳ 强制退出...")
            sys.exit(1)
        _shutdown = True
        print("\n\n⏳ 正在退出（按 Ctrl+C 再按一次强制退出）...")
    signal.signal(signal.SIGINT, handler)

_setup_signal()

import aiohttp
import websockets

JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "we4578")
CDP_PORT = int(os.getenv("LEYU_CDP_PORT", "9222"))
CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


async def main():
    print("=" * 60)
    print("  真实 w 参数捕获脚本")
    print("=" * 60)
    print(f"\n请确保 Chrome 已启动: --remote-debugging-port={CDP_PORT}")
    print(f"账号: {LEYU_USER}")
    print()
    
    # ── 1. 连接 CDP ──
    cdp_base = f"http://127.0.0.1:{CDP_PORT}"
    print(f"[1/6] 连接 Chrome CDP ({cdp_base})...")
    
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{cdp_base}/json/list", timeout=5)
        targets = await r.json()
        r2 = await s.get(f"{cdp_base}/json/version", timeout=5)
        ws_url = (await r2.json()).get("webSocketDebuggerUrl", "")
    
    if not ws_url:
        print("  ❌ 无法连接 CDP。Chrome 是否以 --remote-debugging-port 启动？")
        return 1
    
    print(f"  ✅ 已连接 CDP")
    
    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        cdp_mid = 0
        
        async def cdp(method, params=None, sid=None):
            nonlocal cdp_mid; cdp_mid += 1
            msg = {"id": cdp_mid, "method": method, "params": params or {}}
            if sid: msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return cdp_mid
        
        async def wait_result(mid, timeout=5):
            dl = time.time() + timeout
            while time.time() < dl:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                if msg.get("id") == mid:
                    return msg
            return None
        
        # 找 page target
        page_t = next((t for t in targets if t.get("type") == "page"), None)
        if not page_t:
            # 创建新页面
            mid = await cdp("Target.createTarget", {"url": "about:blank"})
            resp = await wait_result(mid)
            if resp and resp.get("result", {}).get("targetId"):
                print("  ✅ 创建新标签页")
                page_t = {"id": resp["result"]["targetId"]}
            else:
                print("  ❌ 找不到 page target")
                return 1
        
        print(f"  ✅ 使用页面: {page_t['url'] if 'url' in page_t else '新建'}")
        
        # ── 2. Attach ──
        print(f"\n[2/6] 附加到页面...")
        await cdp("Target.attachToTarget",
                  {"targetId": page_t["id"], "flatten": True})
        cdp_sid = ""
        dl = time.time() + 5
        while time.time() < dl:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("method") == "Target.attachedToTarget":
                cdp_sid = msg["params"]["sessionId"]
                break
        if not cdp_sid:
            print("  ❌ attachToTarget 失败")
            return 1
        print(f"  ✅ sessionId: {cdp_sid[:20]}...")
        
        async def cdp_page(method, params=None):
            nonlocal cdp_mid; cdp_mid += 1
            msg = {"id": cdp_mid, "method": method, "params": params or {}}
            msg["sessionId"] = cdp_sid
            await ws.send(json.dumps(msg))
            return cdp_mid
        
        async def cdp_eval(expr):
            mid = await cdp_page("Runtime.evaluate",
                                 {"expression": expr, "returnByValue": True})
            dl = time.time() + 5
            while time.time() < dl:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                if msg.get("id") == mid:
                    return msg.get("result", {}).get("result", {}).get("value")
            return None
        
        # ── 3. 启用 Network 监控 ──
        print(f"\n[3/6] 启用 Network 监控...")
        await cdp_page("Network.enable")
        
        # 存储捕获的请求
        captured = {"verify_url": "", "load_data": {}, "w": ""}
        
        async def handle_ws():
            """后台监听 CDP 消息。"""
            while not _shutdown:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    msg = json.loads(raw)
                    method = msg.get("method", "")
                    
                    # 捕获 Network 请求
                    if method == "Network.requestWillBeSent":
                        req = msg.get("params", {}).get("request", {})
                        url = req.get("url", "")
                        method_type = req.get("method", "")
                        
                        # 捕获 botion verify 请求
                        if "botion.com/verify" in url and "w=" in url:
                            import urllib.parse
                            parsed = urllib.parse.urlparse(url)
                            qs = urllib.parse.parse_qs(parsed.query)
                            w_val = qs.get("w", [""])[0]
                            if w_val and not captured["w"]:
                                captured["w"] = w_val
                                captured["verify_url"] = url
                                print(f"\n  ✅ 捕获到 verify 请求!")
                                print(f"     w 长度: {len(w_val)} hex chars")
                        
                        # 捕获 botion load 响应
                        elif "botion.com/load" in url:
                            pass  # 在 response 中捕获
                    
                    elif method == "Network.responseReceived":
                        resp = msg.get("params", {}).get("response", {})
                        url = resp.get("url", "")
                        
                        if "botion.com/load" in url:
                            # 读取响应体
                            rid = resp.get("requestId", "")
                            if rid:
                                try:
                                    mid_b = await cdp_page("Network.getResponseBody",
                                                            {"requestId": rid})
                                    # 这会异步返回，但我们在另一个协程中
                                except:
                                    pass
                    
                    elif method == "Network.loadingFinished":
                        rid = msg.get("params", {}).get("requestId", "")
                        # 可以尝试读取响应体
                
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    if "closed" in str(e):
                        break
        
        # ── 4. 导航到 leyu.me，获取真实域名 ──
        print(f"\n[4/6] 获取真实域名...")
        await cdp_page("Page.navigate", {"url": "https://leyu.me"})
        await asyncio.sleep(4)
        
        url_val = await cdp_eval("window.location.href")
        print(f"  重定向到: {url_val}")
        
        # 提取真实域名
        import re as _re
        domain_match = _re.match(r"(https://[^/]+)", url_val or "")
        if not domain_match:
            print("  ❌ 无法提取域名")
            return 1
        real_domain = domain_match.group(1)
        print(f"  真实域名: {real_domain}")
        
        # 导航到登录页
        login_url = f"{real_domain}/user/login"
        print(f"\n  导航到登录页: {login_url}")
        await cdp_page("Page.navigate", {"url": login_url})
        await asyncio.sleep(3)
        
        url_val = await cdp_eval("window.location.href")
        print(f"  当前 URL: {url_val}")
        
        if "user/login" not in (url_val or ""):
            print("  页面可能重定向了，等待中...")
            await asyncio.sleep(3)
            url_val = await cdp_eval("window.location.href")
            print(f"  当前 URL: {url_val}")
        
        # ── 5. 自动填表 ──
        print(f"\n[5/6] 自动填写登录表单...")
        escaped_pwd = LEYU_PWD.replace("\\", "\\\\").replace("'", "\\'")
        escaped_user = LEYU_USER.replace("\\", "\\\\").replace("'", "\\'")
        
        fill_script = f"""
        (function() {{
            var inputs = document.querySelectorAll('input');
            var userInp = null, pwdInp = null;
            for (var inp of inputs) {{
                if (inp.type === 'password') pwdInp = inp;
                else if (!userInp) userInp = inp;
            }}
            if (!userInp || !pwdInp) return 'no_inputs';
            
            userInp.value = ''; userInp.focus();
            userInp.value = '{escaped_user}';
            userInp.dispatchEvent(new Event('input', {{bubbles: true}}));
            userInp.dispatchEvent(new Event('change', {{bubbles: true}}));
            
            pwdInp.value = ''; pwdInp.focus();
            pwdInp.value = '{escaped_pwd}';
            pwdInp.dispatchEvent(new Event('input', {{bubbles: true}}));
            pwdInp.dispatchEvent(new Event('change', {{bubbles: true}}));
            
            // 找登录按钮
            var btn = document.querySelector('button[type=submit]');
            if (!btn) btn = document.querySelector('.sV0BIdNgkCghFjH6HXzUFg__');
            if (!btn) {{
                var allBtn = document.querySelectorAll('button, span, a');
                for (var b of allBtn) {{
                    if (b.textContent.trim() === '登录') {{ btn = b; break; }}
                }}
            }}
            
            if (btn) {{
                btn.click();
                return 'clicked_login';
            }}
            return 'no_button';
        }})()
        """
        result = await cdp_eval(fill_script)
        print(f"  填表结果: {result}")
        
        # ── 6. 等待用户手动点验证码 ──
        print(f"\n[6/6] 请在浏览器中手动完成验证码点击...")
        print(f"  （验证码弹窗应该已经出现，点击正确的文字位置）")
        print(f"  （完成后脚本会自动捕获 w 参数）")
        print()
        
        # 启动后台监听
        listener_task = asyncio.create_task(handle_ws())
        
        # 同时轮询验证码弹窗，尝试用 jfbym 自动解决
        from hdt.auth.captcha import fetch_captcha
        from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
        
        has_captcha = False
        for i in range(60):
            if _shutdown:
                print("\n  ⏹  用户中断")
                break
            await asyncio.sleep(1)
            
            # 检查是否有 w 被捕获
            if captured["w"]:
                break
            
            # 检查验证码弹窗是否存在
            popup = await cdp_eval(
                """(function(){
                    var e = document.querySelector('[class*="botion_click"]');
                    if (!e) return null;
                    var r = e.getBoundingClientRect();
                    return r.width > 50 ? 'visible' : 'loading';
                })()"""
            )
            
            if popup == "visible" and not has_captcha:
                has_captcha = True
                
                if JFBYM_TOKEN:
                    print(f"\n  ⏳ 验证码弹窗出现，尝试 jfbym 自动解决...")
                    
                    # 提取验证码图片
                    img_val = await cdp_eval(
                        """(function(){
                            var r = {bg: null, ques: []};
                            document.querySelectorAll('[class*="botion_bg"]').forEach(function(e){
                                var bg = getComputedStyle(e).backgroundImage;
                                var m = bg.match(/url\\(["']?([^"')\\s]+)["']?\\)/);
                                if (m && m[1] && m[1].indexOf('captcha_v4') >= 0) r.bg = m[1];
                            });
                            document.querySelectorAll('[class*="botion"] img').forEach(function(i){
                                if (i.naturalWidth >= 60 && i.src.indexOf('sprite') < 0) 
                                    r.ques.push(i.src);
                            });
                            return JSON.stringify(r);
                        })()"""
                    )
                    
                    if img_val:
                        import json as _json
                        img_data = _json.loads(img_val)
                        
                        if img_data.get("bg") and len(img_data.get("ques", [])) >= 3:
                            print(f"  ✅ 提取到验证码图片")
                            
                            # 先用 HTTP 获取 captcha 数据
                            from curl_cffi import requests as cr
                            
                            solver = JfbymSolver(api_token=JFBYM_TOKEN)
                            challenge = CaptchaChallenge(
                                lot_number="", payload="", process_token="",
                                bg_url=img_data["bg"],
                                ques_urls=img_data["ques"][:3],
                                captcha_id=CAPTCHA_ID,
                            )
                            try:
                                solution = await solver.solve(challenge)
                                pts = solution.pts
                                print(f"  ✅ jfbym 坐标: {solution.coords}")
                                
                                # 缩放到实际显示尺寸 + CDP 点击
                                bg_box = await cdp_eval(
                                    """(function(){
                                        var e = document.querySelector('[class*="botion_bg"]');
                                        if (!e) return null;
                                        var r = e.getBoundingClientRect();
                                        return r.width > 50 ? JSON.stringify({x:r.x, y:r.y, w:r.width, h:r.height}) : null;
                                    })()"""
                                )
                                
                                if bg_box:
                                    bg = _json.loads(bg_box)
                                    scale_x = bg["w"] / 300.0
                                    scale_y = bg["h"] / 200.0
                                    
                                    for idx, (x, y) in enumerate(pts):
                                        sx = bg["x"] + x * scale_x
                                        sy = bg["y"] + y * scale_y
                                        print(f"    点击 {idx+1}: ({sx:.0f}, {sy:.0f})")
                                        await cdp_page("Input.dispatchMouseEvent",
                                                        {"type": "mousePressed", "x": sx, "y": sy,
                                                         "button": "left", "clickCount": 1})
                                        await asyncio.sleep(0.1)
                                        await cdp_page("Input.dispatchMouseEvent",
                                                        {"type": "mouseReleased", "x": sx, "y": sy,
                                                         "button": "left", "clickCount": 1})
                                        await asyncio.sleep(0.5)
                                    
                                    print(f"  ✅ 已完成 CDP 点击，等待 verify 响应...")
                            except Exception as e:
                                print(f"  ⚠️ jfbym 失败: {e}")
                else:
                    print(f"\n  ⏳ 验证码弹窗出现，请手动点击!")
                    print(f"  （未设置 JFBYM_TOKEN，需要手动完成验证码）")
            
            if i % 5 == 4 and not captured["w"] and not _shutdown:
                print(f"  ...等待中 ({i+1}s)")
        
        listener_task.cancel()
        
        if captured["w"]:
            print(f"\n✅✅✅ 成功捕获 w 参数!")
            print(f"   长度: {len(captured['w'])} hex chars")
            print(f"   前40: {captured['w'][:40]}...")
            print(f"   后40: ...{captured['w'][-40:]}")
            
            # 保存到文件
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            save_path = DATA_DIR / "real_w_captured.json"
            
            # 也获取 load 数据
            load_data = None
            try:
                from hdt.auth.captcha import fetch_captcha
                load_data = fetch_captcha()
            except:
                pass
            
            output = {
                "timestamp": int(time.time()),
                "w": captured["w"],
                "w_len": len(captured["w"]),
                "verify_url": captured["verify_url"],
                "load_data": load_data,
            }
            save_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
            print(f"\n   已保存: {save_path}")
            
            # 也保存纯 w 文本
            w_path = DATA_DIR / "real_w_captured.txt"
            w_path.write_text(captured["w"])
            print(f"   已保存: {w_path}")
        else:
            print(f"\n❌ 未捕获到 w 参数")
            print("   可能是 CDP Input 被禁用，请手动点击验证码")
        
        listener_task.cancel()
    
    return 0 if captured["w"] else 1


if __name__ == "__main__":
    import sys
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n⏹  已退出")
        sys.exit(1)
