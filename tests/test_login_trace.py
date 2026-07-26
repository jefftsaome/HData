"""login_trace 埋点核心：URL/摘要脱敏、事件投递、上下文绑定、兜底缓冲。"""
import json

import pytest

from hdata.auth import login_trace


@pytest.fixture(autouse=True)
def _clean_sink():
    """每个用例前后清 sink / 缓冲 / 持久上下文，避免串场。"""
    login_trace.clear_sink()
    login_trace._buf.clear()
    login_trace._ctx.set({})
    yield
    login_trace.clear_sink()
    login_trace._buf.clear()
    login_trace._ctx.set({})


# ---------------------------------------------------------------- URL 脱敏
def test_sanitize_url_redacts_sensitive_query():
    url = ("https://bcaptcha.botion.com/verify?captcha_id=abc123&"
           "lot_number=LOT777&w=BIGENCRYPTEDBLOB&payload=SECRET&"
           "process_token=PT&callback=botion_1")
    out = login_trace.sanitize_url(url)
    assert out.startswith("https://bcaptcha.botion.com/verify?")
    assert "captcha_id=abc123" in out          # 非敏感键保留原文
    assert "lot_number=LOT777" in out
    assert "w=<redacted>" in out
    assert "payload=<redacted>" in out
    assert "process_token=<redacted>" in out
    assert "BIGENCRYPTEDBLOB" not in out and "SECRET" not in out
    assert "PT&" not in out                    # process_token 值已打码


def test_sanitize_url_truncates_long_values():
    url = "https://x.com/path?k=" + "v" * 200
    out = login_trace.sanitize_url(url)
    assert len(out) < len(url)
    assert "v" * 60 in out


def test_sanitize_url_no_query_and_garbage():
    assert login_trace.sanitize_url("https://x.com/p") == "https://x.com/p"
    assert login_trace.sanitize_url("") == ""
    assert login_trace.sanitize_url("not a url")  # 不炸


# ---------------------------------------------------------------- 摘要脱敏
def test_summarize_hashes_token_keeps_shape():
    body = {"status_code": 6000,
            "data": {"token": "RAWTOKEN123456", "uuid": "u-1", "expire": 3600}}
    out = json.loads(login_trace.summarize(body))
    assert out["status_code"] == 6000
    assert out["data"]["token"].startswith("sha256:")
    assert "RAWTOKEN123456" not in json.dumps(out)
    assert out["data"]["uuid"] == "u-1"        # uuid 非敏感，保留
    # 同一个 token 哈希稳定（可关联）
    assert out["data"]["token"] == json.loads(
        login_trace.summarize({"token": "RAWTOKEN123456"}))["token"]


def test_summarize_truncates_and_caps():
    body = {"msg": "x" * 500}
    out = login_trace.summarize(body)
    assert len(out) <= 800
    assert "…(len=500)" in out
    assert login_trace.summarize("plain text") == "plain text"
    assert login_trace.summarize(None) == ""


def test_summarize_deep_structure_safe():
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    out = login_trace.summarize(deep)
    assert out  # 不炸，超深留类型名


# ---------------------------------------------------------------- 事件投递
def test_emit_to_sink_with_context():
    got = []
    login_trace.set_sink(got.append)
    with login_trace.bind(account="acc1", strategy="streak", run_id=42):
        ev = login_trace.emit(
            "login", method="POST", url="https://d.com/site/api/v1/user/login",
            status=200, elapsed_ms=88, ok=True,
            summary={"status_code": 6000}, source="http_login")
    assert len(got) == 1
    e = got[0]
    assert e["account"] == "acc1" and e["strategy"] == "streak"
    assert e["run_id"] == 42 and e["stage"] == "login"
    assert e["status"] == 200 and e["ok"] is True
    assert e["source"] == "http_login" and e["ts"] > 0
    assert ev == e


def test_emit_explicit_account_overrides_context():
    got = []
    login_trace.set_sink(got.append)
    with login_trace.bind(account="ctx-acc"):
        login_trace.emit("login", account="explicit-acc")
    assert got[0]["account"] == "explicit-acc"


def test_emit_buffers_without_sink_then_drains_on_set():
    login_trace.emit("login", account="a1")
    login_trace.emit("token_refresh", account="a2")
    assert login_trace.buffered_count() == 2
    got = []
    login_trace.set_sink(got.append)
    assert [e["account"] for e in got] == ["a1", "a2"]   # 补发顺序保持
    assert login_trace.buffered_count() == 0
    login_trace.emit("login", account="a3")
    assert got[-1]["account"] == "a3"                    # 之后直达


def test_sink_exception_falls_back_to_buffer():
    def bad_sink(ev):
        raise RuntimeError("boom")
    login_trace.set_sink(bad_sink)
    login_trace.emit("login", account="a1")              # 不炸
    assert login_trace.buffered_count() == 1
    login_trace.clear_sink()


def test_emit_never_breaks_on_bad_input():
    got = []
    login_trace.set_sink(got.append)
    # summary 传不可 JSON 化的对象也不允许炸
    login_trace.emit("x", summary=object())
    assert len(got) == 1


# ---------------------------------------------------------------- 出口标识
def test_bind_proxy_masked_host_only():
    # 测试代理一律用 RFC 5737 文档保留地址，绝不写真代理
    with login_trace.bind(account="acc1",
                          proxy="http://user:pass@203.0.113.9:8011"):
        ev = login_trace.emit("login", method="POST",
                              url="https://x.com/login", ok=True)
    assert ev["proxy"] == "203.0.113.9:8011"
    assert "user" not in ev["proxy"] and "pass" not in ev["proxy"]


def test_bind_proxy_no_credentials_passthrough():
    with login_trace.bind(proxy="198.51.100.7:8011"):
        ev = login_trace.emit("login_path", method="-")
    assert ev["proxy"] == "198.51.100.7:8011"     # 无账密形式原样保留


def test_set_context_proxy_masked():
    login_trace.set_context(proxy="socks5://u2:p2@192.0.2.1:1080")
    ev = login_trace.emit("token_refresh", account="a1")
    assert ev["proxy"] == "192.0.2.1:1080"


def test_push_pop_context_roundtrip():
    tok = login_trace.push_context(account="acc9",
                                   proxy="http://u:p@203.0.113.20:8011")
    ev = login_trace.emit("token_refresh")
    assert ev["account"] == "acc9" and ev["proxy"] == "203.0.113.20:8011"
    login_trace.pop_context(tok)
    ev2 = login_trace.emit("token_refresh")
    assert ev2["proxy"] == ""                      # pop 后不残留


def test_no_proxy_context_empty_string():
    ev = login_trace.emit("login", account="a1")
    assert ev["proxy"] == ""
