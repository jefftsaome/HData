#!/usr/bin/env python3
"""对比 jfbym 坐标 vs 人工点击坐标。

流程:
  1. 连 Chrome → 打开 leyu 登录页 → 自动填表 → 点登录
  2. 提取验证码图片 → 调 jfbym 获取坐标
  3. Hook 验证码弹窗的 click 事件（捕获你的点击坐标）
  4. 你在浏览器中手动点击验证码
  5. 对比 jfbym 坐标和你的坐标
  6. 你点击「确定」完成验证码，脚本捕获后续数据

用法:
  uv run python scripts/compare_coords.py 9222
"""
import asyncio, json, os, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets

LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "we4578")
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    
    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json()).get('webSocketDebuggerUrl', '')
    if not ws_url: print("❌ CDP 不可用"); return
    print("✅ 已连接 Chrome")
    
    page_t = next((t for t in targets if t['type'] == 'page'), None)
    if not page_t: print("❌ 无页面"); return
    
    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        mid = 0; sid = ""
        async def cdp(m, p=None):
            nonlocal mid; mid += 1
            msg = {'id': mid, 'method': m, 'params': p or {}}
            if sid: msg['sessionId'] = sid
            await ws.send(json.dumps(msg))
            return mid
        
        async def recv(timeout=5):
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            if msg.get('method') == 'Target.attachedToTarget':
                nonlocal sid; sid = msg['params']['sessionId']
            return msg
        
        await cdp('Target.attachToTarget', {'targetId': page_t['id'], 'flatten': True})
        # 等 attach 完成（sid 被设置）
        dl = time.time() + 5
        while time.time() < dl and not sid:
            try: await recv(timeout=3)
            except: break
        if not sid: print("❌ attach 失败"); return
        print("✅ 已附加到页面")
        
        async def eval_js(expr, timeout=10):
            mid = await cdp('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
            dl = time.time() + timeout
            while time.time() < dl:
                try:
                    msg = await recv(timeout=5)
                    if msg.get('id') == mid:
                        return msg.get('result',{}).get('result',{}).get('value')
                except asyncio.TimeoutError:
                    continue
            return None
        
        # ── 1. 获取域名 ──
        print("\n[1/7] 获取域名...")
        # 先尝试用 domain.py 解析
        from hdt.auth.domain import resolve_domain, DomainCache
        domain = resolve_domain()
        if not domain:
            print("  domain.py 解析失败，从浏览器获取...")
            await cdp('Page.navigate', {'url': 'https://leyu.me'})
            await asyncio.sleep(5)
            url_val = await eval_js("window.location.href")
            print(f"  当前 URL: {url_val}")
            m = re.match(r"(https://[^/]+)", url_val or "")
            domain = m.group(1) if m else ""
        else:
            print(f"  通过 domain.py 解析到: {domain}")
        
        # ── 2. 打开登录页 ──
        print("\n[2/7] 打开登录页...")
        await cdp('Page.navigate', {'url': f'{domain}/user/login'})
        # 等页面完全加载（最长 25s）
        loaded = False
        for i in range(25):
            await asyncio.sleep(1)
            ready = await eval_js("document.readyState")
            if ready == "complete":
                loaded = True
                print(f"  页面加载完成 ({i+1}s)")
                break
            print(f"  加载中... ({i+1}s)")
        if not loaded:
            print("  ⚠️ 页面加载超时")
        
        url_val = await eval_js("window.location.href")
        if 'user/login' not in str(url_val):
            print("  ⚠️ 不在登录页，清除 session 重试...")
            await eval_js("localStorage.clear(); sessionStorage.clear()")
            try: await cdp('Network.clearBrowserCookies')
            except: pass
            await cdp('Page.navigate', {'url': f'{domain}/user/login'})
            for i in range(25):
                await asyncio.sleep(1)
                ready = await eval_js("document.readyState")
                if ready == "complete": break
            url_val = await eval_js("window.location.href")
        print(f"  URL: {url_val}")
        
        # ── 3. Hook 点击事件 ──
        print("\n[3/7] 安装 Hook（捕获你的点击坐标 + jfbym 坐标）...")
        hook_ok = await eval_js('''
        (function(){
            localStorage.removeItem('__hdt_clicks');
            window.__hdt = {human_clicks: []};
            
            // Hook 所有点击
            document.addEventListener('click', function(e) {
                var popup = document.querySelector('[class*="botion_click"]');
                if (!popup) return;
                var rect = popup.getBoundingClientRect();
                if (e.clientX >= rect.left && e.clientX <= rect.right &&
                    e.clientY >= rect.top && e.clientY <= rect.bottom) {
                    // 点击在验证码弹窗内
                    var relX = Math.round(e.clientX - rect.left);
                    var relY = Math.round(e.clientY - rect.top);
                    // 缩放到 300x200 坐标系
                    var imgX = Math.round(relX * 300 / rect.width);
                    var imgY = Math.round(relY * 200 / rect.height);
                    console.log(
                        "%c🧑 人工点击: (" + relX + "," + relY + ") 弹窗内 " +
                        "→ 缩放至300x200: (" + imgX + "," + imgY + ")",
                        "color: #2196F3; font-size: 14px; font-weight: bold"
                    );
                    var click = {
                        x: relX, y: relY,
                        absX: Math.round(e.clientX),
                        absY: Math.round(e.clientY),
                        imgX: imgX, imgY: imgY,
                        t: Date.now()
                    };
                    window.__hdt.human_clicks.push(click);
                    try {
                        localStorage.setItem('__hdt_clicks',
                            JSON.stringify(window.__hdt.human_clicks));
                    } catch(e) {}
                }
            }, true);
            
            return 'hook_ok';
        })()
        ''')
        print(f"  Hook: {hook_ok}")
        
        # ── 4. 自动填表 + 点击登录 ──
        print("\n[4/7] 填表 + 点登录...")
        esc_usr = LEYU_USER.replace("\\","\\\\").replace("'","\\'")
        esc_pwd = LEYU_PWD.replace("\\","\\\\").replace("'","\\'")
        fill = await eval_js(f'''
        (function(){{
            var inputs = document.querySelectorAll('input');
            var ui=null, pi=null;
            for(var inp of inputs) {{
                if(inp.type==='password') pi=inp;
                else if(!ui && inp.type!=='hidden') ui=inp;
            }}
            if(!ui||!pi) return 'no_inputs:'+inputs.length;
            var sv=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
            sv.call(ui,'{esc_usr}'); ui.dispatchEvent(new Event('input',{{bubbles:true}}));
            sv.call(pi,'{esc_pwd}'); pi.dispatchEvent(new Event('input',{{bubbles:true}}));
            return 'filled';
        }})()
        ''')
        print(f"  填表: {fill}")
        
        # 点登录
        await eval_js('''
        (function() {
            var btns = document.querySelectorAll('button, span, a');
            for(var b of btns) {
                if(b.textContent.trim() === '登录') { b.click(); return 'clicked'; }
            }
            return 'no_button';
        })()
        ''')
        print("  已点击登录按钮，等待验证码弹窗...")
        
        # ── 5. 等待验证码弹窗 ──
        print("\n[5/7] 等待验证码弹窗...")
        popup_box = None
        for i in range(15):
            await asyncio.sleep(1)
            box = await eval_js('''
            (function() {
                var e = document.querySelector('[class*="botion_click"]');
                if(!e) return null;
                var r = e.getBoundingClientRect();
                return r.width > 50 ? {x:r.x, y:r.y, w:r.width, h:r.height} : null;
            })()
            ''')
            if box:
                popup_box = box
                print(f"  弹窗出现: {box['w']:.0f}x{box['h']:.0f} @ ({box['x']:.0f},{box['y']:.0f})")
                break
            print(f"  ...等待 ({i+1}s)")
        else:
            print("❌ 验证码未出现"); return
        
        # ── 6. 提取图片 + jfbym ──
        print("\n[6/7] 提取验证码 + jfbym 识别...")
        img_json = await eval_js('''
        JSON.stringify((function(){
            var r = {bg: null, ques: []};
            document.querySelectorAll('[class*="botion_bg"]').forEach(function(e){
                var bg = getComputedStyle(e).backgroundImage;
                var m = bg.match(/url\\(["']?([^"')\\s]+)["']?\\)/);
                if(m && m[1] && m[1].indexOf('captcha_v4') >= 0) r.bg = m[1];
            });
            document.querySelectorAll('[class*="botion"] img').forEach(function(i){
                if(i.naturalWidth >= 60 && i.src.indexOf('sprite') < 0) r.ques.push(i.src);
            });
            return r;
        })())
        ''')
        if not img_json: print("❌ 提取图片失败"); return
        
        img_data = json.loads(img_json)
        if not img_data.get('bg') or len(img_data.get('ques',[])) < 3:
            print("❌ 图片不完整"); return
        
        print(f"  背景图: {img_data['bg'][:60]}...")
        for i, q in enumerate(img_data['ques']):
            print(f"  字图{i+1}: {q.split('/')[-1][:30]}")
        
        # jfbym
        if JFBYM_TOKEN:
            from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
            solver = JfbymSolver(api_token=JFBYM_TOKEN)
            challenge = CaptchaChallenge(
                lot_number='', payload='', process_token='',
                bg_url=img_data['bg'], ques_urls=img_data['ques'][:3],
                captcha_id='eaffad4f65a38a259ae369faf0c2f1a3',
            )
            try:
                sol = await solver.solve(challenge)
                jfbym_pts = sol.pts
                print(f"  🤖 jfbym 坐标 (基于 300x200): {jfbym_pts}")
                if popup_box:
                    for i, (x, y) in enumerate(jfbym_pts):
                        sx = popup_box['x'] + x * (popup_box['w'] / 300)
                        sy = popup_box['y'] + y * (popup_box['h'] / 200)
                        print(f"     jfbym #{i+1}: ({x},{y}) → 弹窗({sx:.0f},{sy:.0f})")
            except Exception as e:
                print(f"  ❌ jfbym 失败: {e}")
                jfbym_pts = []
        else:
            jfbym_pts = []
            print("  ⏭️ 跳过 jfbym（未设置 JFBYM_TOKEN）")
        
        # 存 jfbym 坐标 + 弹窗位置到 localStorage（页面跳转后不丢失）
        if jfbym_pts:
            await eval_js(f'localStorage.setItem("__hdt_jfbym", JSON.stringify({json.dumps(jfbym_pts)}))')
        await eval_js(f'localStorage.setItem("__hdt_popup", JSON.stringify({json.dumps(popup_box)}))')
        
        # ── 7. 等待你点击验证码 ──
        print(f"\n[7/7] 🖱️ 请在浏览器中手动点击验证码（点 3 个字图）!")
        print(f"  点击完成后，验证码会自动提交")
        print(f"  脚本会自动捕获你的点击坐标并对比 jfbym")
        print(f"  （按 Ctrl+C 退出）")
        print()
        
        last_human_count = 0
        for i in range(120):
            await asyncio.sleep(1)
            
            # 获取你的点击
            clicks_json = await eval_js("JSON.stringify(window.__hdt.human_clicks)")
            if clicks_json:
                human_clicks = json.loads(clicks_json)
            else:
                human_clicks = []
            
            # 有新点击时，打印到终端
            if len(human_clicks) > last_human_count:
                for j in range(last_human_count, len(human_clicks)):
                    c = human_clicks[j]
                    rel = f"({c['x']}, {c['y']})"
                    img = f"({c.get('imgX', '?')}, {c.get('imgY', '?')})"
                    print(f"  🖱️ 人工点击 #{j+1}: 弹窗内{rel} → 缩放至300x200:{img}")
                last_human_count = len(human_clicks)
            
            # 检查登录是否成功（页面跳转或 token 出现）
            url_now = await eval_js("window.location.href")
            if url_now and 'user/login' not in url_now and 'register' not in url_now:
                print(f"\n  ✅ 登录成功! 页面跳转到: {url_now}")
                
                # 最终捕获
                raw_val = await eval_js("localStorage.getItem('__hdt_clicks')")
                print(f"  [debug] localStorage clicks: {raw_val[:80] if raw_val and raw_val != 'null' else 'EMPTY'}")
                if raw_val and raw_val != 'null':
                    all_clicks = json.loads(raw_val)
                    
                    # 过滤出验证码弹窗内的点击
                    popup_json = await eval_js("localStorage.getItem('__hdt_popup')")
                    popup_box2 = json.loads(popup_json) if popup_json else popup_box
                    bx, by = popup_box2['x'], popup_box2['y']
                    bw, bh = popup_box2['w'], popup_box2['h']
                    captcha_clicks = [c for c in all_clicks 
                                      if bx <= c.get('absX',0) <= bx+bw 
                                      and by <= c.get('absY',0) <= by+bh]
                    if popup_box:
                        bx, by = popup_box['x'], popup_box['y']
                        bw, bh = popup_box['w'], popup_box['h']
                        captcha_clicks = [c for c in all_clicks 
                                          if bx <= c.get('absX',0) <= bx+bw 
                                          and by <= c.get('absY',0) <= by+bh]
                    else:
                        captcha_clicks = all_clicks
                    
                    print(f"\n{'='*60}")
                    print(f"  坐标对比结果")
                    print(f"{'='*60}")
                    
                    if jfbym_pts:
                        print(f"\n  🤖 jfbym 坐标 (基于 300x200 原图):")
                        for i, (x, y) in enumerate(jfbym_pts):
                            sx = popup_box2['x'] + x * (popup_box2['w'] / 300)
                            sy = popup_box2['y'] + y * (popup_box2['h'] / 200)
                            print(f"     第{i+1}点: ({x}, {y}) → 弹窗坐标 ({sx:.0f}, {sy:.0f})")
                    
                    print(f"\n  🧑 人工点击坐标 (弹窗内相对坐标):")
                    for i, c in enumerate(captcha_clicks):
                        print(f"     第{i+1}点: ({c.get('x',0)}, {c.get('y',0)}) "
                              f"绝对: ({c.get('absX',0)}, {c.get('absY',0)})")
                    
                    if jfbym_pts and captcha_clicks and len(jfbym_pts) >= len(captcha_clicks):
                        print(f"\n  📊 对比 (弹窗内相对坐标，缩放至 300x200):")
                        for i in range(min(len(jfbym_pts), len(captcha_clicks))):
                            jx, jy = jfbym_pts[i]
                            hx = int(captcha_clicks[i]['x'] * 300 / popup_box2['w'])
                            hy = int(captcha_clicks[i]['y'] * 200 / popup_box2['h'])
                            dx, dy = jx - hx, jy - hy
                            dist = (dx**2 + dy**2) ** 0.5
                            marker = "✅" if dist < 10 else ("⚠️" if dist < 20 else "❌")
                            print(f"     {marker} 第{i+1}点: jfbym=({jx},{jy}) 人工=({hx},{hy}) 偏移=({dx},{dy}) 距离={dist:.0f}px")
                    
                    # 保存数据
                    output = {
                        'timestamp': int(time.time()),
                        'jfbym_coords': jfbym_pts,
                        'human_clicks': captcha_clicks,
                        'popup_box': popup_box2,
                    }
                    (DATA_DIR / 'coord_comparison.json').write_text(
                        json.dumps(output, indent=2, ensure_ascii=False))
                    print(f"\n  💾 已保存到 data/coord_comparison.json")
                
                # 获取 token
                ls = await eval_js("JSON.stringify({t: localStorage.getItem('X-API-TOKEN') || ''})")
                if ls:
                    ls_data = json.loads(ls)
                    if ls_data.get('t'):
                        print(f"\n  🔑 Token: {ls_data['t'][:40]}...")
                return
            
            if i % 10 == 9:
                hc = len(human_clicks) if human_clicks else 0
                print(f"  ...等待中 ({i+1}s) 人工点击: {hc}次")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ 退出")
