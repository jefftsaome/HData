#!/usr/bin/env python3
"""一次性抓取登录流程中所有 POST body 和响应体。

运行后，在浏览器中完成一次完整登录，脚本自动捕获：
  - kaptchcate 请求体和响应
  - validateGeeCheckV2 请求体和响应
  - login 请求体和响应
  - 登录后的 X-API-TOKEN / uuidToBase64 等

用法:
    uv run python scripts/capture_login_flow.py
"""

import asyncio, json, time, sys
from pathlib import Path
import aiohttp, websockets

_PROJ_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = _PROJ_ROOT / "data" / "login_flow_captured.json"


async def main():
    async with aiohttp.ClientSession() as s:
        r = await s.get("http://127.0.0.1:9222/json/version")
        ws_url = (await r.json()).get("webSocketDebuggerUrl", "")

    if not ws_url:
        print("❌ Chrome 未在 9222 端口运行")
        return 1

    captured: dict = {"requests": {}, "responses": {}}

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        msg_id = 0
        extra: dict = {}

        async def cmd(method, params=None, sid=None):
            nonlocal msg_id
            msg_id += 1
            m = {"id": msg_id, "method": method, "params": params or {}}
            if sid: m["sessionId"] = sid
            await ws.send(json.dumps(m))
            return msg_id

        async def read_resp(mid, timeout=3):
            dl = time.time() + timeout
            while time.time() < dl:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    return {}
                msg = json.loads(raw)
                if msg.get("id") == mid: return msg
                if msg.get("method") == "Target.attachedToTarget":
                    extra["session_id"] = msg["params"]["sessionId"]
            return {}

        # 找到所有 page targets
        mid = await cmd("Target.getTargets")
        resp = await read_resp(mid)
        targets = resp.get("result", {}).get("targetInfos", [])
        for t in targets:
            if t.get("type") in ("page", "iframe") and any(
                d in t.get("url", "") for d in ("94d9qm", "nhfspi", "rzhsir")
            ):
                tid = t["targetId"]
                mid = await cmd("Target.attachToTarget",
                                {"targetId": tid, "flatten": True})
                resp = await read_resp(mid)
                sid = resp.get("result", {}).get("sessionId") or extra.get(
                    "session_id", ""
                )
                if sid:
                    await cmd("Network.enable", sid=sid)
                    print(f"✅ 已监控: {t['url'][:100]}")

        print(f"""
{'='*60}
  现在请在浏览器中完成一次登录：
  1. 输入账号密码
  2. 完成 GeeTest 验证码
  3. 登录成功后等待页面加载

  监控中... (最长 180 秒)
{'='*60}
""")

        # 监控所有网络请求，捕获关键请求的 body
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)
            method = msg.get("method", "")
            p = msg.get("params", {})
            sid = msg.get("sessionId", "")

            if method == "Network.requestWillBeSent":
                url = p.get("request", {}).get("url", "")
                req_id = p.get("requestId", "")
                req_method = p.get("request", {}).get("method", "")

                # 关注关键请求
                for keyword in ["kaptchcate", "validateGeeCheckV2", "/login"]:
                    if keyword in url and req_method == "POST":
                        print(f"\n🎯 {keyword}: requestId={req_id}")

                        # 获取 POST body
                        mid2 = await cmd(
                            "Network.getRequestPostData",
                            {"requestId": req_id},
                            sid=sid,
                        )
                        resp2 = await read_resp(mid2)
                        post_data = resp2.get("result", {}).get("postData", "")
                        captured["requests"][keyword] = {
                            "url": url,
                            "headers": p.get("request", {}).get("headers", {}),
                            "body": post_data,
                        }
                        print(f"   body: {post_data[:500]}")

                        # 标记：如果是 login，获取响应体
                        if "login" in keyword:
                            captured["_login_request_id"] = req_id
                            captured["_login_sid"] = sid

            # 捕获响应体
            if method == "Network.loadingFinished":
                req_id = p.get("requestId", "")
                if req_id == captured.get("_login_request_id"):
                    mid2 = await cmd(
                        "Network.getResponseBody",
                        {"requestId": req_id},
                        sid=sid,
                    )
                    resp2 = await read_resp(mid2)
                    body = resp2.get("result", {}).get("body", "")
                    captured["responses"]["login"] = body
                    print(f"\n📥 login 响应体: {body[:500]}")

        # 提取登录后的 localStorage
        print("\n📡 提取 session 数据...")
        for t in targets:
            if t.get("type") in ("page", "iframe") and any(
                d in t.get("url", "") for d in ("94d9qm", "nhfspi", "rzhsir")
            ):
                tid = t["targetId"]
                mid = await cmd("Target.attachToTarget",
                                {"targetId": tid, "flatten": True})
                resp = await read_resp(mid)
                sid = resp.get("result", {}).get("sessionId") or extra.get(
                    "session_id", ""
                )
                if sid:
                    mid = await cmd("Runtime.evaluate", {
                        "expression": "JSON.stringify({token: localStorage.getItem('X-API-TOKEN')||'', uuid: localStorage.getItem('_uuid')||'', uuidToBase64: localStorage.getItem('uuidToBase64')||''})",
                        "returnByValue": True,
                    }, sid=sid)
                    resp = await read_resp(mid)
                    val = resp.get("result", {}).get("result", {}).get("value", "{}")
                    ls = json.loads(val) if isinstance(val, str) else val
                    if ls.get("token"):
                        captured["session"] = ls
                        print(f"✅ 提取到 session: token={ls['token'][:40]}...")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(captured, indent=2, ensure_ascii=False))
    print(f"\n💾 已保存: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
