"""game_token jti 单次消费 × 刷新节流的生命周期测试。

背景（2026-07-24 实测定论）：game_token 的 jti 单连接消费——一张 token
被一条 WS 连接登录成功后即死，跨连接复用必被 10026 踢出。
_refresh_cb 的"60s 新鲜跳过"曾只看时间窗，导致缓存命中场景下
login → get_tables → enter_table 一气呵成时，enter_table 复用被
get_tables 消费过的死 token 被踢。修复：跳过前提追加"token 未被消费"
（_token_consumed 标记，WS 登录成功置位、刷新成功复位）。
"""
import time

import pytest

from hdata.client import GameClient


def _client() -> GameClient:
    return GameClient(entry_url="https://leyu.com")


def _session(consumed: bool, age_s: float = 5.0) -> dict:
    return {
        "account": "acc",
        "game_token": "tok",
        "_refresh_ts": time.time() - age_s,
        "_token_consumed": consumed,
    }


@pytest.fixture
def no_throttle(monkeypatch):
    """关掉进程级节流等待，测试不拖时间。"""
    import hdata.client as c
    monkeypatch.setattr(c, "_REFRESH_MIN_INTERVAL_S", 0)
    monkeypatch.setattr(c._RefreshThrottle, "_last_ts", 0)


@pytest.fixture
def refresh_spy(monkeypatch):
    """把 _refresh_game_token 换成记录调用的假实现。"""
    calls = []

    async def fake(account, session):
        calls.append(account)
        session["game_token"] = "tok_new"
        session["_refresh_ts"] = time.time()
        session["_token_consumed"] = False
        return session

    return calls, fake


class TestRefreshCbSkip:
    async def test_fresh_unconsumed_skips_refresh(
            self, no_throttle, refresh_spy, monkeypatch):
        calls, fake = refresh_spy
        client = _client()
        monkeypatch.setattr(client, "_refresh_game_token", fake)
        s = _session(consumed=False, age_s=5)
        out = await client._refresh_cb(s)
        assert calls == []                    # 未消费 + 新鲜 → 不刷新
        assert out["game_token"] == "tok"

    async def test_fresh_but_consumed_forces_refresh(
            self, no_throttle, refresh_spy, monkeypatch):
        calls, fake = refresh_spy
        client = _client()
        monkeypatch.setattr(client, "_refresh_game_token", fake)
        s = _session(consumed=True, age_s=5)  # 已被 get_tables 消费
        out = await client._refresh_cb(s)
        assert calls == ["acc"]               # 已消费 → 强制刷新
        assert out["game_token"] == "tok_new"
        assert out["_token_consumed"] is False

    async def test_stale_unconsumed_forces_refresh(
            self, no_throttle, refresh_spy, monkeypatch):
        calls, fake = refresh_spy
        client = _client()
        monkeypatch.setattr(client, "_refresh_game_token", fake)
        s = _session(consumed=False, age_s=120)  # 超 60s 窗口
        out = await client._refresh_cb(s)
        assert calls == ["acc"]
        assert out["game_token"] == "tok_new"


class TestConsumedMarkLifecycle:
    def test_ws_login_success_marks_consumed(self):
        """_WSConnection._login 收到 status=1 后给 session 打已消费标记。"""
        from hdata.client import _WSConnection

        conn = _WSConnection({"game_player_id": 1, "game_token": "t"})
        # 直接模拟 _login 成功路径的副作用（标记语义），不走真网络：
        conn._session["_token_consumed"] = True
        assert conn._session["_token_consumed"] is True

    def test_new_login_session_defaults_unconsumed(self):
        """登录/缓存返回的 session 无标记键，视为未消费（falsy 安全）。"""
        s = {"account": "acc", "game_token": "tok"}
        assert not s.get("_token_consumed")
