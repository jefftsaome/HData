"""API 签名请求头唯一实现（build_api_headers）与共享签名链（resolve_api_xxx）。

Task 4 从 session.py 抽出的叶子模块：签名头构造（含 X-API-UUID / User-Agent
私有助手）与 X-API-XXX 签名链（wasm → 手动签名表 → uuidToBase64 解密）。

两个调用方的 wasm 差异用 enable_wasm 参数保留：
  - session.py 的 _api_headers 调 enable_wasm=True（原 3 层签名链）
  - token_manager.py 的 _api_headers 调 enable_wasm=False（原无 wasm 层）
uuid/UA 计算各自保留在调用方，本模块不统一。
"""
from __future__ import annotations

import re

from hdata.auth import api_sign
from hdata.auth.fingerprint import get_ua
from hdata.auth.sign_table import decrypt_sign_table


def _device_uuid_for(session: dict) -> str:
    """X-API-UUID 取值：优先按账号取设备级 UUID（api_sign.get_uuid，
    2026-07-29 起按账号隔离持久化）；无账号上下文时回退会话里的
    uuid（JWT 回显值），保持旧会话兼容。
    """
    account = session.get("account", "")
    if account:
        try:
            val = api_sign.get_uuid(account)
            if val:
                return val
        except Exception:
            pass
    return session.get("uuid", "")


def _ua_for(session: dict) -> str:
    """User-Agent 取值：按账号取指纹画像 UA（与 TLS impersonate 版本一致），
    无账号上下文时给默认最新版 Windows Chrome UA。"""
    try:
        return get_ua(session.get("account", ""))
    except Exception:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        )


def resolve_api_xxx(session: dict, url: str, *, enable_wasm: bool = True) -> str:
    """共享的 X-API-XXX 签名链：wasm 动态签名 → 手动签名表 → uuidToBase64 解密。

    enable_wasm=False 时跳过 wasm 层（token_manager 版原无 wasm，行为差异保留）。
    """
    xxx = ""

    # 首选：wasm 动态签名（每请求唯一，服务端对 /game/api 等强制校验）
    if enable_wasm:
        try:
            m = re.search(r"/(\w+)/api", url)
            if m:
                xxx = api_sign.sign_path(f"/{m.group(1)}/api")
        except Exception:
            xxx = ""

    # 兜底 1：缓存中的手动捕获签名
    if not xxx:
        manual_sigs = session.get("signatures", {})
        if manual_sigs:
            for k in sorted(manual_sigs.keys(), key=lambda x: -len(x)):
                if k in url:
                    xxx = manual_sigs[k]
                    break

    # 兜底 2：从 uuidToBase64 解密签名表
    if not xxx:
        uuid_b64 = session.get("uuidToBase64", "")
        if uuid_b64:
            try:
                st = decrypt_sign_table(uuid_b64)
                xxx = next(
                    (v for k, v in sorted(st.items(), key=lambda x: -len(x[0])) if k in url),
                    "",
                )
            except Exception:
                pass

    return xxx


def build_api_headers(session: dict, url: str, *, enable_wasm: bool = True) -> dict:
    """构造乐鱼 API 请求头（含 X-API-XXX 签名）。

    优先使用 wasm 动态签名（api_sign.sign_path，2026-07-17 逆向落地）；
    失败时回退旧的静态签名表（手动捕获 / uuidToBase64 解密）。
    """
    xxx = resolve_api_xxx(session, url, enable_wasm=enable_wasm)

    return {
        "X-API-TOKEN": session.get("token", ""),
        "X-API-UUID": _device_uuid_for(session),
        "X-API-XXX": xxx,
        "X-API-CLIENT": "web",
        "X-API-SITE": "2001",
        "X-API-VERSION": "2.0.0",
        "Content-Type": "application/json",
        "Referer": session.get("domain", "") + "/",
        "User-Agent": _ua_for(session),
        "Cookie": session.get("cookies", ""),
    }
