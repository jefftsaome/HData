"""签名自动捕获 — 通过 CDP Network 监控提取 X-API-XXX。

原理:
  1. 连 browser-act CDP
  2. 启用 Network.enable 监控所有请求
  3. 导航到需要登录的页面（如 /user/login）
  4. 页面加载时浏览器会发出各种 API 请求，带 X-API-XXX header
  5. 按 URL 路径前缀分组提取签名值
  6. 保存到 .cache/{account}.json 的 signatures 字段

用法:
    uv run python -m hdt.auth.signature_recapture --account lidongsen1
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import aiohttp
import websockets

from htools.utils.logger import get_logger

logger = get_logger(__name__)

_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = _PROJ_ROOT / ".cache"


async def recapture_signatures(account: str, cdp_port: int = 0) -> dict[str, str]:
    """捕获当前域名的 API 签名，返回 {path_prefix: signature} 映射。"""

    # 发现 CDP 端口
    if not cdp_port:
        cdp_port = _discover_cdp_port()
    cdp_base = f"http://127.0.0.1:{cdp_port}"

    # 获取页面
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{cdp_base}/json/list", timeout=5)
        targets = await r.json()
        r2 = await s.get(f"{cdp_base}/json/version", timeout=5)
        ws_url = (await r2.json()).get("webSocketDebuggerUrl", "")

    if not ws_url:
        raise RuntimeError(f"CDP 不可用 (port={cdp_port})")

    page_t = next((t for t in targets if t["type"] == "page"), None)
    if not page_t:
        raise RuntimeError("未找到 page target")

    captured: dict[str, str] = {}  # path_prefix → signature

    async with websockets.connect(ws_url, max_size=10 * 10**6) as ws:
        cdp_mid = 0

        async def cdp(method, params=None, sid=None):
            nonlocal cdp_mid; cdp_mid += 1
            msg = {"id": cdp_mid, "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return cdp_mid

        # Attach
        await cdp("Target.attachToTarget",
                  {"targetId": page_t["id"], "flatten": True})
        cdp_sid = ""
        dl = time.time() + 5
        while time.time() < dl:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if msg.get("id") == cdp_mid:
                cdp_sid = msg.get("result", {}).get("sessionId", "")
            if msg.get("method") == "Target.attachedToTarget":
                cdp_sid = msg["params"]["sessionId"]
            if cdp_sid:
                break

        if not cdp_sid:
            raise RuntimeError("attachToTarget 失败")

        # 启用 Network 监控
        await cdp("Network.enable", sid=cdp_sid)
        logger.debug("Network.enable OK — 等待 API 请求...")

        # 导航到一个需要认证的页面（触发 API 请求）
        await cdp("Page.navigate",
                  {"url": "https://www.qgayax.vip:9174/"}, sid=cdp_sid)

        # 收集 X-API-XXX headers
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)
            method = msg.get("method", "")

            if method == "Network.requestWillBeSent":
                req = msg.get("params", {}).get("request", {})
                headers = req.get("headers", {})
                xxx = headers.get("X-API-XXX", "") or headers.get("x-api-xxx", "")
                url = req.get("url", "")

                if xxx and url:
                    # 提取路径前缀: /game/api, /site/api, /act/api 等
                    m = re.search(r"https?://[^/]+(/\w+/\w+)", url)
                    if m:
                        prefix = m.group(1)
                        if prefix not in captured:
                            captured[prefix] = xxx
                            logger.debug(f"  捕获签名: {prefix} → {xxx[:30]}...")

        logger.info(f"共捕获 {len(captured)} 个签名")

    # 保存到缓存
    if captured:
        cache_path = CACHE_DIR / f"{account}.json"
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
            cache["signatures"] = captured
            cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
            logger.info(f"已更新 {cache_path}")

    return captured


def _discover_cdp_port() -> int:
    """自动发现 browser-act CDP 端口。"""
    import subprocess, os
    env = os.getenv("LEYU_CDP_PORT", "")
    if env and env.isdigit():
        return int(env)
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if 'BrowserAct' in line and 'remote-debugging-port' in line:
                m = re.search(r'remote-debugging-port=(\d+)', line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return 57073


# CLI
async def main():
    import argparse
    import sys

    p = argparse.ArgumentParser(description="X-API-XXX 签名自动捕获")
    p.add_argument("--account", default="default")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args()

    try:
        sigs = await recapture_signatures(args.account, args.port)
        if sigs:
            print(f"✅ 捕获 {len(sigs)} 个签名: {list(sigs.keys())}")
            return 0
        else:
            print("❌ 未捕获到签名 — 确保 browser-act 已打开且有已登录页面")
            return 1
    except Exception as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
