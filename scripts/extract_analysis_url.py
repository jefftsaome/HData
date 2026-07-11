"""提取 AnalysisUrlUtils 并分析 params 解密流程

用法:
    uv run python scripts/extract_analysis_url.py
"""

import asyncio, json, base64, re
from hdata.capture.cdp_bridge import CDPSession
import aiohttp


EXTRACT_JS = r"""
JSON.stringify((() => {
    const result = { methods: {}, keys: [] };
    // 多种可能的命名
    const au = window.AnalysisUrlUtils || window.analysisUrlUtils || window.AnalysisURLUtils;
    if (!au) return { error: 'AnalysisUrlUtils not found' };
    
    for (const key of Object.getOwnPropertyNames(au)) {
        try {
            const val = au[key];
            result.methods[key] = typeof val === 'function' ? val.toString() : JSON.stringify(val);
        } catch(e) {
            result.methods[key] = '[Error: ' + e.message + ']';
        }
    }
    
    // 也检查原型链
    const proto = Object.getPrototypeOf(au);
    if (proto) {
        for (const key of Object.getOwnPropertyNames(proto)) {
            if (key === 'constructor') continue;
            try {
                const val = au[key];
                result.methods['__proto__.' + key] = typeof val === 'function' ? val.toString() : JSON.stringify(val);
            } catch(e) {}
        }
    }
    
    // 扫描所有方法源码中的密钥模式
    const allSource = Object.values(result.methods).join('\n');
    const patterns = [
        /\.parse\s*\(\s*["']([A-Za-z0-9+/=]{8,})["']\s*\)/g,
        /key\s*[:=]\s*["']([A-Za-z0-9+/=]{8,})["']/gi,
        /KEY\s*[:=]\s*["']([A-Za-z0-9+/=]{8,})["']/g,
        /iv\s*[:=]\s*["']([A-Za-z0-9+/=]{8,})["']/gi,
        /AES.*?["']([A-Za-z0-9+/=]{8,})["']/g,
        /decrypt.*?["']([A-Za-z0-9+/=]{8,})["']/gi,
    ];
    const found = new Set();
    for (const p of patterns) {
        let m; while ((m = p.exec(allSource)) !== null) found.add(m[1]);
    }
    result.keys = Array.from(found);
    
    // 检查实际 URL 和 params
    result.currentUrl = window.location.href;
    const urlParams = new URLSearchParams(window.location.search);
    result.hasParams = urlParams.has('params');
    if (result.hasParams) {
        result.paramsPreview = urlParams.get('params').slice(0, 50) + '...';
    }
    
    return result;
})())
"""


async def main():
    async with aiohttp.ClientSession() as s:
        r = await s.get("http://127.0.0.1:9222/json/version")
        d = await r.json()
    cdp = CDPSession(d["webSocketDebuggerUrl"])
    await cdp.connect()

    print("提取 AnalysisUrlUtils...")
    r = await cdp.evaluate(EXTRACT_JS)
    raw = r.get("value", "{}") if isinstance(r, dict) else "{}"
    data = json.loads(raw) if isinstance(raw, str) else raw

    if "error" in data:
        print(f"❌ {data['error']}")
    else:
        print(f"✅ AnalysisUrlUtils 找到")
        print(f"\n当前 URL: {data.get('currentUrl', 'N/A')[:80]}")
        print(f"URL 中带 params: {data.get('hasParams', False)}")
        if data.get("paramsPreview"):
            print(f"params 预览: {data['paramsPreview']}")

        print(f"\n发现的密钥 ({len(data.get('keys', []))} 个):")
        for k in data["keys"]:
            print(f"  {k}")

        print(f"\n方法 ({len(data.get('methods', {}))} 个):")
        for name, src in data.get("methods", {}).items():
            src_short = src[:200].replace("\n", "\\n")
            print(f"\n  [{name}]")
            print(f"    {src_short}")

    await cdp.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
