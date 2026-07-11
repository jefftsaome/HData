"""从 Chrome 提取乐鱼认证信息并保存到 .auth_cache.json

用法:
    # Chrome 需在 9222 端口运行且已登录乐鱼
    uv run python scripts/extract_token.py

    # 指定其他 CDP 端口
    CDP_PORT=9333 uv run python scripts/extract_token.py

输出:
    .auth_cache.json — 包含 token/playerId/backendDomain/deviceId/ws_url
    可用于 WS 源直连。
"""

import asyncio
import json
import os
import sys
from pathlib import Path

CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
AUTH_CACHE_PATH = Path(__file__).parent.parent / ".auth_cache.json"


async def _resolve_ws_url(port: int) -> str:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/json/version",
                             timeout=aiohttp.ClientTimeout(total=3)) as resp:
                data = await resp.json()
                url = data.get("webSocketDebuggerUrl", "")
                if url:
                    return url
    except Exception:
        pass
    return f"ws://127.0.0.1:{port}/devtools/browser"


async def main():
    # 检查端口
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", CDP_PORT), timeout=2,
        )
        writer.close()
        await writer.wait_closed()
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
        print(f"❌ 端口 {CDP_PORT} 不可连。请确认 Chrome 已启动:")
        print(f'   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\')
        print(f"     --remote-debugging-port={CDP_PORT} \\")
        print(f"     --user-data-dir=/tmp/chrome_debug")
        sys.exit(1)

    from hdata.capture.cdp_bridge import CDPSession

    ws_url = await _resolve_ws_url(CDP_PORT)
    cdp = CDPSession(ws_url)
    ok = await cdp.connect()
    if not ok:
        print(f"❌ CDP 连接失败: {ws_url}")
        sys.exit(1)

    print(f"✅ CDP 已连接")

    # 从 localStorage 提取认证数据
    js = """
    (() => {
        var result = {};
        result.token = localStorage.getItem('token') || window.token || window.jwtToken || '';
        result.backendDomain = localStorage.getItem('KEY_TARGET_API_DOMAIN') || localStorage.getItem('backendDomainUrl') || '';
        result.deviceId = localStorage.getItem('fixedDeviceId') || '';
        return JSON.stringify(result);
    })()
    """
    r = await cdp.evaluate(js)
    raw = r.get("value", "{}") if isinstance(r, dict) else "{}"
    data = json.loads(raw) if isinstance(raw, str) else raw

    token = data.get("token", "")
    backend_domain = data.get("backendDomain", "")
    device_id = data.get("deviceId", "")

    if not token:
        print("❌ 未找到 token。请确认 Chrome 已登录乐鱼。")
        await cdp.disconnect()
        sys.exit(1)

    # 从 JWT 中解码 player_id
    player_id = 0
    try:
        import base64
        parts = token.split(".")
        if len(parts) == 3:
            payload = parts[1]
            payload += "=" * (4 - len(payload) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload))
            sub = decoded.get("sub", "{}")
            if isinstance(sub, str):
                sub = json.loads(sub)
            player_id = sub.get("playerId", 0)
    except Exception:
        pass

    # 从 backendDomain 提取 host（可能带端口）
    ws_host = backend_domain.split(":")[0] if ":" in backend_domain else backend_domain

    ws_url = (
        f"wss://wsproxy.{ws_host}:18026/"
        f"?playerId={player_id}"
        f"&jwtToken={token}"
        f"&deviceId={device_id}"
        f"&deviceType=2&platform=6"
    )

    auth_data = {
        "token": token,
        "player_id": player_id,
        "backend_domain": backend_domain,
        "device_id": device_id,
        "ws_url": ws_url,
    }

    AUTH_CACHE_PATH.write_text(json.dumps(auth_data, indent=2))
    print(f"✅ 认证信息已保存到 {AUTH_CACHE_PATH}")
    print(f"   player_id: {player_id}")
    print(f"   backend_domain: {backend_domain}")
    print(f"   device_id: {device_id}")
    print(f"   token: {token[:40]}...{token[-20:]}")
    print(f"   ws_url: {ws_url[:80]}...")

    await cdp.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
