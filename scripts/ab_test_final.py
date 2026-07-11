#!/usr/bin/env python3
"""A/B test: jfbym vs human clicks. Auto-detect port, inject double hooks, wait for user."""
import asyncio, json, time, subprocess, re, aiohttp, websockets, base64, os
from pathlib import Path
from curl_cffi import requests as cr

ABDIR = Path(__file__).resolve().parent.parent / "data" / "abtest"
ABDIR.mkdir(parents=True, exist_ok=True)


def find_port():
    r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    for line in r.stdout.split('\n'):
        if 'BrowserAct' in line and '--remote-debugging-port' in line and '--store_data_path' in line:
            m = re.search(r'remote-debugging-port=(\d+)', line)
            if m: return int(m.group(1))
    raise RuntimeError("browser-act not running")


async def main():
    port = find_port()
    print(f"Port: {port}")

    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list'); targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version'); ws_url = (await r2.json())['webSocketDebuggerUrl']

    page_t = [t for t in targets if t['type'] == 'page' and 'login' in t.get('url', '')][0]

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        mid = 0; extra = {}

        async def cdp(m, p=None, sid=None):
            nonlocal mid; mid += 1
            m2 = {'id': mid, 'method': m, 'params': p or {}}
            if sid: m2['sessionId'] = sid
            await ws.send(json.dumps(m2)); return mid

        async def w8(tid, timeout=5):
            dl = time.time() + timeout
            while time.time() < dl:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if msg.get('id') == tid: return msg
                if msg.get('method') == 'Target.attachedToTarget': extra['sid'] = msg['params']['sessionId']
            return {}

        mid = await cdp('Target.attachToTarget', {'targetId': page_t['id'], 'flatten': True})
        resp = await w8(mid)
        sid = resp.get('result', {}).get('sessionId') or extra.get('sid', '')

        async def epage(m, p=None):
            mid = await cdp(m, p, sid=sid); r = await w8(mid)
            return r.get('result', {}).get('result', {}).get('value')

        # Clear + double hooks
        await epage('Runtime.evaluate', {'expression': 'localStorage.clear()', 'returnByValue': True})

        # Hook 1: addEventListener wrapper for ALL event types
        await epage('Runtime.evaluate', {'expression': '''
(function(){
    var o=EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener=function(t,fn,opts){
        var w=function(e){
            var bx=document.querySelector("[class*=botion_click]");
            if(bx){var br=bx.getBoundingClientRect();
                var en={t:t,ax:Math.round(e.clientX),ay:Math.round(e.clientY),
                        rx:Math.round(e.clientX-br.x),ry:Math.round(e.clientY-br.y)};
                var p=localStorage.getItem("c"),a=p?JSON.parse(p):[];a.push(en);localStorage.setItem("c",JSON.stringify(a));}
            return fn.apply(this,arguments);
        };return o.call(this,t,w,opts);
    };return"h1";
})()''', 'returnByValue': True})

        # Hook 2: document capture listeners for specific event types
        await epage('Runtime.evaluate', {'expression': '''
["pointerdown","mousedown","click","touchstart","pointerup","mouseup"].forEach(function(t){
    document.addEventListener(t,function(e){
        var bx=document.querySelector("[class*=botion_click]");
        if(bx){var br=bx.getBoundingClientRect();
            var en={t:"doc_"+t,ax:Math.round(e.clientX),ay:Math.round(e.clientY),
                    rx:Math.round(e.clientX-br.x),ry:Math.round(e.clientY-br.y)};
            var p=localStorage.getItem("c"),a=p?JSON.parse(p):[];a.push(en);localStorage.setItem("c",JSON.stringify(a));}
    },{capture:true,passive:false});
});return"h2";
''', 'returnByValue': True})

        print('Hooks installed. Go click login button, then click captcha 3 times.')

        box = None
        for i in range(300):
            await asyncio.sleep(1)
            box = await epage('Runtime.evaluate', {
                'expression': '(()=>{const e=document.querySelector("[class*=botion_click]");if(!e)return null;const r=e.getBoundingClientRect();return r.width>50?{x:r.x,y:r.y,w:r.width,h:r.height}:null;})()',
                'returnByValue': True})
            if box:
                print('\nCaptcha appeared! Click 3 times!')
                info = json.loads(await epage('Runtime.evaluate', {'expression': """(()=>{const r={bg:null,ques:[]};
document.querySelectorAll('[class*="botion_bg"]').forEach(e=>{const bg=getComputedStyle(e).backgroundImage;const m=bg.match(/url\\(['\"]?([^'\")\\s]+)['\"]?\\)/);if(m&&m[1].includes('captcha_v4'))r.bg=m[1];});
document.querySelectorAll('[class*="botion"] img').forEach(i=>{if(i.naturalWidth>=60&&!i.src.includes('sprite'))r.ques.push(i.src);});return JSON.stringify(r);})()""",
                    'returnByValue': True}))
                from curl_cffi import requests as cr
                (ABDIR / "bg.jpg").write_bytes(cr.get(info['bg'], impersonate="chrome110", timeout=15).content)
                for j, u in enumerate(info['ques']):
                    (ABDIR / f"ques_{j + 1}.png").write_bytes(cr.get(u, impersonate="chrome110", timeout=10).content)
                from hdt.auth.captcha import solve
                jr = None
                for _ in range(3):
                    jr = solve(info['bg'], info['ques'], os.environ.get('JFBYM_TOKEN', ''))
                    if jr: break
                    await asyncio.sleep(2)
                jc = jr['coords'] if jr else 'FAILED'
                jp = [[int(p.split(',')[0]), int(p.split(',')[1])] for p in jc.split('|')] if jr else []
                print(f'jfbym: {jc}')
                await asyncio.sleep(60)
                clicks = json.loads(await epage('Runtime.evaluate', {
                    'expression': 'localStorage.getItem("c")||"[]"', 'returnByValue': True}) or '[]')
                bc = [c for c in clicks if 0 < c.get('rx', 0) < 400]
                print(f'\nHuman clicks ({len(bc)}):')
                for k, c in enumerate(bc):
                    print(f"  {k + 1}: type={c.get('t', '?')} rel=({c['rx']},{c['ry']})")
                if len(bc) >= 3 and jp:
                    print('\n=== COMPARISON ===')
                    print(f'jfbym: {jc}')
                    hs = '|'.join(f"{c['rx']},{c['ry']}" for c in bc[:3])
                    print(f'human: {hs}')
                    for j in range(3):
                        print(f"  d{j + 1}: ({bc[j]['rx'] - jp[j][0]:+d},{bc[j]['ry'] - jp[j][1]:+d})")
                return
        print('Timeout')


if __name__ == '__main__':
    asyncio.run(main())
