#!/usr/bin/env python3
"""多方法捕获登录验证数据：hook → CDP Network → token 提取。逐级兜底。

用法:
    1. 启动 Chrome: /Applications/Google\\ Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222
    2. 运行: uv run python scripts/capture_login_data.py 9222
    3. 在浏览器中手动完成验证码
    4. 脚本自动捕获: w 参数 + 验证码响应 + token
"""

import asyncio, json, os, sys, time, re, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets

LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "we4578")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CAPTURED = {"w": "", "verify_resp": "", "token": "", "chain": []}


def log(msg):
    print(f"  {msg}")


async def cdp_connect(port):
    """连接 Chrome CDP，返回 ws + page + sid。"""
    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json()).get('webSocketDebuggerUrl', '')
    if not ws_url:
        return None, None, None
    
    page_t = next((t for t in targets if t['type'] == 'page'), None)
    if not page_t:
        return None, None, None
    
    ws = await websockets.connect(ws_url, max_size=10 * 10**6)
    return ws, targets, page_t


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    
    ws, targets, page_t = await cdp_connect(port)
    if not ws:
        print("❌ Chrome CDP 不可用，确保以 --remote-debugging-port=9222 启动"); return
    
    print(f"✅ 已连接 Chrome CDP (port={port})")
    
    # ── CDP 工具函数 ──
    mid = 0; sid = ""
    pending = {}  # mid → future
    
    async def cdp_send(method, params=None):
        nonlocal mid; mid += 1
        msg = {'id': mid, 'method': method, 'params': params or {}}
        if sid: msg['sessionId'] = sid
        await ws.send(json.dumps(msg))
        return mid
    
    def handle_msg(msg):
        """处理收来的消息：匹配 pending 或设置 sid。"""
        nonlocal sid
        if msg.get('method') == 'Target.attachedToTarget':
            sid = msg['params']['sessionId']
        # 处理 pending futures
        mid_val = msg.get('id')
        if mid_val in pending:
            pending[mid_val].set_result(msg)
    
    async def cdp_recv(timeout=5):
        """收消息直到有匹配的 response。"""
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            handle_msg(msg)
            return msg  # 每条消息都返回，由调用方判断
    
    async def eval_js(expr, timeout=10):
        """执行 JS 并返回值。"""
        mid = await cdp_send('Runtime.evaluate',
                            {'expression': expr, 'returnByValue': True})
        dl = time.time() + timeout
        while time.time() < dl:
            msg = await cdp_recv(timeout=5)
            if msg.get('id') == mid:
                return msg.get('result', {}).get('result', {}).get('value')
        return None
    
    # ── Attach ──
    await cdp_send('Target.attachToTarget',
                   {'targetId': page_t['id'], 'flatten': True})
    # 等一个消息拿到 sid
    msg = await cdp_recv(timeout=5)
    if msg.get('method') != 'Target.attachedToTarget':
        # 再等一个
        msg = await cdp_recv(timeout=5)
    if not sid:
        print("❌ attach 失败"); return
    log(f"已附加到页面 sid={sid[:12]}...")
    
    # ── 1. 获取真实域名 ──
    print("\n[1/6] 获取真实域名...")
    await cdp_send('Page.navigate', {'url': 'https://leyu.me'})
    await asyncio.sleep(4)
    url_val = await eval_js("window.location.href")
    m = re.match(r"(https://[^/]+)", url_val or "")
    domain = m.group(1) if m else ""
    if not domain: print("❌ 域名解析失败"); return
    print(f"  域名: {domain}")
    CAPTURED["chain"].append({"step": "domain", "value": domain})
    
    # ── 2. 导航到登录页 ──
    print("\n[2/6] 打开登录页...")
    await cdp_send('Page.navigate', {'url': f'{domain}/user/login'})
    await asyncio.sleep(4)
    url_val = await eval_js("window.location.href")
    print(f"  URL: {url_val}")
    
    if 'user/login' not in str(url_val):
        print("  ⚠️ 被重定向，清除 session 重试...")
        await eval_js("localStorage.clear(); sessionStorage.clear()")
        await cdp_send('Network.clearBrowserCookies')
        await asyncio.sleep(1)
        await cdp_send('Page.navigate', {'url': f'{domain}/user/login'})
        await asyncio.sleep(4)
        url_val = await eval_js("window.location.href")
        print(f"  URL: {url_val}")
    
    # ── 3. 方法 A: XMLHttpRequest Hook ──
    print("\n[3/6] 安装方法A: XMLHttpRequest Hook...")
    hook_ok = await eval_js('''
    (function(){
        var c = window.__hdt_captures || []; window.__hdt_captures = c;
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
                c.push({
                    url: (u || '').substring(0,600), method: self._method,
                    req: ((typeof b === 'string' ? b : '') || '').substring(0,500),
                    resp: (self.responseText || '').substring(0,1500),
                    t: Date.now()
                });
            });
            return _send.apply(this, arguments);
        };
        return 'hook_ok';
    })()
    ''')
    print(f"  Hook: {hook_ok}")
    
    # ── 4. 安装方法 B: CDP Network enable ──
    print("\n[4/6] 安装方法B: CDP Network...")
    await cdp_send('Network.enable')
    net_events = []
    print(f"  Network: enabled")
    
    # ── 5. 自动填表 ──
    print("\n[5/6] 自动填表...")
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
        if (!ui || !pi) return 'no_inputs:' + inputs.length;
        var sv = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        sv.call(ui, '{esc_usr}');
        ui.dispatchEvent(new Event('input', {{bubbles:true}}));
        sv.call(pi, '{esc_pwd}');
        pi.dispatchEvent(new Event('input', {{bubbles:true}}));
        return 'filled';
    }})()
    ''')
    print(f"  填表: {fill}")
    
    # ── 6. 等待捕获 ──
    print(f"\n[6/6] ⏳ 请在浏览器中操作（Ctrl+C 退出）:")
    print(f"  ① 点击「登录」按钮")
    print(f"  ② 手动点验证码（点 3 个字图）")
    print(f"  ③ 登录成功后脚本自动保存数据")
    print()
    
    capture_saved = False
    
    for i in range(120):
        await asyncio.sleep(1)
        
        # 处理 Network 事件
        try:
            msg = await cdp_recv(timeout=0.1)
            if msg.get('method') == 'Network.requestWillBeSent':
                req = msg.get('params', {}).get('request', {})
                net_events.append({
                    'url': req.get('url', ''),
                    'method': req.get('method', ''),
                    'type': 'requestWillBeSent',
                })
            elif msg.get('method') == 'Network.responseReceived':
                resp = msg.get('params', {}).get('response', {})
                net_events.append({
                    'url': resp.get('url', ''),
                    'type': 'responseReceived',
                })
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        
        # 从 Hook 提取数据
        cap_json = await eval_js("JSON.stringify(window.__hdt_captures || [])")
        if cap_json:
            caps = json.loads(cap_json)
            
            # 提取 w 参数
            for c in caps:
                if 'botion.com/verify' in c.get('url','') and 'w=' in c.get('url',''):
                    parsed = urllib.parse.urlparse(c['url'])
                    qs = urllib.parse.parse_qs(parsed.query)
                    if qs.get('w') and not CAPTURED['w']:
                        CAPTURED['w'] = qs['w'][0]
                        log(f"🎯 方法A捕获 w: {len(CAPTURED['w'])} hex chars")
                
                if 'botion.com/verify' in c.get('url','') and not CAPTURED['verify_resp']:
                    if c.get('resp') and 'result' in c['resp']:
                        CAPTURED['verify_resp'] = c['resp'][:500]
                        log(f"🎯 捕获 verify 响应")
                
                if '/site/api/v1/user/login' in c.get('url','') and not CAPTURED['token']:
                    if c.get('resp') and 'token' in c['resp']:
                        try:
                            resp_data = json.loads(c['resp'])
                            token = (resp_data.get('data',{}) or {}).get('token','')
                            if token:
                                CAPTURED['token'] = token
                                log(f"🎯 捕获 token: {token[:40]}...")
                        except: pass
            
            # 如果 A 方法没捕获到 w，从 Network 事件查
            if not CAPTURED['w']:
                for ev in net_events:
                    if 'botion.com/verify' in ev.get('url','') and 'w=' in ev.get('url',''):
                        parsed = urllib.parse.urlparse(ev['url'])
                        qs = urllib.parse.parse_qs(parsed.query)
                        if qs.get('w'):
                            CAPTURED['w'] = qs['w'][0]
                            log(f"🎯 方法B捕获 w: {len(CAPTURED['w'])} hex chars")
            
            # 如果 A/B 都没捕获到，从 Net 事件 raw 里搜
            if not CAPTURED['w']:
                for ev in net_events:
                    if 'botion.com/verify' in ev.get('url',''):
                        # 直接从事件 url 文本提取
                        import re as _re
                        wm = _re.search(r'w=([a-f0-9]+)', ev.get('url',''))
                        if wm:
                            CAPTURED['w'] = wm.group(1)
                            log(f"🎯 方法C(正则)捕获 w: {len(CAPTURED['w'])} hex chars")
        
        # Token 兜底：从 localStorage 直接读
        if not CAPTURED['token']:
            ls = await eval_js("JSON.stringify({t: localStorage.getItem('X-API-TOKEN') || '', u: localStorage.getItem('uuidToBase64') || ''})")
            if ls:
                ls_data = json.loads(ls)
                if ls_data.get('t'):
                    CAPTURED['token'] = ls_data['t']
                    log(f"🎯 方法D(localStorage)捕获 token: {ls_data['t'][:40]}...")
        
        # 保存条件：验证码通过（有 w + token）或超时
        if CAPTURED['w'] and CAPTURED['token'] and not capture_saved:
            await save_captured()
            capture_saved = True
            print(f"\n✅✅✅ 所有数据已保存!")
            print(f"  w: {len(CAPTURED['w'])} hex chars")
            print(f"  token: {CAPTURED['token'][:40]}...")
            return
        
        if i % 10 == 9:
            status = f"w={'✅' if CAPTURED['w'] else '⏳'} token={'✅' if CAPTURED['token'] else '⏳'}"
            print(f"  ...等待中 ({i+1}s) {status}")
    
    # 超时，保存已有数据
    if not capture_saved:
        await save_captured()
        print(f"\n⏰ 超时，已保存已有数据")
        print(f"  w={'✅' if CAPTURED['w'] else '❌'} token={'✅' if CAPTURED['token'] else '❌'}")


async def save_captured():
    """保存捕获的数据到文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # 纯 w 参数
    if CAPTURED['w']:
        (DATA_DIR / "real_w_captured.txt").write_text(CAPTURED['w'])
    
    # JSON 完整数据
    output = {
        "timestamp": int(time.time()),
        "w": CAPTURED['w'],
        "w_len": len(CAPTURED['w']),
        "token_preview": CAPTURED['token'][:40] + "..." if CAPTURED['token'] else "",
        "verify_resp_preview": CAPTURED['verify_resp'][:200] if CAPTURED['verify_resp'] else "",
        "chain": CAPTURED['chain'],
    }
    (DATA_DIR / "captured_login_data.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False))
    
    print(f"\n📁 保存:")
    if CAPTURED['w']:   print(f"  data/real_w_captured.txt ({len(CAPTURED['w'])} hex)")
    print(f"  data/captured_login_data.json")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹ 退出中...")
        asyncio.run(save_captured())
        print("已保存已有数据")
