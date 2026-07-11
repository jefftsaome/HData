#!/usr/bin/env python3
"""用 CDP Network 捕获 iframe 内 GeeTest SDK 的 load API 响应，
拿到 process_token，再用我们的 generate_w + jfbym 坐标调 verify。

用法:
    JFBYM_TOKEN=xxx uv run python scripts/test_cdp_token.py 9222
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
from hdata.auth.captcha_solver import JfbymSolver, CaptchaChallenge
from hdata.auth.geetest_signer import LotParser, _generate_pow, _rand_uid

CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "lds19830413")
RSA_N = int("00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81", 16)
RSA_E = int("10001", 16)

def make_w(ld, pts, passtime):
    ln=ld["lot_number"]; pd=ld["pow_detail"]; lp=LotParser()
    eo={**_generate_pow(ln,CAPTCHA_ID,pd['hashfunc'],pd['version'],pd['bits'],pd['datetime']),
        **lp.get_dict(ln),'biht':'1426265548','em':{},
        'gee_guard':{'auh':'3','aup':'3','cdc':'3','egp':'3','res':'3','rew':'3','sep':'3','snh':'3'},
        'geetest':'captcha','lang':'zh','lot_number':ln,
        'userresponse':pts,'passtime':passtime}
    rk=_rand_uid()
    ej=json.dumps(eo,separators=(',',':'))
    c=AES_C.new(rk.encode(),AES_C.MODE_CBC,b'0000000000000000')
    ee=c.encrypt(pad(ej.encode(),AES_C.block_size))
    rc=PKCS1_v1_5.new(construct((RSA_N,RSA_E))); ek=rc.encrypt(rk.encode())
    return binascii.hexlify(ee).decode()+binascii.hexlify(ek).decode()

async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    
    async with aiohttp.ClientSession() as s:
        r=await s.get(f'http://127.0.0.1:{port}/json/list')
        targets=await r.json()
        r2=await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url=(await r2.json()).get('webSocketDebuggerUrl','')
    if not ws_url: print("❌ CDP 不可用"); return
    page_t=next((t for t in targets if t['type']=='page'),None)
    if not page_t: print("❌ 无页面"); return
    
    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        mid=0; sid=''; msg_queue=asyncio.Queue()
        
        async def cdp_send(m,p=None):
            nonlocal mid; mid+=1
            msg={'id':mid,'method':m,'params':p or {}}
            if sid: msg['sessionId']=sid
            await ws.send(json.dumps(msg)); return mid
        
        async def reader():
            """持续读 ws，所有消息入队列"""
            while True:
                try:
                    raw=await ws.recv()
                    msg=json.loads(raw)
                    if msg.get('method')=='Target.attachedToTarget':
                        nonlocal sid; sid=msg['params']['sessionId']
                    await msg_queue.put(msg)
                except: break
        
        # 启动后台 reader
        asyncio.create_task(reader())
        
        async def wait_msg(timeout=10, msg_filter=None):
            """从队列取消息，可选过滤。"""
            dl=time.time()+timeout
            while time.time()<dl:
                try:
                    msg=await asyncio.wait_for(msg_queue.get(), timeout=1)
                    if msg_filter is None or msg_filter(msg):
                        return msg
                except: continue
            return None
        
        async def js(expr, timeout=10):
            mid=await cdp_send('Runtime.evaluate',{'expression':expr,'returnByValue':True})
            while True:
                msg=await wait_msg(timeout=timeout, msg_filter=lambda m: m.get('id')==mid)
                if msg: return msg.get('result',{}).get('result',{}).get('value')
                return None
        
        async def cdp_cmd(cmd, params=None, timeout=10):
            """发送 CDP 命令，等带 id 的响应。"""
            mid=await cdp_send(cmd, params)
            while True:
                msg=await wait_msg(timeout=timeout, msg_filter=lambda m: m.get('id')==mid)
                if msg: return msg
                return None
        
        await cdp_send('Target.attachToTarget',{'targetId':page_t['id'],'flatten':True})
        await asyncio.sleep(1)  # 等 attach 完成
        if not sid: print("❌ attach失败"); return
        print("✅ 已连接 Chrome")
        
        # 1. 域名 + 打开登录页
        from hdata.auth.domain import resolve_domain
        domain=resolve_domain()
        print(f"域名: {domain}")
        await cdp_send('Page.navigate',{'url':f'{domain}/user/login'})
        for i in range(20):
            await asyncio.sleep(1)
            rdy=await js('document.readyState')
            if rdy=='complete': print(f"  页面加载 ({i+1}s)"); break
        
        # 2. 安装 XHR hook + 启用 Network
        await js('''
        (function(){
            window.__hdt={load_resp:'',verify_resp:'',verify_url:''};
            var _open=XMLHttpRequest.prototype.open, _send=XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open=function(m,u){this._url=u;return _open.apply(this,arguments);};
            XMLHttpRequest.prototype.send=function(b){
                var u=this._url||'',self=this;
                this.addEventListener('load',function(){
                    if(u.indexOf('botion.com/load')>=0) window.__hdt.load_resp=self.responseText;
                    if(u.indexOf('botion.com/verify')>=0){window.__hdt.verify_resp=self.responseText;window.__hdt.verify_url=u;}
                });
                return _send.apply(this,arguments);
            };
        })()
        ''')
        # 启用 Network
        await cdp_send('Network.enable')
        
        # 3. 填表 + 点登录
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
        print("✅ 已点登录")
        
        # 4. 等待弹窗，同时收 Network 事件
        print("等待验证码弹窗 + Network 事件...")
        browser_load = None
        load_request_id = None
        
        for i in range(20):
            await asyncio.sleep(1)
            
            # 从队列处理 Network 事件（不阻塞）
            while not msg_queue.empty():
                try: msg = msg_queue.get_nowait()
                except: break
                
                # 捕获 Network 请求
                if msg.get('method')=='Network.requestWillBeSent':
                    req=msg.get('params',{}).get('request',{})
                    url=req.get('url','')
                    if 'botion.com/load' in url:
                        load_request_id = msg.get('params',{}).get('requestId','')
                        print(f"  📡 发现 load 请求: {load_request_id}")
                
                # 捕获响应
                elif msg.get('method')=='Network.responseReceived':
                    resp=msg.get('params',{}).get('response',{})
                    url=resp.get('url','')
                    if 'botion.com/load' in url:
                        rid=msg.get('params',{}).get('requestId','')
                        # 请求响应体
                        try:
                            body_resp=await cdp_cmd('Network.getResponseBody',{'requestId':rid}, timeout=5)
                            if body_resp:
                                body=body_resp.get('result',{}).get('body','')
                                m=re.search(r'\((.*)\)$', body, re.DOTALL)
                                if m:
                                    ld=json.loads(m.group(1)).get('data',{})
                                    browser_load={
                                        'lot_number':ld.get('lot_number',''),
                                        'payload':ld.get('payload',''),
                                        'process_token':ld.get('process_token',''),
                                        'pow_detail':ld.get('pow_detail',{}),
                                        'pt':ld.get('pt','1'),
                                        'payload_protocol':ld.get('payload_protocol','1'),
                                    }
                                    print(f"  📥 捕获到 load 数据!")
                                    print(f"     process_token: {browser_load['process_token'][:20]}...")
                        except Exception as e:
                            print(f"  ⚠️  读取响应体失败: {e}")
            
            # 检查弹窗
            box=await js('(function(){var e=document.querySelector("[class*=botion_click]");if(!e)return null;var r=e.getBoundingClientRect();return r.width>50;})()')
            if box: print(f"  弹窗出现 ({i+1}s)"); break
        
        # 如果 Network 没捕获到，从页面 hook 取
        if not browser_load:
            load_raw = await js("window.__hdt.load_resp || ''")
            if load_raw and len(load_raw)>50:
                m=re.search(r'\((.*)\)$', load_raw, re.DOTALL)
                if m:
                    ld=json.loads(m.group(1)).get('data',{})
                    browser_load={
                        'lot_number':ld.get('lot_number',''),
                        'payload':ld.get('payload',''),
                        'process_token':ld.get('process_token',''),
                        'pow_detail':ld.get('pow_detail',{}),
                        'pt':ld.get('pt','1'),
                        'payload_protocol':ld.get('payload_protocol','1'),
                    }
                    print(f"  从 XHR hook 获取 load 数据 ✅")
        
        # 如果还是没拿到，说明 iframe 里发的不走主页 XHR
        if not browser_load:
            print("❌ 无法获取浏览器 load 数据（SDK 在 iframe 里）")
            print("尝试从 __BOTION__ 读取...")
            bot=await js("JSON.stringify({ln:window.__BOTION__?.lotNumber||'',pt:window.__BOTION__?.processToken||'',pl:window.__BOTION__?.payload||''})")
            print(f"  __BOTION__: {bot}")
            
            # 如果 iframe 跨域，用 CDP 切到 iframe 的 target
            iframes=await js("document.querySelectorAll('iframe').length")
            print(f"  iframe 数量: {iframes}")
            
            # 从页面 URL params 提取（如果 iframe 加载了 verify URL）
            page_url=await js("window.location.href")
            print(f"  当前 URL: {page_url}")
            return
        
        # 5. 提取验证码 + jfbym
        print("\n提取验证码 + jfbym...")
        img_json=await js('''
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
        if not img_json: print("❌ 提取失败"); return
        img_data=json.loads(img_json)
        if JFBYM_TOKEN:
            solver=JfbymSolver(api_token=JFBYM_TOKEN)
            challenge=CaptchaChallenge(lot_number='',payload='',process_token='',
                bg_url=img_data['bg'],ques_urls=img_data['ques'][:3],captcha_id=CAPTCHA_ID)
            sol=await solver.solve(challenge)
            jfbym_pts=sol.pts
            print(f"jfbym: {jfbym_pts}")
        else: return
        
        # 6. 用浏览器 process_token + 我们的 w 调 verify
        print(f"\n用浏览器 process_token 调 verify...")
        print(f"  process_token: {browser_load['process_token'][:30]}...")
        w=make_w(browser_load, jfbym_pts, random.randint(800,3000))
        
        cb=f"botion_{int(time.time()*1000)}"
        params={"callback":cb,"captcha_id":CAPTCHA_ID,"client_type":"web",
            "lot_number":browser_load["lot_number"],"payload":browser_load["payload"],
            "process_token":browser_load["process_token"],
            "payload_protocol":browser_load.get("payload_protocol","1"),
            "pt":browser_load.get("pt","1"),"w":w}
        url="https://bcaptcha.botion.com/verify?"+urllib.parse.urlencode(params)
        resp=cr.get(url,impersonate="chrome110",
            headers={"Referer":f"{domain}/","User-Agent":"Mozilla/5.0"},timeout=30)
        text=resp.text
        m=re.search(r'\((.*)\)$',text,re.DOTALL)
        if m:
            d=json.loads(m.group(1)); r2=d.get('data',{})
            icon="✅" if r2.get('result')=='success' else "❌"
            print(f"  {icon} verify: status={d.get('status')} result={r2.get('result')} "
                  f"fail_count={r2.get('fail_count')} score={r2.get('score')}")
        
        # 7. 你也可以手动点验证码看看浏览器端结果
        print(f"\n手动点验证码确认对比（Ctrl+C 退出）...")
        for i in range(30):
            await asyncio.sleep(1)
            url_v=await js("window.location.href")
            if url_v and 'user/login' not in url_v:
                ls=await js("localStorage.getItem('X-API-TOKEN')||''")
                print(f"浏览器登录: {'✅' if ls else '❌'} token={ls[:40] if ls else ''}...")
                break

if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n退出")
