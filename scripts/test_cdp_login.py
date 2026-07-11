#!/usr/bin/env python3
"""纯 raw CDP 登录测试 — 验证 CDP Input+Runtime 方案"""
import asyncio, json, time, aiohttp, websockets, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hdt.auth.captcha import solve

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 64371

    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r.json())['webSocketDebuggerUrl']
        r2 = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r2.json()

    page_t = next((t for t in targets if t['type']=='page' and 'qgayax' in t.get('url','')), None)
    if not page_t: page_t = [t for t in targets if t['type']=='page'][-1]
    print(f'Page: {page_t.get("url","")[:80]}')

    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        mid=0; extra={}
        async def cdp(method, params=None, sid=None):
            nonlocal mid; mid+=1
            m={'id':mid,'method':method,'params':params or {}}
            if sid: m['sessionId']=sid
            await ws.send(json.dumps(m)); return mid

        async def wait(tid, timeout=5):
            dl=time.time()+timeout
            while time.time()<dl:
                msg=json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if msg.get('id')==tid: return msg
                if msg.get('method')=='Target.attachedToTarget': extra['sid']=msg['params']['sessionId']
            return {}

        mid=await cdp('Target.attachToTarget',{'targetId':page_t['id'],'flatten':True})
        resp=await wait(mid); sid=resp.get('result',{}).get('sessionId') or extra.get('sid','')
        print(f'SID: {sid[:20]}...')

        # Navigate
        mid=await cdp('Page.navigate',{'url':'https://www.qgayax.vip:9174/user/login'},sid=sid)
        await asyncio.sleep(3)

        # Fill + submit
        mid=await cdp('Runtime.evaluate',{
            'expression':'''(()=>{
                const io=document.querySelectorAll('input');
                if(io.length>=2){
                    io[0].value='lidongsen1';io[0].dispatchEvent(new Event('input',{bubbles:true}));
                    io[1].value='lds19830413';io[1].dispatchEvent(new Event('input',{bubbles:true}));
                    document.querySelector('span.sV0BIdNgkCghFjH6HXzUFg__')?.click();
                }
                return 'ok';
            })()''',
            'returnByValue':True},sid=sid)
        await wait(mid); print('Submitted')

        # Wait popup
        for i in range(15):
            await asyncio.sleep(1)
            mid=await cdp('Runtime.evaluate',{
                'expression':'''(()=>{const e=document.querySelector('[class*="botion_click"]');if(!e)return null;const r=e.getBoundingClientRect();return r.width>50?{x:r.x,y:r.y,w:r.width,h:r.height}:null;})()''',
                'returnByValue':True},sid=sid)
            resp=await wait(mid); box=resp.get('result',{}).get('result',{}).get('value')
            if box:
                print(f'Popup: {box["w"]:.0f}x{box["h"]:.0f} @ ({box["x"]:.0f},{box["y"]:.0f})'); break
        else: print('No popup'); return

        # Images
        mid=await cdp('Runtime.evaluate',{
            'expression':'''(()=>{const r={bg:null,ques:[]};document.querySelectorAll('[class*="botion_bg"]').forEach(e=>{const bg=getComputedStyle(e).backgroundImage;const m=bg.match(/url\\([\"']?([^\"')\\s]+)[\"']?\\)/);if(m&&m[1].includes("captcha_v4"))r.bg=m[1];});document.querySelectorAll('[class*="botion"] img').forEach(i=>{if(i.naturalWidth>=60&&i.naturalHeight>=60&&!i.src.includes("sprite"))r.ques.push(i.src);});return JSON.stringify(r);})()''',
            'returnByValue':True},sid=sid)
        resp=await wait(mid); img_data=json.loads(resp.get('result',{}).get('result',{}).get('value','{}'))

        for attempt in range(3):
            result=solve(img_data['bg'],img_data['ques'],os.getenv('JFBYM_TOKEN',''))
            if result: break
            print(f'jfbym retry {attempt+1}...'); await asyncio.sleep(2)
        if not result: print('jfbym failed'); return
        pts=[[int(p.split(',')[0]),int(p.split(',')[1])] for p in result['coords'].split('|')]
        print(f'Coords: {result["coords"]}')

        # Click
        sx_f=box['w']/300.0; sy_f=box['h']/200.0
        for i,(x,y) in enumerate(pts):
            sx=box['x']+x*sx_f; sy=box['y']+y*sy_f
            print(f'Click {i+1}: ({sx:.0f},{sy:.0f})')
            await cdp('Input.dispatchMouseEvent',{'type':'mouseMoved','x':sx,'y':sy},sid=sid)
            await asyncio.sleep(0.08)
            await cdp('Input.dispatchMouseEvent',{'type':'mousePressed','x':sx,'y':sy,'button':'left','clickCount':1},sid=sid)
            await asyncio.sleep(0.08)
            await cdp('Input.dispatchMouseEvent',{'type':'mouseReleased','x':sx,'y':sy,'button':'left','clickCount':1},sid=sid)
            await asyncio.sleep(0.4)

        # Wait token
        print('Waiting...')
        for _ in range(40):
            await asyncio.sleep(1)
            mid=await cdp('Runtime.evaluate',{'expression':'localStorage.getItem("X-API-TOKEN")||""','returnByValue':True},sid=sid)
            resp=await wait(mid); token=resp.get('result',{}).get('result',{}).get('value','')
            if token and len(token)>10:
                print(f'✅ {token[:60]}...'); return
        print('Timeout')

asyncio.run(main())
