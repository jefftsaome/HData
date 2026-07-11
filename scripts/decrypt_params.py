"""解密乐鱼 params 参数，获取认证数据

用法:
    # 从 CDP 获取 params+ttl 并解密
    uv run python scripts/decrypt_params.py

    # 直接传入 params+ttl 解密
    uv run python scripts/decrypt_params.py --params 'xxx' --ttl 'xxx'

解密算法:
    - Key = ttl + "AES" (如 "1782535308601AES")
    - 模式: AES-ECB, PKCS7 padding
    - 输出: JSON {playerId, token, backendDomainUrl, ...}
"""
import base64, json, sys
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathlib import Path


def decrypt_params(params_b64: str, ttl: str) -> dict:
    """解密乐鱼 params 参数。

    Args:
        params_b64: URL 中的 params 参数值（base64 编码，+ 号保留）
        ttl: URL 中的 ttl 参数值

    Returns:
        解密后的 dict，包含 playerId/token/backendDomainUrl 等
    """
    key = (ttl + "AES").encode("ascii")
    ct = base64.b64decode(params_b64)
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    padded = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    data = padded[: -padded[-1]]  # PKCS7 去填充
    return json.loads(data)


def main():
    if "--params" in sys.argv and "--ttl" in sys.argv:
        idx = sys.argv.index("--params")
        params = sys.argv[idx + 1]
        idx = sys.argv.index("--ttl")
        ttl = sys.argv[idx + 1]
        result = decrypt_params(params, ttl)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # 默认从 CDP 获取
    import asyncio
    import aiohttp
    from hdata.capture.cdp_bridge import CDPSession

    async def from_cdp():
        async with aiohttp.ClientSession() as s:
            r = await s.get("http://127.0.0.1:9222/json/version")
            d = await r.json()
        cdp = CDPSession(d["webSocketDebuggerUrl"])
        await cdp.connect()
        r = await cdp.evaluate(
            """JSON.stringify({
                p: window.location.search.match(/[?&]params=([^&]*)/)[1],
                ttl: new URLSearchParams(window.location.search).get("ttl")
            })"""
        )
        raw = r.get("value", "{}") if isinstance(r, dict) else "{}"
        data = json.loads(raw) if isinstance(raw, str) else raw
        await cdp.disconnect()
        return data["p"], data["ttl"]

    params, ttl = asyncio.run(from_cdp())
    result = decrypt_params(params, ttl)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # 打印可用 WS URL
    print("\n=== WS URLs ===")
    for domain in result.get("backendDomainUrlList", "").split(","):
        domain = domain.strip()
        host = domain.split(":")[0]
        port = domain.split(":")[1] if ":" in domain else "18026"
        ws = f"wss://wsproxy.{host}:{port}/?playerId={result['playerId']}&jwtToken={result['token']}&deviceType=2&platform=6"
        print(f"  {ws}")


if __name__ == "__main__":
    main()
