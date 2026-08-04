"""build_api_headers / resolve_api_xxx 特征测试（Task 4）。

本机 node 可用（api_sign 走 PATH 真签名），因此"手动签名表兜底"测试需先
强制 wasm 失败才能稳定走兜底路径；test_no_wasm_when_disabled 直接关闭 wasm 层。
"""
from hdata.auth.headers import build_api_headers


def _sample_session(**over):
    s = {
        "token": "tk", "domain": "https://leyu.me", "cookies": "ck=1",
        "uuidToBase64": "", "signatures": {}, "device_uuid": "dev",
    }
    s.update(over)
    return s


def test_headers_structure():
    h = build_api_headers(_sample_session(), "https://leyu.me/game/api")
    assert set(h) >= {
        "X-API-TOKEN", "X-API-UUID", "X-API-XXX", "X-API-CLIENT",
        "X-API-SITE", "X-API-VERSION", "Content-Type", "Referer",
        "User-Agent", "Cookie",
    }


def test_manual_signature_fallback(monkeypatch):
    def no_wasm(*a, **k):
        raise RuntimeError("wasm unavailable（测试环境强制 wasm 失败）")

    monkeypatch.setattr("hdata.auth.headers.api_sign.sign_path", no_wasm)
    h = build_api_headers(
        _sample_session(signatures={"/game/api": "SIG"}),
        "https://leyu.me/game/api",
    )
    assert h["X-API-XXX"] == "SIG"


def test_no_wasm_when_disabled(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("wasm path must not be taken")

    monkeypatch.setattr("hdata.auth.headers.api_sign.sign_path", boom)
    h = build_api_headers(
        _sample_session(signatures={"/game/api": "SIG"}),
        "https://leyu.me/game/api", enable_wasm=False,
    )
    assert h["X-API-XXX"] == "SIG"
