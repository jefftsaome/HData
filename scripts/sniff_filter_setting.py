"""网络嗅探 — 确认"路纸筛选设置"走 WS / HTTP / 纯前端过滤。

原理:
  附加到你本机 Chrome (CDP 9222 端口)，对所有页面开启 Network 域，
  同时记录:
    - HTTP 请求（URL/method/postData）
    - WS 帧（发送/接收方向，并用协议解码器解出 protocolId）
  你在浏览器里点开"路纸筛选设置"并修改选项，之后对比时间窗内的流量即可定性。

用法:
  1. 先运行本脚本（它会提示等待）
  2. 在 Chrome 里操作：打开/修改路纸筛选设置 → 应用
  3. 回这里按 Enter，脚本导出 .cache/sniff_result.json 并打印分析
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

CDP_HTTP = "http://127.0.0.1:9222"
OUT = Path(".cache/sniff_result.json")

# 游戏相关域名关键字（过滤无关流量，留空=全记）
GAME_HINTS = ("ylsvq", "5qk8bt", "leyu", "wsproxy", "6pwn4i", ".vip:",
              "lisxdc", "egret")


async def main():
    import aiohttp
    import websockets
    from hdata.protocol.codec import decode_frame

    # 找游戏页面 target
    async with aiohttp.ClientSession() as http:
        async with http.get(f"{CDP_HTTP}/json") as r:
            targets = await r.json()
    pages = [t for t in targets if t.get("type") in ("page", "iframe")]
    game = None
    for t in pages:
        url = t.get("url", "")
        if "egret" in url or "hall" in url:   # 游戏大厅 iframe 优先
            game = t
            break
    if not game:
        for t in pages:
            url = t.get("url", "")
            if any(h in url for h in GAME_HINTS):
                game = t
                break
    if not game:
        print("未找到游戏页面。当前页面:")
        for t in pages:
            print("  ", t.get("url", "")[:100])
        return
    print(f"盯上页面: {game['url'][:80]}")

    events: list[dict] = []
    ws_req_ids: set[str] = set()

    async with websockets.connect(game["webSocketDebuggerUrl"],
                                  max_size=50 * 1024 * 1024) as cdp:
        mid = 0

        async def send_cmd(method, params=None):
            nonlocal mid
            mid += 1
            await cdp.send(json.dumps(
                {"id": mid, "method": method, "params": params or {}}))

        await send_cmd("Network.enable")
        await send_cmd("Page.enable")

        # 嗅探时长（秒），命令行第 1 个参数可覆盖，默认 240
        duration = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
        print(f"\n>>> 请在 {duration:.0f} 秒内完成以下操作 <<<")
        print(">>> 1. 登录并进入游戏大厅")
        print(">>> 2. 打开【路纸筛选设置】，修改选项并应用")
        print(">>> 3. 再改一次选项（便于对比两次设置的流量差异）\n")

        deadline = time.time() + duration
        enter_task = None

        def decode_ws_payload(payload: str, is_base64: bool) -> dict:
            """尝试用协议解码器解 WS 帧，返回 {pid, summary}。"""
            raw: bytes = b""
            if is_base64:
                try:
                    raw = base64.b64decode(payload)
                except Exception:
                    raw = b""
            if not raw:
                # CDP 对二进制帧有时直接给 str（latin-1 语义），尝试还原
                try:
                    raw = payload.encode("latin-1")
                except Exception:
                    raw = payload.encode("utf-8", "replace")
            frame = decode_frame(raw)
            if not frame:
                return {"pid": None,
                        "note": f"undecoded {len(raw)}B",
                        "hex": raw[:60].hex(),
                        "repr": repr(payload[:60])}
            info = {}
            try:
                from hdata.protocol.codec import extract_param
                p = extract_param(frame) or {}
                data = p.get("param") or p.get("data")
                if isinstance(data, str):
                    data = json.loads(data)
                if isinstance(data, dict):
                    info = {k: data[k] for k in list(data)[:8]}
            except Exception:
                pass
            return {"pid": frame.get("protocolId"), "keys": info}

        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(cdp.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            method = msg.get("method", "")
            params = msg.get("params", {})
            ts = time.time()

            if method == "Network.webSocketCreated":
                ws_req_ids.add(params.get("requestId", ""))
                events.append({"t": ts, "kind": "ws_open",
                               "url": params.get("url", "")})
            elif method == "Network.webSocketFrameSent":
                rid = params.get("requestId", "")
                ws_req_ids.add(rid)  # 老连接也收（enable 前已建的 WS 没有 Created 事件）
                resp = params.get("response", {})
                payload = resp.get("payloadData", "")
                dec = decode_ws_payload(payload, True)
                events.append({"t": ts, "kind": "ws_send",
                               "pid": dec.get("pid"),
                               "detail": dec})
            elif method == "Network.webSocketFrameReceived":
                rid = params.get("requestId", "")
                ws_req_ids.add(rid)
                resp = params.get("response", {})
                payload = resp.get("payloadData", "")
                dec = decode_ws_payload(payload, True)
                events.append({"t": ts, "kind": "ws_recv",
                               "pid": dec.get("pid")})
            elif method == "Network.requestWillBeSent":
                req = params.get("request", {})
                url = req.get("url", "")
                if not any(h in url for h in GAME_HINTS):
                    continue
                if req.get("method") == "OPTIONS":
                    continue
                events.append({"t": ts, "kind": "http",
                               "method": req.get("method"),
                               "url": url[:150],
                               "post": (req.get("postData") or "")[:300]})

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(events, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    # ── 分析 ──
    print(f"\n共捕获 {len(events)} 条事件，已存 {OUT}")
    http_reqs = [e for e in events if e["kind"] == "http"]
    ws_sends = [e for e in events if e["kind"] == "ws_send"]
    print(f"HTTP 请求: {len(http_reqs)} 条")
    for e in http_reqs[-15:]:
        print(f"  {e['method']} {e['url']}")
        if e.get("post"):
            print(f"    post: {e['post'][:120]}")
    print(f"WS 发送帧: {len(ws_sends)} 条")
    pid_count: dict = {}
    for e in ws_sends:
        pid_count[e["pid"]] = pid_count.get(e["pid"], 0) + 1
    for pid, n in sorted(pid_count.items(), key=lambda x: str(x[0])):
        print(f"  pid={pid}: {n} 次")
    # 打印非心跳的 WS 发送帧详情
    print("\nWS 发送帧明细（非10089）:")
    for e in ws_sends:
        if e["pid"] not in (10089, 1):
            d = e.get("detail", {})
            print(f"  pid={e['pid']} {json.dumps({k: v for k, v in d.items() if k != 'keys'}, ensure_ascii=False)[:160]}")
            keys = d.get("keys")
            if keys:
                print(f"    keys={json.dumps(keys, ensure_ascii=False)[:200]}")


asyncio.run(main())
