"""登录链路 HTTP 留底（login_events 埋点核心）。

背景：采集侧需要把登录链路（打码验证 / 参数刷新 / 登录请求 / token 刷新）
的每一次 HTTP 请求留底到 PG login_events 表，用于事后审计登录失败原因、
打码平台耗时、域名轮换等。hdata 作为通用包不感知存储——本模块只负责
**产生规范化事件**，由调用方（crawl-bot）通过 set_sink() 接走落库。

事件字段（与 login_events 表一一对应）：
    ts / account / stage / method / url / status / elapsed_ms / ok /
    summary / strategy / run_id / source

脱敏口径（留底可审计、不泄露凭证）：
  - URL：保留 scheme://host/path；query 里敏感键（token/sign/seccode/w/
    payload/process_token/challenge/params 等）的值一律替换为 <redacted>，
    非敏感值截断到 60 字符。
  - 响应摘要：dict 结构保留，敏感键的值替换为 sha256 前 12 位 hex
    （可关联同一次 token 但不存原文）；超长字符串截断；整体约 800 字符封顶。
  - 密码/token 原文永远不会进入事件（埋点处本就不传）。

用法：
    from hdata.auth import login_trace

    # 采集侧启动时接走事件（sync 回调，必须快速非阻塞）
    login_trace.set_sink(lambda ev: queue.put_nowait(ev))

    # 调用方绑定上下文（account/strategy/run_id 自动带进后续事件）
    with login_trace.bind(account="a1", strategy="streak", run_id=12):
        await client.login(...)        # 内部各埋点自动携带上下文

    # 埋点处直接发事件（account 显式传，strategy/run_id 来自上下文）
    login_trace.emit("login", method="POST", url=url, status=200,
                     elapsed_ms=123, ok=True, summary=resp, source="http_login")
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
from collections import deque
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

# 事件去路：sink 未接时进内存兜底缓冲（防丢，set_sink 时一并 drain）
_sink = None
_buf: deque = deque(maxlen=5000)
_lock = threading.Lock()

# 调用方上下文（account/strategy/run_id），contextvars 跨 asyncio task 传播
_ctx: ContextVar[dict] = ContextVar("hdata_login_trace_ctx", default={})

# URL query / 响应体里的敏感键（值打码）
_SENSITIVE_KEY = re.compile(
    r"^(w|token|.*token|sign|signature|seccode|secret|password|pwd|"
    r"payload|process_token|challenge|params|codeId|jwt)$", re.I)

MAX_URL_VALUE = 60          # query 非敏感值截断长度
MAX_STR = 120               # 摘要里字符串截断长度
MAX_SUMMARY = 800           # 摘要整体字符上限
MAX_DEPTH = 3               # 摘要递归深度


# ---------------------------------------------------------------- 上下文

@contextmanager
def bind(account: str = "", strategy: str = "", run_id=None):
    """绑定调用上下文：with 块内产生的埋点事件自动带 account/strategy/run_id。"""
    merged = {**_ctx.get()}
    if account:
        merged["account"] = account
    if strategy:
        merged["strategy"] = strategy
    if run_id is not None:
        merged["run_id"] = run_id
    token = _ctx.set(merged)
    try:
        yield
    finally:
        _ctx.reset(token)


def set_sink(fn) -> None:
    """接走事件：fn(event_dict)，sync 调用、必须快速非阻塞（可 put_nowait）。

    设置时把兜底缓冲里积压的事件先补发给它。
    """
    global _sink
    with _lock:
        backlog = list(_buf)
        _buf.clear()
        _sink = fn
    for ev in backlog:
        _deliver(fn, ev)


def clear_sink() -> None:
    global _sink
    with _lock:
        _sink = None


def buffered_count() -> int:
    """当前兜底缓冲里积压的事件数（sink 未接或投递失败时 >0）。"""
    return len(_buf)


# ---------------------------------------------------------------- 脱敏

def sanitize_url(url: str) -> str:
    """URL 留底：保留 scheme://host/path；敏感 query 值打码，其余截断。"""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return url[:200]
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    if not parts.query:
        return base
    kept = []
    for pair in parts.query.split("&"):
        key, _, val = pair.partition("=")
        if _SENSITIVE_KEY.match(key):
            kept.append(f"{key}=<redacted>")
        elif len(val) > MAX_URL_VALUE:
            kept.append(f"{key}={val[:MAX_URL_VALUE]}…")
        else:
            kept.append(pair)
    return base + "?" + "&".join(kept)


def _hash_value(val) -> str:
    raw = val if isinstance(val, str) else json.dumps(val, default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _scrub(obj, depth: int):
    """递归脱敏：敏感键值哈希化、超长字符串截断、超深只留类型名。"""
    if depth > MAX_DEPTH:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            if _SENSITIVE_KEY.match(str(k)) and v:
                out[k] = _hash_value(v)
            else:
                out[k] = _scrub(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_scrub(x, depth + 1) for x in obj[:20]]
    if isinstance(obj, str) and len(obj) > MAX_STR:
        return obj[:MAX_STR] + f"…(len={len(obj)})"
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)[:MAX_STR]


def summarize(body, limit: int = MAX_SUMMARY) -> str:
    """响应体 → 留底摘要（脱敏 + 限长）。非 JSON 输入按字符串截断。"""
    if body is None:
        return ""
    if isinstance(body, str):
        return body[:limit]
    try:
        text = json.dumps(_scrub(body, 0), ensure_ascii=False, default=str)
    except Exception:
        return str(body)[:limit]
    return text[:limit]


# ---------------------------------------------------------------- 发事件

def _deliver(fn, ev: dict) -> None:
    try:
        fn(ev)
    except Exception:
        with _lock:
            _buf.append(ev)          # sink 投递失败回兜底缓冲，不炸登录链路


def emit(stage: str, *, method: str = "", url: str = "",
         status: int | None = None, elapsed_ms: int | None = None,
         ok: bool | None = None, summary=None, account: str = "",
         source: str = "") -> dict:
    """产生一条登录链路事件并投递。任何异常都不允许影响登录主流程。"""
    try:
        ctx = _ctx.get()
        ev = {
            "ts": int(time.time() * 1000),
            "account": account or ctx.get("account", ""),
            "stage": stage,
            "method": method,
            "url": sanitize_url(url),
            "status": status,
            "elapsed_ms": elapsed_ms,
            "ok": ok,
            "summary": summarize(summary) if summary is not None else "",
            "strategy": ctx.get("strategy", ""),
            "run_id": ctx.get("run_id"),
            "source": source,
        }
    except Exception:
        return {}                    # 构造事件本身失败：静默丢弃
    sink = _sink
    if sink is None:
        with _lock:
            _buf.append(ev)
    else:
        _deliver(sink, ev)
    return ev
