"""_refresh_cb 兜底账号 / 新鲜跳过 / 失败重试 / 刷新节流 单测（不触网）。

回归背景（2026-07-20 生产事故）：所有分片共享主客户端 _refresh_cb，
旧实现兜底重登写死 self._account——任一分片刷新失败都会错误重登
主账号并把主账号会话塞进分片，多条连接以同一账号建连互相顶号
（jti 10026），形成每 15~20s 一次的打码登录死循环（持续 40+ 分钟）。
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

import hdata.client as hc
from hdata.auth.session import LoginError
from hdata.client import GameClient


def _client() -> GameClient:
    c = GameClient(entry_url="https://leyu.com")
    c._account = "main1"
    c._password = "mainpw"
    return c


async def test_fallback_uses_shard_account_and_proxy(monkeypatch):
    """分片刷新失败 → 兜底必须用分片账号+分片代理，不污染主会话。"""
    monkeypatch.setattr(hc, "_REFRESH_RETRY_DELAY_S", 0)
    client = _client()
    client._session = {"account": "main1", "_password": "mainpw"}
    client._refresh_game_token = AsyncMock(
        side_effect=RuntimeError("rate limited"))
    captured = {}

    async def fake_login(account, password, **kw):
        captured.update(account=account, password=password, **kw)
        return {"account": account, "game_token": "new"}

    monkeypatch.setattr(hc, "_session_login", fake_login)
    shard = {"account": "lds002", "_password": "pw2",
             "proxy": "http://u:p@host:8011", "game_token": "old"}
    fresh = await client._refresh_cb(shard)
    assert captured["account"] == "lds002"          # 不是 main1
    assert captured["password"] == "pw2"
    assert captured["proxy"] == "http://u:p@host:8011"
    assert captured["force_refresh"] is True
    assert client._session["account"] == "main1"    # 主会话不被污染
    assert fresh["account"] == "lds002"
    assert fresh["_password"] == "pw2"              # 供下次兜底


async def test_fallback_main_account_updates_client_session(monkeypatch):
    """主账号刷新失败 → 兜底重登后才更新主客户端会话。"""
    monkeypatch.setattr(hc, "_REFRESH_RETRY_DELAY_S", 0)
    client = _client()
    old = {"account": "main1", "_password": "mainpw", "game_token": "old"}
    client._session = old
    client._refresh_game_token = AsyncMock(side_effect=RuntimeError("x"))

    async def fake_login(account, password, **kw):
        assert account == "main1" and password == "mainpw"
        return {"account": account, "game_token": "new"}

    monkeypatch.setattr(hc, "_session_login", fake_login)
    fresh = await client._refresh_cb(old)
    assert client._session is fresh
    assert fresh["game_token"] == "new"


async def test_skip_when_token_fresh():
    """_REFRESH_SKIP_S 内刚刷过的 token 直接复用，不再请求刷新接口。"""
    client = _client()
    client._refresh_game_token = AsyncMock(
        side_effect=AssertionError("不应被调用"))
    s = {"account": "lds002", "_refresh_ts": time.time()}
    out = await client._refresh_cb(s)
    assert out is s


async def test_retry_once_then_success(monkeypatch):
    """首次刷新失败（限流类）→ 退避重试一次成功即返回，不兜底重登。"""
    monkeypatch.setattr(hc, "_REFRESH_RETRY_DELAY_S", 0)
    client = _client()
    calls = {"n": 0}

    async def flaky(account, session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rate limited")
        return session

    client._refresh_game_token = flaky
    s = {"account": "lds002"}
    out = await client._refresh_cb(s)
    assert out is s and calls["n"] == 2


async def test_fallback_without_password_raises(monkeypatch):
    """分片会话无密码可兜底 → 抛 LoginError（由连接层记日志并沿用旧 token）。"""
    monkeypatch.setattr(hc, "_REFRESH_RETRY_DELAY_S", 0)
    client = _client()
    client._refresh_game_token = AsyncMock(side_effect=RuntimeError("x"))
    with pytest.raises(LoginError):
        await client._refresh_cb({"account": "lds002", "game_token": "old"})


async def test_refresh_throttle_spacing(monkeypatch):
    """进程级节流：连续两次 acquire 间隔不小于 MIN_INTERVAL。"""
    monkeypatch.setattr(hc, "_REFRESH_MIN_INTERVAL_S", 0.05)
    hc._RefreshThrottle._last_ts = 0.0
    t0 = time.monotonic()
    await hc._RefreshThrottle.acquire()
    await hc._RefreshThrottle.acquire()
    assert time.monotonic() - t0 >= 0.05
