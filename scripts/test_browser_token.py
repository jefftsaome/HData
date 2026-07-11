#!/usr/bin/env python3
"""验证 process_token 假设：从浏览器 SDK 捕获 load API 响应中的 process_token，
用这个 token + jfbym 坐标 + 我们的 generate_w 调 verify。

如果成功 → 证明 Python fetch_captcha() 拿到的 process_token 被标记了。
如果失败 → 问题在别处。

操作流程：
  1. 脚本连 Chrome → 打开 leyu 登录页 → 填表 → 点登录
  2. 安装 XHR hook 拦截 load API 响应（获取 process_token）
  3. 验证码弹窗出现后，提取图片
  4. jfbym 识别坐标
  5. 用浏览器的 process_token + 我们的 generate_w 调 verify
  6. 你手动点击验证码（比较结果）

用法:
    JFBYM_TOKEN=xxx uv run python scripts/test_browser_token.py 9222
"""
import asyncio, json, os, re, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aiohttp, websockets
import binascii, random
from Crypto.Cipher import AES as AES_C
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad
from curl_cffi import requests as cr
from hdt.auth.captcha_solver import JfbymSolver, CaptchaChallenge
from hdt.auth.geetest_signer import LotParser, _generate_pow, _rand_uid

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "lds19830413")
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)


def make_w(ld, pts, passtime):
    """生成 w（与 generate_w 一致）。"""
    ln = ld["lot_number"]; pd = ld["pow_detail"]
    lp = LotParser()
    eo = {
        **_generate_pow(ln, CAPTCHA_ID, pd["hashfunc"], pd["version"], pd["bits"], pd["datetime"]),
        **lp.get_dict(ln),
        "biht": "1426265548", "em": {},
        "gee_guard": {"auh":"3","aup":"3","cdc":"3","egp":"3","res":"3","rew":"3","sep":"3","snh":"3"},
        "geetest": "captcha", "lang": "zh", "lot_number": ln,
        "userresponse": pts, "passtime": passtime,
    }
    rk = _rand_uid()
    ej = json.dumps(eo, separators=(',', ':'))
    cipher = AES_C.new(rk.encode(), AES_C.MODE_CBC, b"0000000000000000")
    ee = cipher.encrypt(pad(ej.encode(), AES_C.block_size))
    rc = PKCS1_v1_5.new(construct((RSA_N, RSA_E)))
    ek = rc.encrypt(rk.encode())
    return binascii.hexlify(ee).decode() + binascii.hexlify(ek).decode()


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222

    # 连接 CDP
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

        # 1. 域名
        print("\n[1] 获取域名...")
        from hdt.auth.domain import resolve_domain
        domain = resolve_domain()
        print(f"  域名: {domain}")

        # 2. 打开登录页
        print("\n[2] 打开登录页...")
        await cdp('Page.navigate',{'url':f'{domain}/user/login'})
        for i in range(20):
            await asyncio.sleep(1)
            rdy=await js('document.readyState')
            if rdy=='complete': print(f"  加载完成 ({i+1}s)"); break

        # 3. 安装 XHR hook 专门捕获 load 响应
        print("\n[3] 安装 XHR hook（捕获 load API 响应）...")
        hook = await js('''
        (function(){
            window.__hdt_load = null;
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
                    // 捕获 load API 响应
                    if(u.indexOf('botion.com/load') >= 0) {
                        window.__hdt_load = self.responseText;
                    }
                    // 捕获 verify API 响应
                    if(u.indexOf('botion.com/verify') >= 0) {
                        window.__hdt_verify = self.responseText;
                    }
                });
                return _send.apply(this, arguments);
            };
            return 'hook_ok';
        })()
        ''')
        print(f"  Hook: {hook}")

        # 4. 填表 + 点登录
        print("\n[4] 填表 + 点登录...")
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
        print("  ✅ 已点登录")

        # 5. 等验证码弹窗 + 等 load 响应
        print("\n[5] 等待验证码弹窗...")
        browser_load_data = None
        popup_box = None

        for i in range(20):
            await asyncio.sleep(1)
            
            # 检查 load 响应是否已捕获
            load_raw = await js("window.__hdt_load || ''")
            if load_raw and len(load_raw) > 50 and not browser_load_data:
                # 解析 load 响应
                m = re.search(r'\((.*)\)$', load_raw, re.DOTALL)
                if m:
                    try:
                        ld = json.loads(m.group(1)).get('data', {})
                        browser_load_data = {
                            'lot_number': ld.get('lot_number', ''),
                            'payload': ld.get('payload', ''),
                            'process_token': ld.get('process_token', ''),
                            'pow_detail': ld.get('pow_detail', {}),
                            'pt': ld.get('pt', '1'),
                            'payload_protocol': ld.get('payload_protocol', '1'),
                        }
                        print(f"  📥 捕获到浏览器 load 数据!")
                        print(f"     lot_number: {browser_load_data['lot_number'][:20]}...")
                        print(f"     process_token: {browser_load_data['process_token'][:20]}...")
                    except Exception as e:
                        print(f"  ⚠️  解析load数据失败: {e}")
            
            # 检查弹窗
            box=await js('(function(){var e=document.querySelector("[class*=botion_click]");if(!e)return null;var r=e.getBoundingClientRect();return r.width>50?{x:r.x,y:r.y,w:r.width,h:r.height}:null;})()')
            if box and box.get('w',0) > 50:
                popup_box = box
                print(f"  弹窗出现: {box['w']:.0f}x{box['h']:.0f} ({i+1}s)")
                break

        if not popup_box:
            print("❌ 验证码未出现"); return
        if not browser_load_data:
            print("⚠️  未捕获到浏览器 load 数据")
            # 从已有的 fetch_captcha 接口获取
            from hdt.auth.captcha import fetch_captcha
            browser_load_data = fetch_captcha()
            print(f"  改用 Python fetch_captcha() 数据")
        else:
            print(f"  ✅ 使用浏览器 SDK 的 process_token")

        # 6. 提取验证码图片 + jfbym
        print("\n[6] 提取验证码 + jfbym...")
        img_json = await js('''
        JSON.stringify((function(){
            var r={bg:null,ques:[]};
            document.querySelectorAll('[class*="botion_bg"]').forEach(function(e){
                var bg=getComputedStyle(e).backgroundImage;
                var m=bg.match(/url\\(["']?([^"')\\s]+)["']?\\)/);
                if(m&&m[1]&&m[1].indexOf('captcha_v4')>=0)r.bg=m[1];
            });
            document.querySelectorAll('[class*="botion"] img').forEach(function(i){
                if(i.naturalWidth>=60&&i.src.indexOf('sprite')<0)r.ques.push(i.src);
            });
            return r;
        })())
        ''')
        if not img_json: print("❌ 提取图片失败"); return
        img_data = json.loads(img_json)
        if not img_data.get('bg') or len(img_data.get('ques',[])) < 3:
            print("❌ 图片不完整"); return
        print(f"  背景图: {img_data['bg'][:60]}...")

        if JFBYM_TOKEN:
            solver = JfbymSolver(api_token=JFBYM_TOKEN)
            challenge = CaptchaChallenge(
                lot_number='', payload='', process_token='',
                bg_url=img_data['bg'], ques_urls=img_data['ques'][:3],
                captcha_id=CAPTCHA_ID)
            sol = await solver.solve(challenge)
            jfbym_pts = sol.pts
            print(f"  🤖 jfbym: {jfbym_pts}")
        else:
            print("  ⏭️ 跳过 jfbym（无 JFBYM_TOKEN）"); return

        # 7. 用浏览器 process_token + 我们的 w 调 verify
        print("\n[7] 用浏览器 process_token + 我们的 generate_w 调 verify...")
        w = make_w(browser_load_data, jfbym_pts, random.randint(800, 3000))
        print(f"  w: {len(w)} hex, AES={(len(w)-256)//2}B")

        cb = f"botion_{int(time.time()*1000)}"
        params = {"callback": cb, "captcha_id": CAPTCHA_ID, "client_type": "web",
            "lot_number": browser_load_data["lot_number"],
            "payload": browser_load_data["payload"],
            "process_token": browser_load_data["process_token"],
            "payload_protocol": browser_load_data.get("payload_protocol", "1"),
            "pt": browser_load_data.get("pt", "1"), "w": w}
        url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)

        resp = cr.get(url, impersonate="chrome110",
                      headers={"Referer": f"{domain}/",
                               "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                      timeout=30)
        text = resp.text
        m = re.search(r'\((.*)\)$', text, re.DOTALL)
        if m:
            d = json.loads(m.group(1))
            r2 = d.get("data", {})
            icon = "✅" if r2.get("result") == "success" else "❌"
            print(f"  {icon} 结果: status={d.get('status')} result={r2.get('result')} "
                  f"fail_count={r2.get('fail_count')} score={r2.get('score')}")
        else:
            print(f"  ❌ 解析失败: {text[:100]}")

        # 8. 提示用户手动点验证码（查看浏览器 SDK 的结果）
        print(f"\n[8] 🖱️ 请在浏览器中手动点击验证码")
        print(f"  如果我们的 verify {'通过' if r2.get('result')=='success' else '失败'}，浏览器应该也会{'通过' if r2.get('result')=='success' else '失败'}")
        print(f"  等待浏览器 SDK 的结果（Ctrl+C 退出）...")

        for i in range(30):
            await asyncio.sleep(1)
            # 检查是否已登录
            url_v = await js("window.location.href")
            if url_v and 'user/login' not in url_v:
                print(f"  ✅ 浏览器登录成功!")
                # 获取 token
                ls = await js("localStorage.getItem('X-API-TOKEN') || ''")
                if ls: print(f"  🔑 Token: {ls[:40]}...")
                break
            if i % 5 == 4: print(f"  ...等待 ({i+1}s)")

if __name__ == '__main__':
    asyncio.run(main())
