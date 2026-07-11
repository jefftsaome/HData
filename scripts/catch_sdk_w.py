#!/usr/bin/env python3
"""捕获 SDK 的 verify URL（含 w）+ 同时用我们的代码生成 w 做对比。
用 CDP Network 事件（不受 iframe 限制）。

用法: uv run python scripts/catch_sdk_w.py 9222
"""
import asyncio, json, os, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets, binascii, random
from Crypto.Cipher import AES as AES_C
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad
from hdt.auth.geetest_signer import LotParser, _generate_pow, _rand_uid

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN","")
LEYU_USER = os.getenv("LEYU_USER","lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD","lds19830413")
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    async with aiohttp.ClientSession() as s:
        r=await s.get(f'http://127.0.0.1:{port}/json/list'); targets=await r.json()
        r2=await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url=(await r2.json()).get('webSocketDebuggerUrl','')
    if not ws_url: print("❌ CDP不可用"); return
    pt=next((t for t in targets if t['type']=='page'),None)
    if not pt: return
    
    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        mid=0;sid=''
        async def cdp(m,p=None):
            nonlocal mid;mid+=1;msg={'id':mid,'method':m,'params':p or {}}
            if sid:msg['sessionId']=sid;await ws.send(json.dumps(msg));return mid
        
        # 后台 reader + 消息队列
        cmd_q=asyncio.Queue()
        evt_q=asyncio.Queue()
        async def reader():
            nonlocal sid
            while True:
                try:
                    raw=await ws.recv()
                    msg=json.loads(raw)
                    if msg.get('method')=='Target.attachedToTarget':
                        sid=msg['params']['sessionId']
                    elif 'sessionId' in msg.get('result',{}):
                        sid=msg['result']['sessionId']
                    if msg.get('id'):
                        await cmd_q.put(msg)
                    else:
                        await evt_q.put(msg)
                except: break
        asyncio.create_task(reader())
        
        async def recv_cmd(target_id, timeout=15):
            dl=time.time()+timeout
            while time.time()<dl:
                try:msg=await asyncio.wait_for(cmd_q.get(),timeout=5)
                except:continue
                if msg.get('id')==target_id: return msg
            return None
        
        async def js(expr):
            mid=await cdp('Runtime.evaluate',{'expression':expr,'returnByValue':True})
            r=await recv_cmd(mid,timeout=15)
            return r.get('result',{}).get('result',{}).get('value') if r else None
        
        await cdp('Target.attachToTarget',{'targetId':pt['id'],'flatten':True})
        # 等 sid 被设置（reader 会自动处理 Target.attachedToTarget 事件）
        for i in range(20):
            if sid: break
            await asyncio.sleep(0.5)
        if not sid: print("❌ attach失败"); return
        print("✅ Chrome已连接")
        
        # 获取域名
        from hdt.auth.domain import resolve_domain
        domain=resolve_domain()
        
        # 打开登录页
        print("打开登录页...")
        await cdp('Page.navigate',{'url':f'{domain}/user/login'})
        for i in range(20):
            await asyncio.sleep(1)
            if await js('document.readyState')=='complete':print(f"  加载({i+1}s)");break
        
        # 填表
        eu=LEYU_USER.replace("'","\\'");ep=LEYU_PWD.replace("'","\\'")
        await js(f'''
        (function(){{var inputs=document.querySelectorAll('input');var ui=null,pi=null;
        for(var inp of inputs){{if(inp.type==='password')pi=inp;else if(!ui&&inp.type!=='hidden')ui=inp;}}
        if(!ui||!pi)return;var sv=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        sv.call(ui,'{eu}');ui.dispatchEvent(new Event('input',{{bubbles:true}}));
        sv.call(pi,'{ep}');pi.dispatchEvent(new Event('input',{{bubbles:true}}));
        }})()
        ''')
        
        # 启用 Network（捕获所有帧的请求）
        await cdp('Network.enable')
        print("Network监控已启用")
        
        print("\n请手动点「登录」→ 点验证码 → 脚本自动捕获 SDK 的 w")
        
        sdk_w = ""; load_data = None  # load_data = {lot_number, payload, process_token, pow_detail, pt, payload_protocol}
        
        for i in range(120):
            await asyncio.sleep(1)
            
            # 处理 Network 事件（从 evt_q）
            while not evt_q.empty():
                try: msg = evt_q.get_nowait()
                except: break
                
                if msg.get('method')=='Network.requestWillBeSent':
                    req=msg.get('params',{}).get('request',{})
                    url=req.get('url','')
                    # 捕获 SDK 的 verify 请求（含 w）
                    if 'botion.com/verify' in url and 'w=' in url:
                        qs=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        w_val=qs.get('w',[''])[0]
                        if w_val and not sdk_w:
                            sdk_w=w_val
                            print(f"\n🎯 捕获到 SDK w!")
                            print(f"  长度: {len(sdk_w)} hex")
                            print(f"  AES: {len(sdk_w)-256} hex = {(len(sdk_w)-256)//2} bytes")
                            print(f"  前40: {sdk_w[:40]}")
                            print(f"  后40: ...{sdk_w[-40:]}")
                    
                    # 捕获 load 请求，后续获取响应体
                    if 'botion.com/load' in url:
                        pass  # 忽略 load 请求，只要 verify 的 w
            
            if sdk_w: break
            if i%10==9:print(f"  等待...({i+1}s)")
        
        if sdk_w:
            # 保存
            (Path(__file__).resolve().parent.parent/'data'/'sdk_w_captured.txt').write_text(sdk_w)
            print(f"\n✅ 已保存到 data/sdk_w_captured.txt")
            print(f"  我们的 generate_w 产出: w=1216 hex, AES=480B")
            print(f"  SDK w 对比: AES={(len(sdk_w)-256)//2}B")
        else:
            print("❌ 未捕获到")

if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n退出")
