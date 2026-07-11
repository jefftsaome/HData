"""从已有 Chrome 提取 token（通过 CDP 读 window.urlParams）

用法:
    1. Chrome 启动在 9222 端口，已登录乐鱼
    2. uv run python scripts/extract_token.py
"""

import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

CDP_PORT = 9222


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            f"http://127.0.0.1:{CDP_PORT}"
        )
        print(f"✅ 已连接到 Chrome (端口 {CDP_PORT})")

        # 在所有页面中找有 urlParams 的
        for ctx in browser.contexts:
            for page in ctx.pages:
                url_params = await page.evaluate("window.urlParams")
                if url_params and url_params.get("token"):
                    token = url_params["token"]
                    player_id = url_params["playerId"]
                    domain = url_params.get("backendDomainUrl", "")
                    print(f"\n✅ 找到 token!")
                    print(f"  playerId: {player_id}")
                    print(f"  token: {token[:40]}...{token[-20:]}")
                    print(f"  backendDomainUrl: {domain}")

                    # 构造 WS URL
                    host = domain.split(":")[0]
                    port = domain.split(":")[1] if ":" in domain else "18026"
                    ws_url = (
                        f"wss://wsproxy.{host}:{port}/"
                        f"?playerId={player_id}"
                        f"&jwtToken={token}"
                        f"&deviceType=2&platform=6"
                    )
                    print(f"\n  WS URL: {ws_url[:80]}...")

                    # 保存
                    cache = {
                        "token": token,
                        "player_id": player_id,
                        "backend_domain": domain,
                        "ws_url": ws_url,
                    }
                    Path(".auth_cache.json").write_text(json.dumps(cache, indent=2))
                    print(f"\n✅ 已保存到 .auth_cache.json")
                    break
            else:
                continue
            break
        else:
            print("❌ 未找到含 urlParams 的页面")
            print("请确认 Chrome 已登录乐鱼并打开了游戏页面")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
