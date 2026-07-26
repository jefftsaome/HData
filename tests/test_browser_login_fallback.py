"""浏览器兜底登录体验优化单测（不触网、不启动真实浏览器）。

覆盖行为（2026-07-25 生产事故修复）：
  - 启动时关闭持久化 profile 恢复的残留标签页，新开干净页；
  - 等待人工登录期间每 WAIT_HEARTBEAT_S 秒打心跳日志；
  - 检测到登录态准备 re-nav 大厅前打"页面会自动跳转"预告日志，
    goto 失败打 warning 不吞掉；
  - session.py：打码超时自动重试 1 次（间隔 5s），重试成功即返回，
    不转浏览器；失败阶段（geetest 求解/登录请求/域名解析）写进日志。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from loguru import logger as _loguru

import hdata.auth.browser_login as bl
import hdata.auth.session as sess


class _LogCapture:
    """loguru 日志捕获上下文。"""

    def __init__(self, level="INFO"):
        self.messages: list[str] = []
        self._level = level
        self._sink = None

    def __enter__(self):
        self._sink = _loguru.add(lambda m: self.messages.append(str(m)),
                                 level=self._level)
        return self

    def __exit__(self, *exc):
        _loguru.remove(self._sink)

    def text(self) -> str:
        return "".join(self.messages)


def _make_bot(headless=False, account="lds003") -> bl.GameBrowserLogin:
    return bl.GameBrowserLogin(entry_url="https://leyu.me", headless=headless,
                               account=account)


# ── 残留标签页清理 ──


async def test_fresh_page_closes_stale_pages():
    """persistent profile 恢复的旧标签页全部关闭，使用新开页面。"""
    bot = _make_bot()
    p1, p2 = SimpleNamespace(close=AsyncMock()), SimpleNamespace(close=AsyncMock())
    p3 = SimpleNamespace(close=AsyncMock())
    context = SimpleNamespace(pages=[p1, p2], new_page=AsyncMock(return_value=p3))

    with _LogCapture() as cap:
        page = await bot._fresh_page(context)

    p1.close.assert_awaited_once()
    p2.close.assert_awaited_once()
    assert page is p3
    assert "关闭残留标签页 2 个" in cap.text()
    assert "lds003" in cap.text()                     # 账号标签


async def test_fresh_page_no_stale_pages():
    """无残留页时直接开新页，不打清理日志。"""
    bot = _make_bot()
    p = SimpleNamespace(close=AsyncMock())
    context = SimpleNamespace(pages=[], new_page=AsyncMock(return_value=p))
    with _LogCapture() as cap:
        page = await bot._fresh_page(context)
    assert page is p
    assert "残留" not in cap.text()


# ── 等待心跳 ──


async def test_wait_for_params_heartbeat(monkeypatch):
    """等待期间每 WAIT_HEARTBEAT_S 秒提示一次（人工登录文案）。"""
    monkeypatch.setattr(bl, "WAIT_HEARTBEAT_S", 0.05)
    monkeypatch.setattr(bl, "RENAV_INTERVAL_S", 9999)   # 不触发 re-nav
    bot = _make_bot(headless=False)
    context = SimpleNamespace(pages=[])

    with _LogCapture() as cap:
        result = await bot._wait_for_params(context, timeout=2)

    assert result is None
    assert "等待在浏览器中完成登录…（已等待" in cap.text()
    assert "/2s）" in cap.text()


async def test_wait_for_params_heartbeat_headless(monkeypatch):
    """headless 模式心跳文案是"等待自动跳转"。"""
    monkeypatch.setattr(bl, "WAIT_HEARTBEAT_S", 0.05)
    monkeypatch.setattr(bl, "RENAV_INTERVAL_S", 9999)
    bot = _make_bot(headless=True)
    context = SimpleNamespace(pages=[])

    with _LogCapture() as cap:
        result = await bot._wait_for_params(context, timeout=2)

    assert result is None
    assert "等待浏览器自动跳转…（已等待" in cap.text()


# ── re-nav 预告日志 ──


async def test_renav_logs_before_goto(monkeypatch):
    """检测到登录态 → goto 大厅前打预告；goto 失败打 warning。"""
    monkeypatch.setattr(bl, "RENAV_INTERVAL_S", 0.01)
    monkeypatch.setattr(bl, "FIRST_RENAV_DELAY_S", 0)
    monkeypatch.setattr(bl, "WAIT_HEARTBEAT_S", 9999)   # 不看心跳

    bot = _make_bot()
    page = SimpleNamespace(
        url="https://lyvip666.com/home",
        evaluate=AsyncMock(return_value="fake-x-api-token"),
        goto=AsyncMock(side_effect=RuntimeError("nav boom")))
    context = SimpleNamespace(pages=[page])

    with _LogCapture() as cap:
        await bot._wait_for_params(context, timeout=2)

    text = cap.text()
    assert "检测到登录态，正在进入大厅捕获凭证" in text
    assert "页面会自动跳转，请勿操作" in text
    assert "大厅跳转失败" in text                     # goto 异常不再静默
    page.goto.assert_awaited()


# ── session.py：打码超时重试 + 阶段分类 ──


def test_classify_http_login_stage():
    c = sess._classify_http_login_stage
    assert c(TimeoutError("Connect to https://bcaptcha.botion.com/verify timed out")) \
        == "geetest 验证码加载/求解"
    assert c(TimeoutError("POST https://lyvip666.com/site/api/v1/user/login")) \
        == "登录请求"
    assert c(TimeoutError("GET https://api.example.com/member/jwt")) \
        == "game_token 刷新"
    assert c(TimeoutError("https://leyu.me redirect")) == "域名解析"
    assert "未知" in c(RuntimeError("weird"))


def _patch_http_login(monkeypatch, side_effects):
    """把 hdata.auth.http_login.login 换成按序产生 side_effects 的假实现。"""
    import hdata.auth.http_login as hl
    calls = []

    async def fake_login(account, password, **kw):
        calls.append(account)
        item = side_effects[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(hl, "login", fake_login)
    return calls


async def test_http_login_timeout_retried_once_and_succeeds(monkeypatch):
    """首次打码超时 → 5s 后自动重试 → 成功返回，不转浏览器。"""
    monkeypatch.setattr(sess.asyncio, "sleep", AsyncMock())  # 不等真 5s
    calls = _patch_http_login(
        monkeypatch,
        [TimeoutError("https://bcaptcha.botion.com/load timeout"),
         {"token": "x-api-tok", "uuid": "u1", "domain": "https://d.com"}])

    async def fake_refresh(account, session):
        return {"token": "game-jwt", "backendDomainUrl": "b.com"}

    monkeypatch.setattr(sess, "refresh_game_session", fake_refresh)
    saved = []
    monkeypatch.setattr(sess, "save_session",
                        lambda account, data: saved.append(data) or data)

    with _LogCapture() as cap:
        result = await sess.get_login(
            "lds003", password="pwd", force_refresh=True,
            geepass_token="gp", entry_url="https://leyu.me")

    assert calls == ["lds003", "lds003"]              # 恰好重试 1 次
    assert result["game_token"] == "game-jwt"
    assert result["source"] == "http_login"
    text = cap.text()
    assert "阶段=geetest 验证码加载/求解" in text
    assert "5s 后重试 1 次" in text
    assert "fall to browser" not in text
    assert len(saved) == 1


async def test_http_login_retry_fails_then_falls_to_browser(monkeypatch):
    """重试仍超时 → 转浏览器兜底（日志带阶段）。"""
    monkeypatch.setattr(sess.asyncio, "sleep", AsyncMock())
    calls = _patch_http_login(
        monkeypatch,
        [TimeoutError("https://bcaptcha.botion.com/load timeout"),
         TimeoutError("https://lyvip666.com/site/api/v1/user/login timeout")])
    # 浏览器兜底必然失败（无密码可人工操作/无真实浏览器），
    # 验证走到浏览器路径即可——GameBrowserLogin.run 直接抛 LoginError
    monkeypatch.setattr(sess, "get_real_domain", lambda url: "https://d.com")

    class _FakeBot:
        def __init__(self, **kw):
            self.kw = kw

        async def run(self):
            return None                          # 模拟浏览器也没捕获到

    import hdata.auth.browser_login as bl_mod
    monkeypatch.setattr(bl_mod, "GameBrowserLogin", _FakeBot)

    with _LogCapture() as cap:
        with pytest.raises(sess.LoginError):
            await sess.get_login(
                "lds008", password="pwd", force_refresh=True,
                geepass_token="gp", entry_url="https://leyu.me")

    assert calls == ["lds008", "lds008"]
    text = cap.text()
    assert "HTTP login retry failed" in text
    assert "阶段=登录请求" in text
    assert "fall to browser" in text
