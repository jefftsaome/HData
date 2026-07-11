#!/usr/bin/env python3
"""A/B test: jfbym coordinates vs human click coordinates on same captcha."""
import asyncio, json, time, subprocess, re, aiohttp, websockets, base64, os, sys

def find_port():
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for line in r.stdout.split('\n'):
        if 'BrowserAct' in line and 'remote-debugging-port' in line:
            m = re.search(r'remote-debugging-port=(\d+)', line)
            if m: return int(m.group(1))
    return 56926

async def main():
    port = find_port()
    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json())['webSocketDebuggerUrl']
    page_t = [t for t in targets if t['type'] == 'page' and '5ttn8v' in t.get('url', '')][0]

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
            nonlocal mid; mid += 1
            await ws.send(json.dumps({'id': mid, 'method': m, 'params': p or {}, 'sessionId': sid}))
            return mid

        async def ewait(tid, timeout=5):
            dl = time.time() + timeout
            while time.time() < dl:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if msg.get('id') == tid: return msg.get('result', {}).get('result', {}).get('value')
            return None

        # Clear login state
        await ewait(await epage('Runtime.evaluate', {
            'expression': 'localStorage.clear();"cleared"', 'returnByValue': True}))

        # Persistent listener: pointerdown + capture (fires before SDK)
        await epage('Page.addScriptToEvaluateOnNewDocument', {'source': '''
        window.addEventListener("pointerdown", function(e){
            var bx = document.querySelector("[class*=botion_click]");
            if(bx){
                var br = bx.getBoundingClientRect();
                var entry = {ax:Math.round(e.clientX), ay:Math.round(e.clientY),
                             rx:Math.round(e.clientX-br.x), ry:Math.round(e.clientY-br.y)};
                var prev = localStorage.getItem("__clicks");
                var arr = prev ? JSON.parse(prev) : [];
                arr.push(entry);
                localStorage.setItem("__clicks", JSON.stringify(arr));
            }
        }, {capture:true, passive:false});
        '''})
        await asyncio.sleep(0.3)

        # Navigate + fill
        await epage('Page.navigate', {'url': 'https://www.5ttn8v.vip:9037/user/login'})
        await asyncio.sleep(4)
        await ewait(await epage('Runtime.evaluate', {
            'expression': '''(function(){
                var io=document.querySelectorAll("input"),ui=null,pi=null;
                for(var i=0;i<io.length;i++){if(!ui&&io[i].type!=="password")ui=io[i];else if(!pi&&io[i].type==="password")pi=io[i];}
                ui.focus();ui.value="lidongsen1";ui.dispatchEvent(new Event("input",{bubbles:true}));
                pi.focus();pi.value="lds19830413";pi.dispatchEvent(new Event("input",{bubbles:true}));
                var btn=null;document.querySelectorAll("span").forEach(function(s){if(s.textContent.trim()==="登录")btn=s;});
                if(btn){btn.click();return "clicked";}
                return "no_button";
            })()''', 'returnByValue': True}), timeout=10)

        # Wait for popup
        await asyncio.sleep(3)
        box = None
        for i in range(10):
            await asyncio.sleep(1)
            box = await ewait(await epage('Runtime.evaluate', {
                'expression': '(()=>{const e=document.querySelector("[class*=botion_click]");if(!e)return null;const r=e.getBoundingClientRect();return r.width>50?{x:r.x,y:r.y,w:r.width,h:r.height}:null;})()',
                'returnByValue': True}))
            if box: print(f'Popup: {box["w"]:.0f}x{box["h"]:.0f} @ ({box["x"]:.0f},{box["y"]:.0f})'); break
        else: print('No popup'); return

        # jfbym solve
        img_json = await ewait(await epage('Runtime.evaluate', {
            'expression': '''(()=>{const r={bg:null,ques:[]};document.querySelectorAll("[class*=botion_bg]").forEach(e=>{const bg=getComputedStyle(e).backgroundImage;const m=bg.match(/url\\([\\\"']?([^\\\"')\\s]+)[\\\"']?\\)/);if(m&&m[1].includes("captcha_v4"))r.bg=m[1];});document.querySelectorAll("[class*=botion] img").forEach(i=>{if(i.naturalWidth>=60&&!i.src.includes("sprite"))r.ques.push(i.src);});return JSON.stringify(r);})()''',
            'returnByValue': True}))

        from curl_cffi import requests as cr
        from hdt.auth.captcha import solve
        img_data = json.loads(img_json)
        jfbym_result = None
        for attempt in range(3):
            jfbym_result = solve(img_data['bg'], img_data['ques'], os.environ.get('JFBYM_TOKEN', ''))
            if jfbym_result: break
            await asyncio.sleep(2)

        jfbym_coords = jfbym_result['coords'] if jfbym_result else 'FAILED'
        jfbym_pts = [[int(p.split(',')[0]), int(p.split(',')[1])] for p in jfbym_coords.split('|')] if jfbym_coords != 'FAILED' else []
        print(f'\njfbym: {jfbym_coords}')
        print(f'现在点验证码3次! 点完等10秒...')

        await asyncio.sleep(12)

        clicks_raw = await ewait(await epage('Runtime.evaluate', {
            'expression': 'localStorage.getItem("__clicks")||"[]"', 'returnByValue': True}))
        clicks = json.loads(clicks_raw or '[]')
        print(f'\n捕获 {len(clicks)} 次点击:')
        for i, c in enumerate(clicks):
            print(f"  点击{i + 1}: 相对({c['rx']},{c['ry']}) 绝对({c['ax']},{c['ay']})")

        if len(clicks) >= 3 and jfbym_pts:
            print(f'\n=== 对比 ===')
            print(f'  jfbym: {jfbym_coords}')
            human_str = '|'.join(f"{c['rx']},{c['ry']}" for c in clicks[:3])
            print(f'  人工:  {human_str}')
            diffs = []
            for j in range(3):
                dx = clicks[j]['rx'] - jfbym_pts[j][0]
                dy = clicks[j]['ry'] - jfbym_pts[j][1]
                diffs.append(f"({dx:+d},{dy:+d})")
            print(f'  差异:  {", ".join(diffs)}')
        else:
            print(f'没抓到足够点击 ({len(clicks)})')


if __name__ == '__main__':
    asyncio.run(main())
