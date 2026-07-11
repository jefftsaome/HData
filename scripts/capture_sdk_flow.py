#!/usr/bin/env python3
"""CDP Network 全量抓包 — 捕获 SDK 的 verify w参数 + captcha_output + validateGeeCheckV2 请求体。

用法:
    uv run python scripts/capture_sdk_flow.py

用户需要手动: 1) 退出登录 2) 填表 3) 点验证码 4) 等登录完成
"""
import asyncio, json, time, subprocess, re, aiohttp, websockets
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "sdk_flow_captured.json"
TARGET_KW = ['verify', 'validateGeeCheckV2', '/login', 'kaptchcate', 'botion']


def find_cdp_port():
    r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    for line in r.stdout.split('\n'):
        if 'BrowserAct' in line and 'remote-debugging-port' in line:
            m = re.search(r'remote-debugging-port=(\d+)', line)
            if m: return int(m.group(1))
    return 56926


async def main():
    port = find_cdp_port()
    print(f"CDP port: {port}")

    async with aiohttp.ClientSession() as s:
        r = await s.get(f'http://127.0.0.1:{port}/json/list')
        targets = await r.json()
        r2 = await s.get(f'http://127.0.0.1:{port}/json/version')
        ws_url = (await r2.json()).get('webSocketDebuggerUrl', '')

    # Find the best page target
    page_t = None
    for t in targets:
        if t['type'] == 'page' and any(d in t.get('url', '') for d in ('5ttn8v', 'login', 'vip')):
            page_t = t
            break
    if not page_t:
        page_t = next((t for t in targets if t['type'] == 'page'), None)
    if not page_t:
        print("No page target found"); return

    print(f"Page: {page_t['url'][:100]}")

    captured = {'verify_requests': [], 'verify_responses': [], 'validate_requests': [],
                'login_requests': [], 'login_responses': [],
                'kaptchcate_requests': [], 'kaptchcate_responses': []}

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        mid = 0; extra = {}

        async def cdp(method, params=None, sid=None):
            nonlocal mid; mid += 1
            m2 = {'id': mid, 'method': method, 'params': params or {}}
            if sid: m2['sessionId'] = sid
            await ws.send(json.dumps(m2)); return mid

        async def wait_resp(tid, timeout=5):
            dl = time.time() + timeout
            while time.time() < dl:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if msg.get('id') == tid: return msg
                if msg.get('method') == 'Target.attachedToTarget':
                    extra['sid'] = msg['params']['sessionId']
            return {}

        mid = await cdp('Target.attachToTarget', {'targetId': page_t['id'], 'flatten': True})
        resp = await wait_resp(mid)
        sid = resp.get('result', {}).get('sessionId') or extra.get('sid', '')
        print(f"Session: {sid[:20]}...")

        # Enable Network
        await cdp('Network.enable', sid=sid)

        # Navigate to login page if not already there
        if '/user/login' not in page_t.get('url', ''):
            async def epage(m, p=None):
                nonlocal mid; mid += 1
                await ws.send(json.dumps({'id': mid, 'method': m, 'params': p or {}, 'sessionId': sid}))
                return mid

            async def ewait(tid, timeout=5):
                dl = time.time() + timeout
                while time.time() < dl:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                    if msg.get('id') == tid:
                        return msg.get('result', {}).get('result', {}).get('value')
                return None

            domain = page_t['url'].split('/user')[0] if '/user' in page_t.get('url', '') else 'https://www.5ttn8v.vip:9037'
            await epage('Page.navigate', {'url': f'{domain}/user/login'})
            await asyncio.sleep(3)
            print(f"Navigated to login page")

        print(f"""
{'=' * 60}
  现在请手动操作:
  1. 如果已登录，先退出登录
  2. 输入账号 lidongsen1 密码 lds19830413
  3. 点击验证码
  4. 等登录完成
  监控中... (120 秒)
{'=' * 60}
""")

        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)
            method = msg.get('method', '')
            p = msg.get('params', {})

            if method == 'Network.requestWillBeSent':
                req = p.get('request', {})
                url = req.get('url', '')
                req_id = p.get('requestId', '')
                req_method = req.get('method', '')

                matched = None
                if 'verify' in url and 'botion' in url:
                    matched = 'verify'
                elif 'validateGeeCheckV2' in url:
                    matched = 'validateGeeCheckV2'
                elif '/user/login' in url and req_method == 'POST':
                    matched = 'login'
                elif 'kaptchcate' in url:
                    matched = 'kaptchcate'

                if matched:
                    print(f"\n>>> [{matched}] {req_method} {url[:150]}")
                    if req_method == 'POST':
                        rmid = await cdp('Network.getRequestPostData', {'requestId': req_id}, sid=sid)
                        rresp = await wait_resp(rmid, timeout=3)
                        pd = rresp.get('result', {}).get('postData', '')
                        print(f"    Body: {pd}")
                        key = f"{matched}_requests"
                        if key in captured:
                            captured[key].append({'url': url, 'method': req_method, 'body': pd})

            if method == 'Network.responseReceived':
                response = p.get('response', {})
                url = response.get('url', '')
                status = response.get('status', 0)
                req_id = p.get('requestId', '')

                matched = None
                if 'verify' in url and 'botion' in url:
                    matched = 'verify'
                elif 'validateGeeCheckV2' in url:
                    matched = 'validateGeeCheckV2'
                elif '/user/login' in url:
                    matched = 'login'
                elif 'kaptchcate' in url:
                    matched = 'kaptchcate'

                if matched:
                    rmid = await cdp('Network.getResponseBody', {'requestId': req_id}, sid=sid)
                    rresp = await wait_resp(rmid, timeout=3)
                    body = rresp.get('result', {}).get('body', '')
                    print(f"<<< [{matched}] HTTP {status}")
                    print(f"    Body: {body[:2000]}")
                    key = f"{matched}_responses"
                    if key in captured:
                        captured[key].append({'url': url, 'status': status, 'body': body})

    # Save
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(captured, indent=2, ensure_ascii=False))
    print(f"\nSaved to {OUTPUT}")
    print(f"Captured: verify_req={len(captured['verify_requests'])} verify_resp={len(captured['verify_responses'])}")
    print(f"          validate_req={len(captured['validate_requests'])}")
    print(f"          kaptchcate_req={len(captured['kaptchcate_requests'])}")
    print(f"          login_req={len(captured['login_requests'])} login_resp={len(captured['login_responses'])}")


if __name__ == '__main__':
    asyncio.run(main())
