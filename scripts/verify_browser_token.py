#!/usr/bin/env python3
"""验证 process_token 假设：用浏览器 SDK 的 process_token 生成 w 调 verify。

如果成功 → 证实是我们的 Python fetch_captcha() 拿到的 process_token 被标记为非浏览器。
如果失败 → 问题在其他地方。

用法:
    JFBYM_TOKEN=xxx uv run python scripts/verify_browser_token.py 9222
"""
import asyncio, json, os, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets
from curl_cffi import requests as cr
from hdata.auth.captcha_solver import JfbymSolver, CaptchaChallenge

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "lds19830413")


async def main():
    import binascii, random
    from Crypto.Cipher import AES as AES_C
    from Crypto.PublicKey.RSA import construct
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.Util.Padding import pad
    from hdata.auth.geetest_signer import LotParser, _generate_pow, _rand_uid
    
    RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
    RSA_E = int("10001", 16)
    
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    
    # 连 CDP
    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json()).get('webSocketDebuggerUrl', '')
    if not ws_url: print("❌ CDP 不可用"); return
    
    page_t = next((t for t in targets if t['type'] == 'page'), None)
    if not page_t: print("❌ 无页面"); return
    
    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        mid=0; sid=''
        async def cdp(m,p=None):
            nonlocal mid; mid+=1
            msg={'id':mid,'method':m,'params':p or {}}
            if sid: msg['sessionId']=sid
            await ws.send(json.dumps(msg)); return mid
        async def recv(t=5):
            raw=await asyncio.wait_for(ws.recv(),timeout=t)
            msg=json.loads(raw)
            if msg.get('method')=='Target.attachedToTarget':
                nonlocal sid; sid=msg['params']['sessionId']
            return msg
        
        await cdp('Target.attachToTarget',{'targetId':page_t['id'],'flatten':True})
        dl=time.time()+5
        while time.time()<dl and not sid:
            try: await recv(3)
            except: break
        if not sid: print("❌ attach失败"); return
        print("✅ 已连接 Chrome")
        
        async def js(expr):
            mid=await cdp('Runtime.evaluate',{'expression':expr,'returnByValue':True})
            dl=time.time()+10
            while time.time()<dl:
                msg=await recv(5)
                if msg.get('id')==mid:
                    return msg.get('result',{}).get('result',{}).get('value')
            return None
        
        # 1. 获取域名
        print("\n[1] 获取域名...")
        from hdata.auth.domain import resolve_domain
        domain = resolve_domain()
        print(f"  域名: {domain}")
        
        # 2. 打开登录页
        print("\n[2] 打开登录页...")
        await cdp('Page.navigate',{'url':f'{domain}/user/login'})
        for i in range(20):
            await asyncio.sleep(1)
            rdy=await js('document.readyState')
            if rdy=='complete': print(f"  加载完成 ({i+1}s)"); break
        
        # 3. 填表 + 点登录
        print("\n[3] 填表...")
        eu=LEYU_USER.replace("'","\\'"); ep=LEYU_PWD.replace("'","\\'")
        await js(f'''
        (function(){{var inputs=document.querySelectorAll('input');var ui=null,pi=null;
        for(var inp of inputs){{if(inp.type==='password')pi=inp;else if(!ui&&inp.type!=='hidden')ui=inp;}}
        if(!ui||!pi)return;var sv=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        sv.call(ui,'{eu}');ui.dispatchEvent(new Event('input',{{bubbles:true}}));
        sv.call(pi,'{ep}');pi.dispatchEvent(new Event('input',{{bubbles:true}}));
        var btns=document.querySelectorAll('button,span,a');
        for(var b of btns){{if(b.textContent.trim()==='登录'){{b.click();break;}}}}
        }})()
        ''')
        print("  已点登录，等验证码...")
        
        # 4. 等验证码弹窗
        for i in range(15):
            await asyncio.sleep(1)
            box=await js('(function(){var e=document.querySelector("[class*=botion_click]");if(!e)return null;var r=e.getBoundingClientRect();return r.width>50;})()')
            if box: print(f"  弹窗出现 ({i+1}s)"); break
        
        # 5. 从浏览器提取 GeeTest load 数据
        print("\n[4] 提取浏览器 SDK 的 load 数据...")
        
        # 方法A: 从 iframe 里拿
        load_data = await js('''
        (function(){
            // 找 captcha iframe
            var iframes = document.querySelectorAll('iframe');
            for(var f of iframes) {
                try {
                    var doc = f.contentDocument || f.contentWindow.document;
                    if(doc) {
                        var bt = doc.defaultView ? doc.defaultView.__BOTION__ : null;
                        if(bt) return JSON.stringify({lot: bt.lotNumber, pt: bt.processToken, pay: bt.payload});
                    }
                } catch(e) {}
            }
            return 'no_iframe_data';
        })()
        ''')
        print(f"  iframe方式: {load_data}")
        
        # 方法B: 直接在主页面搜
        if not load_data or 'no_iframe' in str(load_data):
            print("  尝试从主页提取...")
            bot = await js('JSON.stringify({lot: window.__BOTION__?.lotNumber, pt: window.__BOTION__?.processToken, pay: window.__BOTION__?.payload})')
            print(f"  主页__BOTION__: {bot}")
        
        # 方法C: 从页面 URL/search params 提取
        print("  尝试从已验证的 SDK 捕获文件提取...")
        
        # 其实最简单的方法：让浏览器自己去调 verify
        # 我们 Hook XMLHttpRequest 捕获 verify 请求 URL
        # 然后用那个 verify URL 的 w 和参数
    
    print("\n❌ 浏览器 SDK 的 load 数据无法直接读取（在 iframe 内，跨域限制）")
    print("需要换方法验证 process_token 假设")
