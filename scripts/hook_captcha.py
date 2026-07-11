#!/usr/bin/env python3
"""用 Chrome 人工点击验证码，hook XMLHttpRequest 捕获完整链路数据。

用法:
    1. 启动 Chrome: /Applications/Google\\ Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222
    2. 运行: JFBYM_TOKEN=xxx uv run python scripts/hook_botion.py 9222
    
流程:
    1. 连接 Chrome → 打开 leyu 登录页 → 自动填表
    2. 你手动点击「登录」按钮 + 手动点击验证码
    3. Hook 自动捕获: verify URL(w参数) + validate + login 数据
    4. 保存到 data/hook_captured.json
"""

import asyncio, json, os, sys, time, re, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets

LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "we4578")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222

    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json()).get('webSocketDebuggerUrl', '')

    if not ws_url:
        print("❌ Chrome CDP 不可用，确保 --remote-debugging-port=9222 启动"); return

    print(f"✅ 已连接 Chrome")
    
    # 找登录页面或创建新的
    page_t = next((t for t in targets if t['type'] == 'page' and 'user/login' in t.get('url', '')), None)
    if not page_t:
        page_t = next((t for t in targets if t['type'] == 'page'), None)
    if not page_t:
        print("❌ 无可用页面"); return

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        mid = 0
        sid = ""

        async def cdp(m, p=None):
            nonlocal mid; mid += 1
            msg = {'id': mid, 'method': m, 'params': p or {}}
            if sid: msg['sessionId'] = sid
            await ws.send(json.dumps(msg))
            return mid

        async def cdp_wait(tid, to=10):
            dl = time.time() + to
            while time.time() < dl:
                raw = await asyncio.wait_for(ws.recv(), timeout=to)
                msg = json.loads(raw)
                if msg.get('id') == tid:
                    return msg.get('result', {})
                if msg.get('method') == 'Target.attachedToTarget':
                    nonlocal sid
                    sid = msg['params']['sessionId']
            return {}

        # Attach
        await cdp('Target.attachToTarget', {'targetId': page_t['id'], 'flatten': True})
        resp = await cdp_wait(0)  # 等 attach 完成
        if not sid:
            print("❌ attachToTarget 失败"); return
        print("✅ 已附加到页面")

        async def eval_js(expr):
            mid = await cdp('Runtime.evaluate', 
                           {'expression': expr, 'returnByValue': True})
            dl = time.time() + 10
            while time.time() < dl:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                if msg.get('id') == mid:
                    return msg.get('result', {}).get('result', {}).get('value')
            return None

        # 1. 获取真实域名
        print("\n[1] 获取真实域名...")
        await cdp('Page.navigate', {'url': 'https://leyu.me'})
        await asyncio.sleep(4)
        url_val = await eval_js("window.location.href")
        m = re.match(r"(https://[^/]+)", url_val or "")
        if not m: print("❌ 无法解析域名"); return
        domain = m.group(1)
        print(f"  域名: {domain}")

        # 2. 安装 XMLHttpRequest hook（关键：捕获 w 参数）
        print("\n[2] 安装 Hook...")
        hook_ok = await eval_js('''
        (function(){
            window.__hdt = {captures:[]};
            var _open = XMLHttpRequest.prototype.open;
            var _send = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(m, u) {
                this._url = u; this._method = m;
                return _open.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(b) {
                var u = this._url || '';
                var self = this;
                this.addEventListener('load', function() {
                    window.__hdt.captures.push({
                        url: u.substring(0, 500),
                        method: self._method,
                        reqBody: (b || '').substring(0, 500),
                        respBody: (self.responseText || '').substring(0, 1000),
                        time: Date.now(),
                    });
                });
                return _send.apply(this, arguments);
            };
            return 'hooked';
        })()
        ''')
        print(f"  Hook: {hook_ok}")

        # 3. 导航到登录页
        print(f"\n[3] 打开登录页...")
        await cdp('Page.navigate', {'url': f'{domain}/user/login'})
        await asyncio.sleep(4)
        url_val = await eval_js("window.location.href")
        print(f"  URL: {url_val}")

        if 'user/login' not in str(url_val):
            print("  ⚠️ 没有到登录页（可能已有 session），清除 session 重试...")
            await eval_js("localStorage.clear()")
            await cdp('Network.clearBrowserCookies')
            await cdp('Page.navigate', {'url': f'{domain}/user/login'})
            await asyncio.sleep(4)
            url_val = await eval_js("window.location.href")
            print(f"  URL: {url_val}")

        # 4. 自动填表
        print(f"\n[4] 自动填表...")
        esc_usr = LEYU_USER.replace("\\", "\\\\").replace("'", "\\'")
        esc_pwd = LEYU_PWD.replace("\\", "\\\\").replace("'", "\\'")
        fill = await eval_js(f'''
        (function(){{
            var inputs = document.querySelectorAll('input');
            var ui = null, pi = null;
            for (var inp of inputs) {{
                if (inp.type === 'password') pi = inp;
                else if (!ui && inp.type !== 'hidden') ui = inp;
            }}
            if (!ui || !pi) return 'no_inputs: found ' + inputs.length;
            var setVal = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setVal.call(ui, '{esc_usr}');
            ui.dispatchEvent(new Event('input', {{bubbles:true}}));
            ui.dispatchEvent(new Event('change', {{bubbles:true}}));
            setVal.call(pi, '{esc_pwd}');
            pi.dispatchEvent(new Event('input', {{bubbles:true}}));
            pi.dispatchEvent(new Event('change', {{bubbles:true}}));
            return 'filled';
        }})()
        ''')
        print(f"  填表: {fill}")

        # 5. 等待用户手动操作
        print(f"\n[5] ⏳ 请在浏览器中:")
        print(f"  1. 点击「登录」按钮")
        print(f"  2. 手动点击验证码（点 3 个字图）")
        print(f"  3. 等待登录完成")
        print(f"  （Hook 会自动捕获所有请求数据）")
        print(f"  （按 Ctrl+C 退出）")
        print()

        for i in range(120):
            await asyncio.sleep(1)
            captures = await eval_js("JSON.stringify(window.__hdt.captures)")
            if captures:
                cap_data = json.loads(captures)
                if cap_data:
                    # 检查是否捕获到 verify
                    has_verify = any('botion.com/verify' in c.get('url','') for c in cap_data)
                    has_validate = any('validateGeeCheckV2' in c.get('url','') for c in cap_data)
                    has_login = any('/user/login' in c.get('url','') for c in cap_data)
                    
                    if has_verify or has_validate or has_login:
                        print(f"  [{i}s] 已捕获: verify={'✅' if has_verify else '❌'} validate={'✅' if has_validate else '❌'} login={'✅' if has_login else '❌'}")
                    
                    # 如果捕获到 login 响应，任务完成
                    login_resp = [c for c in cap_data if '/user/login' in c.get('url','') and 'token' in c.get('respBody','')]
                    if login_resp:
                        print(f"\n🎉 登录成功! 保存数据...")
                        
                        # 提取 w 参数
                        verify_reqs = [c for c in cap_data if 'botion.com/verify' in c.get('url','')]
                        w_param = ""
                        for vr in verify_reqs:
                            parsed = urllib.parse.urlparse(vr['url'])
                            qs = urllib.parse.parse_qs(parsed.query)
                            if 'w' in qs:
                                w_param = qs['w'][0]
                                print(f"  捕获到 w: {len(w_param)} hex chars")
                        
                        # 保存
                        output = {
                            "timestamp": int(time.time()),
                            "captures": cap_data,
                            "w": w_param,
                            "w_len": len(w_param),
                        }
                        DATA_DIR.mkdir(parents=True, exist_ok=True)
                        (DATA_DIR / "hook_captured.json").write_text(
                            json.dumps(output, indent=2, ensure_ascii=False))
                        
                        # 也保存纯 w
                        if w_param:
                            (DATA_DIR / "real_w_captured.txt").write_text(w_param)
                            print(f"  w 已保存到 data/real_w_captured.txt")
                        
                        print(f"  完整数据已保存到 data/hook_captured.json")
                        return
            
            if i % 10 == 9:
                print(f"  ...等待中 ({i+1}s)")
        
        # 超时 - 输出已有捕获
        final = await eval_js("JSON.stringify(window.__hdt.captures)")
        if final:
            data = json.loads(final)
            print(f"\n⏰ 超时，捕获到 {len(data)} 个请求:")
            for c in data:
                print(f"  {c['method']} {c['url'][:100]}")
        else:
            print(f"\n⏰ 超时，未捕获到任何请求")

if __name__ == '__main__':
    asyncio.run(main())
