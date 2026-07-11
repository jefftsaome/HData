#!/usr/bin/env python3
"""简化版：连 Chrome → 开登录页 → 自动填表 → 等你手动点验证码 → 捕获 verify URL。

用法:
    1. 启动 Chrome: /Applications/Google\\ Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222
    2. 运行: uv run python scripts/capture_verify_url.py
    3. 在浏览器中手动完成验证码点击
    4. 脚本自动捕获 w 参数

注意: 不再使用后台监听（避免 ConcurrencyError），而是统一的消息路由器。
"""

import asyncio, json, os, re, signal, sys, time, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp, websockets

JFBYM_TOKEN = os.getenv("JFBYM_TOKEN", "")
LEYU_USER = os.getenv("LEYU_USER", "lidongsen1")
LEYU_PWD = os.getenv("LEYU_PWD", "we4578")
CDP_PORT = int(os.getenv("LEYU_CDP_PORT", "9222"))
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_shutdown = False
def _setup_signal():
    def handler(sig, frame):
        global _shutdown
        if _shutdown: sys.exit(1)
        _shutdown = True
        print("\n\n⏳ 退出中...")
    signal.signal(signal.SIGINT, handler)
_setup_signal()


class CDPConnection:
    """CDP 连接 + 消息路由器。"""
    
    def __init__(self, ws):
        self._ws = ws
        self._mid = 0
        self._pending = {}  # mid → future
        self._events = []   # 累积的事件
    
    async def send(self, method, params=None, session_id=None):
        """发送 CDP 命令，返回响应。"""
        self._mid += 1
        msg = {"id": self._mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        await self._ws.send(json.dumps(msg))
        return await self._wait_response(self._mid)
    
    async def _wait_response(self, mid, timeout=10):
        """等待特定 id 的响应（同时收集中间事件）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _shutdown:
                raise asyncio.CancelledError()
            raw = await asyncio.wait_for(self._ws.recv(), timeout=1)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                # 也返回期间收集的事件
                events = self._events[:]
                self._events.clear()
                return msg, events
            elif msg.get("method"):
                self._events.append(msg)
    
    def get_events(self):
        """获取累积的事件。"""
        e = self._events[:]
        self._events.clear()
        return e
    
    async def eval(self, expr, session_id=None, timeout=5):
        """执行 JS 并返回值。"""
        resp, events = await self.send("Runtime.evaluate",
            {"expression": expr, "returnByValue": True}, session_id)
        result = resp.get("result", {}).get("result", {})
        return result.get("value"), events


async def main():
    print("=" * 60)
    print("  捕获 verify URL / w 参数")
    print("=" * 60)
    print(f"\nCDP port: {CDP_PORT}, 账号: {LEYU_USER}")
    
    # 连接 CDP
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5)
        targets = await r.json()
        r2 = await s.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5)
        ws_url = (await r2.json()).get("webSocketDebuggerUrl", "")
    if not ws_url:
        print("❌ Chrome 没启动 --remote-debugging-port=9222")
        return 1
    
    print("✅ 已连接 Chrome CDP")
    
    async with websockets.connect(ws_url, max_size=10*10**6) as ws:
        cdp = CDPConnection(ws)
        
        # 找或创建页面
        page_t = next((t for t in targets if t.get("type") == "page"), None)
        if not page_t:
            resp, _ = await cdp.send("Target.createTarget", {"url": "about:blank"})
            tid = resp.get("result", {}).get("targetId", "")
            page_t = {"id": tid}
        
        # Attach
        resp, events = await cdp.send("Target.attachToTarget",
            {"targetId": page_t["id"], "flatten": True})
        sid = ""
        for evt in events:
            if evt.get("method") == "Target.attachedToTarget":
                sid = evt["params"]["sessionId"]
                break
        if not sid:
            # 有时 events 在响应里
            sid = resp.get("sessionId", "")
        if not sid:
            print("❌ attachToTarget 失败")
            return 1
        print(f"✅ 已附加到页面")
        
        async def page_send(method, params=None):
            return await cdp.send(method, params, sid)
        
        async def page_eval(expr, timeout=5):
            return await cdp.eval(expr, sid, timeout)
        
        # Enable Network
        await page_send("Network.enable")
        print("✅ Network 监控已启用")
        
        # ===== 1. 获取真实域名 =====
        print("\n[1] 访问 leyu.me 获取真实域名...")
        await page_send("Page.navigate", {"url": "https://leyu.me"})
        await asyncio.sleep(4)
        url_val, _ = await page_eval("window.location.href")
        print(f"  重定向到: {url_val}")
        
        m = re.match(r"(https://[^/]+)", url_val or "")
        if not m: print("❌ 无法获取域名"); return 1
        domain = m.group(1)
        print(f"  真实域名: {domain}")
        
        # ===== 2. 清除旧 session，强制触发登录 =====  
        print(f"\n[2] 清除旧 session...")
        await page_eval("localStorage.clear(); sessionStorage.clear();")
        # 刷新 cookies
        await page_send("Network.clearBrowserCookies")
        print("  已清除 localStorage + cookies")
        
        # ===== 3. 导航到登录页 =====
        print(f"\n[3] 导航到 {domain}/user/login ...")
        await page_send("Page.navigate", {"url": f"{domain}/user/login"})
        await asyncio.sleep(3)
        
        # ===== 4. 自动填表 =====
        print(f"\n[4] 自动填写登录表单...")
        escaped_user = LEYU_USER.replace("\\", "\\\\").replace("'", "\\'")
        escaped_pwd = LEYU_PWD.replace("\\", "\\\\").replace("'", "\\'")
        
        fill_js = f"""
        (function() {{
            var inputs = document.querySelectorAll('input');
            var ui = null, pi = null;
            for (var inp of inputs) {{
                if (inp.type === 'password') pi = inp;
                else if (!ui) ui = inp;
            }}
            if (!ui || !pi) return 'no_inputs: ' + inputs.length + ' inputs found, types: ' + Array.from(inputs).map(i=>i.type).join(',');
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(ui, '{escaped_user}');
            ui.dispatchEvent(new Event('input', {{bubbles:true}}));
            ui.dispatchEvent(new Event('change', {{bubbles:true}}));
            nativeInputValueSetter.call(pi, '{escaped_pwd}');
            pi.dispatchEvent(new Event('input', {{bubbles:true}}));
            pi.dispatchEvent(new Event('change', {{bubbles:true}}));
            return 'filled';
        }})()
        """
        result, _ = await page_eval(fill_js)
        print(f"  填表结果: {result}")
        
        if result and result.startswith("no_inputs"):
            # 尝试别的选择器
            print("  尝试备用方法...")
            result2, _ = await page_eval(f"""
            (function() {{
                var all = document.querySelectorAll('input, [contenteditable], textarea');
                return 'found ' + all.length + ' elements';
            }})()
            """)
            print(f"  备用: {result2}")
        
        # ===== 5. 等待用户手动操作 =====
        print(f"\n[5] 请在浏览器中:")
        print(f"   1. 检查账号密码是否已填写")
        print(f"   2. 点击「登录」按钮")
        print(f"   3. 在验证码弹窗中手动点击正确的文字")
        print(f"   4. 等待验证码通过（脚本会自动捕获 w）")
        print(f"\n   ⏳ 等待中（Ctrl+C 退出）...")
        
        captured_w = ""
        captured_url = ""
        
        for i in range(120):  # 最长等 2 分钟
            if _shutdown: break
            await asyncio.sleep(1)
            
            # 检查是否有新的 network 事件
            events = cdp.get_events()
            for evt in events:
                if evt.get("method") == "Network.requestWillBeSent":
                    req = evt.get("params", {}).get("request", {})
                    url = req.get("url", "")
                    if "botion.com/verify" in url and "w=" in url:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        w_val = qs.get("w", [""])[0]
                        if w_val and not captured_w:
                            captured_w = w_val
                            captured_url = url
                            print(f"\n✅✅✅ 捕获到 verify 请求!")
                            print(f"   w 长度: {len(w_val)} hex chars")
                            break
            
            if captured_w:
                break
            
            # 也检查是否已登录（验证码通过后页面会变化）
            if i % 5 == 0:
                url_v, _ = await page_eval("window.location.href")
                if url_v and "user/login" not in url_v:
                    print(f"\n  页面已跳转，可能已登录: {url_v}")
                    # 试试从 localStorage 提取 token
                    ls, _ = await page_eval(
                        "JSON.stringify({t: localStorage.getItem('X-API-TOKEN') || ''})")
                    if ls:
                        ls_data = json.loads(ls)
                        if ls_data.get("t"):
                            print(f"  已获取 token: {ls_data['t'][:40]}...")
                            break
            
            if i % 10 == 9:
                print(f"   ...等待中 ({i+1}s)")
        
        # 保存结果
        if captured_w:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            txt_path = DATA_DIR / "real_w_captured.txt"
            txt_path.write_text(captured_w)
            json_path = DATA_DIR / "real_w_captured.json"
            json_path.write_text(json.dumps({
                "timestamp": int(time.time()),
                "w": captured_w,
                "w_len": len(captured_w),
                "verify_url": captured_url,
            }, indent=2))
            print(f"\n✅ w 已保存到:")
            print(f"   {txt_path}")
            print(f"   {json_path}")
            print(f"\n   w 前40: {captured_w[:40]}...")
            print(f"   w 后40: ...{captured_w[-40:]}")
            return 0
        else:
            print(f"\n❌ 未捕获到 w 参数")
            return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n⏹ 已退出")
        sys.exit(1)
