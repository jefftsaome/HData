"""Playwright 登录乐鱼 + 提取 params 测试

用法:
    uv run python scripts/test_playwright_login.py

流程:
    1. 启动 Chromium（反自动化检测配置）
    2. 打开乐鱼登录页
    3. 等待用户手动登录（在浏览器中操作）
    4. 捕获登录后的 URL（含 params）
    5. 解密 params → 输出 token/WS URL

如果失败，会生成截图到 /tmp/playwright_debug.png
"""

import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

# 乐鱼游戏大厅 URL（登录后会自动跳转到这个页面）
GAME_HALL_URL = "https://pc.lisxdc.com:2083/egret/hall"


async def main():
    async with async_playwright() as p:
        # 用系统 Chrome（Mac 路径）
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        browser = await p.chromium.launch(
            headless=False,  # 显示浏览器窗口，方便手动登录
            executable_path=chrome_path,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=ChromeWhatsNewUI",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
            ],
        )

        # 创建上下文（隔离的浏览器会话）
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 注入反自动化检测脚本（在每页加载前执行）
        await context.add_init_script("""
            // 隐藏 webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // 模拟 chrome.runtime
            if (!window.chrome?.runtime) {
                try {
                    Object.defineProperty(window.chrome, 'runtime', {
                        value: { id: 'fake' },
                        writable: false,
                    });
                } catch(e) {}
            }

            // 覆盖 Permissions 查询
            try {
                const origQuery = navigator.permissions.query;
                navigator.permissions.query = (params) => (
                    params.name === 'notifications'
                        ? Promise.resolve({ state: 'denied' })
                        : origQuery(params)
                );
            } catch(e) {}
        """)

        page = await context.new_page()

        # 监听页面跳转，捕获最终的 params URL
        params_url = None
        async def on_request(request):
            nonlocal params_url
            url = request.url
            if "egret/hall" in url and "params=" in url:
                params_url = url
                print(f"\n[捕获] 检测到游戏页面 URL")

        page.on("request", on_request)

        print("正在打开游戏页面...")
        try:
            await page.goto(GAME_HALL_URL, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"导航异常: {e}")

        print("\n========================================")
        print("请手动登录乐鱼（在打开的浏览器窗口中操作）")
        print("登录成功后，页面会自动跳转到游戏大厅")
        print("等待超时: 120 秒")
        print("========================================")

        # 等待用户登录并跳转到游戏大厅（最多等 2 分钟）
        try:
            await page.wait_for_url("**/egret/hall**", timeout=120000)
            print("\n✅ 已进入游戏大厅！")
        except Exception as e:
            print(f"\n❌ 等待超时: {e}")

        # 获取当前 URL
        current_url = page.url
        print(f"\n当前 URL: {current_url[:120]}...")

        # 提取 params
        import urllib.parse
        parsed = urllib.parse.urlparse(current_url)
        qs = urllib.parse.parse_qs(parsed.query)
        params = qs.get("params", [None])[0]
        ttl = qs.get("ttl", [None])[0]

        if params and ttl:
            print(f"\n✅ 成功获取 params 和 ttl!")
            print(f"  params[:50]: {params[:50]}...")
            print(f"  ttl: {ttl}")

            # 解密 params
            try:
                from scripts.decrypt_params import decrypt_params
                result = decrypt_params(params, ttl)
                print(f"\n✅ 解密成功!")
                print(f"  playerId: {result['playerId']}")
                print(f"  token: {result['token'][:40]}...")
                print(f"  backendDomainUrl: {result.get('backendDomainUrl')}")

                # 构造 WS URL
                domain = result.get("backendDomainUrl", "")
                host = domain.split(":")[0]
                port = domain.split(":")[1] if ":" in domain else "18026"
                ws_url = (
                    f"wss://wsproxy.{host}:{port}/"
                    f"?playerId={result['playerId']}"
                    f"&jwtToken={result['token']}"
                    f"&deviceType=2&platform=6"
                )
                print(f"\n  WS URL: {ws_url[:80]}...")

                # 保存到 .auth_cache.json
                import json as j
                cache = {
                    "token": result["token"],
                    "player_id": result["playerId"],
                    "backend_domain": domain,
                    "ws_url": ws_url,
                }
                Path(".auth_cache.json").write_text(j.dumps(cache, indent=2))
                print(f"\n  ✅ 已保存到 .auth_cache.json")

            except Exception as e:
                print(f"\n❌ 解密失败: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"\n❌ URL 中未找到 params 参数")
            print(f"  当前 URL params: params={'存在' if params else '缺失'}, ttl={'存在' if ttl else '缺失'}")

            # 截图保存
            await page.screenshot(path="/tmp/playwright_debug.png")
            print(f"  截图已保存到 /tmp/playwright_debug.png")

        input("\n按 Enter 键关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
