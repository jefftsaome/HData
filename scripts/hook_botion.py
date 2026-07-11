#!/usr/bin/env python3
"""Hook botion SDK to capture e_obj, w, captcha_output, RSA key during real login."""
import asyncio, json, time, base64, aiohttp, websockets, os, sys

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 56926
    TOKEN = os.environ.get("JFBYM_TOKEN", "")

    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json()).get('webSocketDebuggerUrl', '')

    if not ws_url:
        print("CDP not available"); return

    page_t = [t for t in targets if t['type'] == 'page' and 'login' in t.get('url', '')]
    if not page_t:
        print("No login page found")
        return
    page_t = page_t[0]
    print(f'Page: {page_t["url"][:80]}')

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        mid = 0
        extra = {}

        async def cdp(m, p=None, sid=None):
            nonlocal mid; mid += 1
            m2 = {'id': mid, 'method': m, 'params': p or {}}
            if sid: m2['sessionId'] = sid
            await ws.send(json.dumps(m2)); return mid

        async def wait(tid, to=5):
            dl = time.time() + to
            while time.time() < dl:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=to))
                if msg.get('id') == tid: return msg
                if msg.get('method') == 'Target.attachedToTarget':
                    extra['sid'] = msg['params']['sessionId']
            return {}

        mid = await cdp('Target.attachToTarget', {'targetId': page_t['id'], 'flatten': True})
        resp = await wait(mid)
        sid = resp.get('result', {}).get('sessionId') or extra.get('sid', '')

        async def epage(m, p=None):
            nonlocal mid; mid += 1
            await ws.send(json.dumps({'id': mid, 'method': m, 'params': p or {}, 'sessionId': sid}))
            return mid

        async def ewait(tid, to=5):
            dl = time.time() + to
            while time.time() < dl:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=to))
                if msg.get('id') == tid:
                    return msg.get('result', {}).get('result', {}).get('value')
            return None

        # 1. Install hooks
        hook_ok = await ewait(await epage('Runtime.evaluate', {'expression': '''
        (function(){
            window.__hdt = {vr:[],vv:[],vl:[],wv:[],vvr:[],vlr:[]};
            var oxo=XMLHttpRequest.prototype.open, oxs=XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open=function(m,u){this._u=u;this._m=m;return oxo.apply(this,arguments)};
            XMLHttpRequest.prototype.send=function(b){
                var u=this._u||'',m=this._m||'';
                if(u.includes("verify")&&u.includes("botion")) window.__hdt.vr.push({u:u,w:u.split("w=")[1]?.split("&")[0]?.slice(0,200)});
                if(u.includes("validateGeeCheckV2")) window.__hdt.vv.push({u:u,b:b});
                if(u.includes("/user/login")) window.__hdt.vl.push({u:u,b:b});
                this.addEventListener("load",function(){
                    if(u.includes("verify")) window.__hdt.wv.push({r:this.responseText.slice(0,800)});
                    if(u.includes("validateGeeCheckV2")) window.__hdt.vvr.push({r:this.responseText.slice(0,500)});
                    if(u.includes("/user/login")) window.__hdt.vlr.push({r:this.responseText.slice(0,500)});
                });
                return oxs.apply(this,arguments);
            };
            return "hooked";
        })()''', 'returnByValue': True}))
        print(f"Hooks: {hook_ok}")

        # 2. Fill form + click login
        fill = await ewait(await epage('Runtime.evaluate', {'expression': '''
        (function(){
            var io=document.querySelectorAll("input"),ui=null,pi=null;
            for(var i=0;i<io.length;i++){if(!ui&&io[i].type!=="password")ui=io[i];else if(!pi&&io[i].type==="password")pi=io[i];}
            if(!ui||!pi) return "no_inputs("+io.length+")";
            ui.focus();ui.value="lidongsen1";ui.dispatchEvent(new Event("input",{bubbles:true}));
            pi.focus();pi.value="lds19830413";pi.dispatchEvent(new Event("input",{bubbles:true}));
            var btn=null;document.querySelectorAll("span").forEach(function(s){if(s.textContent.trim()==="登录")btn=s;});
            if(btn){btn.click();return "clicked";}
            return "no_button";
        })()''', 'returnByValue': True}))
        print(f"Form: {fill}")

        # 3. Wait for popup
        await asyncio.sleep(3)
        box = None
        for i in range(10):
            await asyncio.sleep(1)
            box = await ewait(await epage('Runtime.evaluate', {
                'expression': '(()=>{const e=document.querySelector("[class*=botion_click]");if(!e)return null;const r=e.getBoundingClientRect();return r.width>50?{x:r.x,y:r.y,w:r.width,h:r.height}:null;})()',
                'returnByValue': True}))
            if box:
                print(f'Popup: {box["w"]:.0f}x{box["h"]:.0f}')
                break
        else:
            print('No popup'); return

        # 4. Get images + jfbym solve
        img_json = await ewait(await epage('Runtime.evaluate', {
            'expression': """(()=>{const r={bg:null,ques:[]};document.querySelectorAll('[class*="botion_bg"]').forEach(e=>{const bg=getComputedStyle(e).backgroundImage;const m=bg.match(/url\\([\"']?([^\"')\\s]+)[\"']?\\)/);if(m&&m[1].includes("captcha_v4"))r.bg=m[1];});document.querySelectorAll('[class*="botion"] img').forEach(i=>{if(i.naturalWidth>=60&&!i.src.includes("sprite"))r.ques.push(i.src);});return JSON.stringify(r);})()""",
            'returnByValue': True}))

        from curl_cffi import requests as cr
        from hdt.auth.captcha import solve
        img_data = json.loads(img_json)

        for attempt in range(3):
            result = solve(img_data['bg'], img_data['ques'], TOKEN)
            if result: break
            print(f'jfbym retry {attempt + 1}...')
            await asyncio.sleep(2)
        if not result: print('jfbym failed'); return

        pts = [[int(p.split(',')[0]), int(p.split(',')[1])] for p in result['coords'].split('|')]
        print(f'Coords: {result["coords"]}')

        # 5. CDP Input click
        sx_f = box['w'] / 300.0
        sy_f = box['h'] / 200.0
        for (x, y) in pts:
            sx = box['x'] + x * sx_f
            sy = box['y'] + y * sy_f
            print(f'Click: ({sx:.0f},{sy:.0f})')
            await epage('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': sx, 'y': sy, 'modifiers': 0})
            await asyncio.sleep(0.08)
            await epage('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': sx, 'y': sy, 'button': 'left', 'clickCount': 1, 'modifiers': 0})
            await asyncio.sleep(0.08)
            await epage('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': sx, 'y': sy, 'button': 'left', 'clickCount': 1, 'modifiers': 0})
            await asyncio.sleep(0.4)

        # 6. Wait and capture
        print('Waiting for SDK to process...')
        for i in range(25):
            await asyncio.sleep(1)
            cap = await ewait(await epage('Runtime.evaluate', {
                'expression': 'JSON.stringify(window.__hdt)', 'returnByValue': True}))
            if cap:
                data = json.loads(cap)
                has_data = sum(1 for k in ['vr', 'vv', 'vl', 'wv', 'vvr', 'vlr'] if data.get(k))
                if has_data > 0:
                    print(f'[{i}s] captured {has_data} categories')
                if data.get('vlr') or data.get('vvr'):
                    print(f'\n=== FULL CAPTURE ===')
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                    return
        else:
            cap = await ewait(await epage('Runtime.evaluate', {
                'expression': 'JSON.stringify(window.__hdt)', 'returnByValue': True}))
            if cap:
                print(f'\nPartial capture:')
                print(json.dumps(json.loads(cap), indent=2, ensure_ascii=False))

if __name__ == '__main__':
    asyncio.run(main())
