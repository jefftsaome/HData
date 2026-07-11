#!/usr/bin/env python3
"""CDP 网络监控 — 捕获 kaptchcate / validateGeeCheckV2 / login 的 POST body。

连接到已在运行的 Chrome CDP（如 browser-act 启动的浏览器），
用户手动完成登录，脚本自动捕获关键请求的 body 和响应。

用法:
    uv run python scripts/capture_cdp_bodies.py --port 57073
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp
import websockets

_PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
OUTPUT = _PROJ_ROOT / "data" / "login_flow_captured.json"

# 要监控的 URL 关键词
KEYWORDS = ["kaptchcate", "validateGeeCheckV2", "/site/api/v1/user/login",
            "bcaptcha.botion.com/verify"]


async def main(port: int):
    # 1. 获取 WebSocket URL
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"http://127.0.0.1:{port}/json/version")
        ws_url = (await r.json()).get("webSocketDebuggerUrl", "")

    if not ws_url:
        print(f"❌ 端口 {port} 未找到 Chrome CDP")
        return 1

    print(f"✅ 已连接 CDP (port={port})")

    captured: dict = {"requests": [], "responses": {}}
    extra: dict = {}
    msg_id = 0

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:

        async def cmd(method, params=None, sid=None):
            nonlocal msg_id
            msg_id += 1
            m = {"id": msg_id, "method": method, "params": params or {}}
            if sid:
                m["sessionId"] = sid
            await ws.send(json.dumps(m))
            return msg_id

        async def read_resp(mid, timeout=5):
            dl = time.time() + timeout
            while time.time() < dl:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    return {}
                msg = json.loads(raw)
                if msg.get("id") == mid:
                    return msg
                if msg.get("method") == "Target.attachedToTarget":
                    extra["session_id"] = msg["params"]["sessionId"]
            return {}

        # 2. 找到所有 page targets 并启用 Network
        mid = await cmd("Target.getTargets")
        resp = await read_resp(mid)
        targets = resp.get("result", {}).get("targetInfos", [])

        page_sids = {}  # targetId -> sessionId
        for t in targets:
            if t.get("type") == "page":
                tid = t["targetId"]
                mid = await cmd("Target.attachToTarget",
                                {"targetId": tid, "flatten": True})
                resp = await read_resp(mid)
                sid = resp.get("result", {}).get("sessionId") or extra.get(
                    "session_id", "")
                if sid:
                    await cmd("Network.enable", sid=sid)
                    page_sids[tid] = sid
                    url = t.get("url", "")[:100]
                    print(f"  📡 监控页面: {url} (sid={sid[:20]}...)")

        if not page_sids:
            print("❌ 未找到任何 page target")
            return 1

        # 3. 监控网络请求
        print(f"""
{'='*60}
  请在浏览器中完成登录：
  1. 输入账号密码
  2. 完成 GeeTest 验证码
  3. 等待登录成功

  ⏳ 监控中... (最长 5 分钟)
  按 Ctrl+C 停止
{'='*60}
""")

        pending_post_data: dict[str, str] = {}  # requestId -> keyword

        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=300)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)
            method = msg.get("method", "")
            p = msg.get("params", {})
            sid = msg.get("sessionId", "")

            # ── 请求发送时 ──
            if method == "Network.requestWillBeSent":
                url = p.get("request", {}).get("url", "")
                req_id = p.get("requestId", "")
                req_method = p.get("request", {}).get("method", "")
                req_headers = p.get("request", {}).get("headers", {})

                for kw in KEYWORDS:
                    if kw in url:
                        print(f"\n🎯 [{kw}] {req_method} {url[:150]}")
                        pending_post_data[req_id] = kw

                        entry = {
                            "keyword": kw,
                            "method": req_method,
                            "url": url,
                            "headers": req_headers,
                            "request_id": req_id,
                        }

                        # 如果是 POST，异步获取 body
                        if req_method == "POST":
                            mid2 = await cmd(
                                "Network.getRequestPostData",
                                {"requestId": req_id}, sid=sid)
                            resp2 = await read_resp(mid2)
                            post_data = resp2.get("result", {}).get(
                                "postData", "")
                            entry["post_data"] = post_data
                            print(f"   📤 body: {post_data[:300]}")

                        captured["requests"].append(entry)
                        break

            # ── 响应接收时 ──
            if method == "Network.responseReceived":
                url = p.get("response", {}).get("url", "")
                req_id = p.get("requestId", "")
                status = p.get("response", {}).get("status", 0)

                for kw in KEYWORDS:
                    if kw in url and status:
                        print(f"   📥 [{kw}] 响应: HTTP {status}")
                        # 获取响应体
                        mid2 = await cmd(
                            "Network.getResponseBody",
                            {"requestId": req_id}, sid=sid)
                        resp2 = await read_resp(mid2)
                        body = resp2.get("result", {}).get("body", "")
                        captured["responses"][kw] = {
                            "status": status,
                            "body": body[:2000],  # 只保留前2000字符
                        }
                        print(f"   📥 body: {body[:300]}")
                        break

    # 4. 保存结果
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(captured, indent=2, ensure_ascii=False))
    print(f"\n💾 已保存到: {OUTPUT}")
    print(f"   捕获 {len(captured['requests'])} 个请求, "
          f"{len(captured['responses'])} 个响应")

    # 5. 显示关键发现
    print(f"\n{'='*60}")
    print("📋 捕获摘要:")
    print(f"{'='*60}")
    for req in captured["requests"]:
        kw = req["keyword"]
        has_body = bool(req.get("post_data"))
        print(f"  [{kw}] {req['method']} — body={has_body}")
    for kw, resp in captured["responses"].items():
        print(f"  [{kw}] HTTP {resp['status']}")

    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=57073)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.port)))
