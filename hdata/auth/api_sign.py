"""X-API-XXX 签名 — 通过 Node 运行站点 wasm（wasm_api_sign_bg.wasm）。

签名算法在 WebAssembly 中（见 docs/login-api-capture-20260717.md 第四节），
无法纯 Python 复现，用 Node 直接执行官方 wasm 是最可靠的方案。

用法:
    from hdata.auth.api_sign import sign_path, get_uuid, common_headers
    sig = sign_path("/site/api")          # 64 hex，每次调用都不同（含时间+随机）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid as _uuid_mod
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SIGN_JS = _ROOT / "scripts" / "sign_wasm.cjs"
_UUID_CACHE = _ROOT / ".cache" / "api_uuid.txt"

_NODE = shutil.which("node")


class SignError(RuntimeError):
    pass


def _normalize_path(path: str) -> str:
    """与前端 87802 模块一致的路径归一化。"""
    if "/component" in path:
        return "/site/api"
    if "/page/fd" in path:
        return "/fd/api"
    return path


def sign_path(path: str, env: str = "prod", timeout: float = 10.0) -> str:
    """生成 X-API-XXX 签名（64 hex）。

    Args:
        path: API 路径前缀，如 "/site/api"、"/game/api"、"/act/api"
        env: wasm 第二参数，浏览器固定传 "prod"
    """
    if not _NODE:
        raise SignError("node 不在 PATH 中，无法生成 X-API-XXX 签名")
    if not _SIGN_JS.exists():
        raise SignError(f"签名脚本缺失: {_SIGN_JS}")

    p = _normalize_path(path)
    try:
        out = subprocess.run(
            [_NODE, str(_SIGN_JS), p, env],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SignError(f"签名超时: {p}") from exc

    sig = (out.stdout or "").strip()
    if out.returncode != 0 or len(sig) != 64:
        raise SignError(f"签名失败 rc={out.returncode}: {(out.stderr or '')[:200]}")
    return sig


def get_uuid() -> str:
    """X-API-UUID — 持久化大写 UUID（模拟浏览器 localStorage._uuid）。"""
    try:
        if _UUID_CACHE.exists():
            val = _UUID_CACHE.read_text(encoding="utf-8").strip()
            if val:
                return val
    except OSError:
        pass
    val = str(_uuid_mod.uuid4()).upper()
    try:
        _UUID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _UUID_CACHE.write_text(val, encoding="utf-8")
    except OSError:
        pass
    return val


def common_headers(
    path: str,
    *,
    token: str = "",
    finger: str = "",
    domain: str = "",
    referer_path: str = "/user/login",
) -> dict:
    """构造站点 API 公共请求头（与浏览器一致）。

    Args:
        path: API 路径（如 "/site/api/v1/user/login"），签名取其前两段
        token: X-API-TOKEN（登录前可为空或旧值）
        finger: X-API-FINGER（仅 login 接口需要）
        domain: 站点域名（用于 Referer，可选）
    """
    # 浏览器签名输入是路径前两段，如 "/site/api"
    parts = [s for s in path.split("/") if s]
    prefix = "/" + "/".join(parts[:2]) if len(parts) >= 2 else path

    headers = {
        "Content-Type": "application/json",
        "X-API-CLIENT": "web",
        "X-API-XXX": sign_path(prefix),
        "X-API-VERSION": "2.0.0",
        "X-API-SITE": "2001",
        "X-API-UUID": get_uuid(),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
    }
    if token:
        headers["X-API-TOKEN"] = token
    if finger:
        headers["X-API-FINGER"] = finger
    if domain:
        headers["Referer"] = f"{domain}{referer_path}"
    return headers
