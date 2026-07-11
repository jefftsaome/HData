#!/usr/bin/env python3
"""极简版：CDP Network 捕获 SDK verify 请求的 w 参数。
用法: uv run python scripts/catch_w.py 9222
"""
import asyncio, json, os, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    async with aiohttp.ClientSession() as s:
        r=await s.get(f'http://127.0.0.1:{port}/json/list'); targets=await r.json()
        ws_url=(await (await s.get(f'http://127.0.0.1:{port}/json/version')).json()).get('webSocketDebuggerUrl','')
    if not ws_url: print("CDP不可用"); return
    pt=next((t for t in targets if t['type']=='page'),None)
    if not pt: return
    
    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        mid=0; sid=''; events=[]
        
        async def send(m,p=None):
            nonlocal mid;mid+=1
            msg={'id':mid,'method':m,'params':p or {}}
            if sid: msg['sessionId']=sid
            await ws.send(json.dumps(msg)); return mid
        
        async def cmd(m,p=None,timeout=10):
            nonlocal sid
            target=await send(m,p)
            dl=time.time()+timeout
            while time.time()<dl:
                raw=await asyncio.wait_for(ws.recv(),timeout=5)
                msg=json.loads(raw)
                if msg.get('method'): events.append(msg)
                if msg.get('method')=='Target.attachedToTarget':
                    sid=msg['params'].get('sessionId','')
                elif msg.get('result',{}).get('sessionId'):
                    sid=msg['result']['sessionId']
                if msg.get('id')==target: return msg
            return None
        
        async def js(expr):
            nonlocal sid
            while events:
                ev=events.pop(0)
                if ev.get('method')=='Target.attachedToTarget':
                    sid=ev['params'].get('sessionId','')
            r=await cmd('Runtime.evaluate',{'expression':expr,'returnByValue':True})
            return r.get('result',{}).get('result',{}).get('value') if r else None
        
        await cmd('Target.attachToTarget',{'targetId':pt['id'],'flatten':True})
        if not sid: print("❌ 无 sid"); return
        print(f"✅ sid={sid[:12]}...")

        from hdt.auth.domain import resolve_domain
        domain=resolve_domain()
        print(f"域名: {domain}")
        
        await cmd('Page.navigate',{'url':f'{domain}/user/login'})
        await asyncio.sleep(3)
        for _ in range(10):
            await asyncio.sleep(1)
            if await js('document.readyState')=='complete': break
        
        eu='lidongsen1'; ep='lds19830413'
        await js(f'''
        (function(){{var inputs=document.querySelectorAll('input');var ui=null,pi=null;
        for(var inp of inputs){{if(inp.type==='password')pi=inp;else if(!ui&&inp.type!=='hidden')ui=inp;}}
        if(!ui||!pi)return;var sv=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        sv.call(ui,'{eu}');ui.dispatchEvent(new Event('input',{{bubbles:true}}));
        sv.call(pi,'{ep}');pi.dispatchEvent(new Event('input',{{bubbles:true}}));
        }})()
        ''')
        
        await cmd('Network.enable')
        print("\n请手动点登录→验证码，脚本自动捕获 w...")
        
        for i in range(120):
            await asyncio.sleep(1)
            # 发个简单命令触发 ws 读取（收集 Network 事件）
            try: await cmd('Runtime.evaluate',{'expression':'1','returnByValue':True},timeout=2)
            except: pass
            
            # 处理积压事件
            while events:
                ev=events.pop(0)
                if ev.get('method')=='Network.requestWillBeSent':
                    req=ev.get('params',{}).get('request',{})
                    url=req.get('url','')
                    if 'botion.com/verify' in url and 'w=' in url:
                        qs=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        w=qs.get('w',[''])[0]
                        if w:
                            print(f"\n🎯 SDK w 捕获!")
                            print(f"  长度: {len(w)} hex")
                            print(f"  AES: {len(w)-256} hex = {(len(w)-256)//2} bytes")
                            print(f"  前40: {w[:40]}")
                            print(f"  后40: ...{w[-40:]}")
                            (Path(__file__).resolve().parent.parent/'data'/'sdk_w_captured.txt').write_text(w)
                            print(f"  已保存 data/sdk_w_captured.txt")
                            return
            if i%10==9: print(f"  等待({i+1}s)")

if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n退出")
