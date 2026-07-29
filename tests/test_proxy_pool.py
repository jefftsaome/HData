"""ProxyPool 单元测试（不触网，health_check 注入假探测）。

_probe_sync 的出口 IP 解析用 curl_cffi 打桩测试；测试代理一律用
RFC 5737 文档保留地址（203.0.113.x / 198.51.100.x / 192.0.2.x）。
"""
from __future__ import annotations

import json

import pytest

from hdata.proxy import ProxyPool, _probe_sync

P1 = "http://u1:p1@1.1.1.1:8001"
P2 = "http://u2:p2@2.2.2.2:8002"
P3 = "http://u3:p3@3.3.3.3:8003"


class TestAssign:
    def test_balanced_round_robin(self):
        pool = ProxyPool([P1, P2, P3], cap_per_proxy=2)
        m = pool.assign(["a", "b", "c", "d", "e"])
        loads = sorted(
            sum(1 for v in m.values() if v == p) for p in (P1, P2, P3))
        assert loads == [1, 2, 2]          # 均衡：2/2/1
        assert None not in m.values()

    def test_sticky_binding(self):
        pool = ProxyPool([P1, P2], cap_per_proxy=5)
        m1 = pool.assign(["a", "b"])
        m2 = pool.assign(["a", "b"])
        assert m1 == m2                    # 粘性：重复分配结果不变

    def test_cap_overflow_returns_none(self):
        pool = ProxyPool([P1], cap_per_proxy=2)
        m = pool.assign(["a", "b", "c"])
        assert m["c"] is None              # 容量不足的账号得 None
        assert m["a"] == P1 and m["b"] == P1

    def test_new_account_fills_least_loaded(self):
        pool = ProxyPool([P1, P2], cap_per_proxy=3)
        pool.assign(["a"])                 # a → P1
        m = pool.assign(["b"])             # b 应去负载更小的 P2
        assert m["b"] == P2


class TestFailure:
    def test_mark_dead_unbinds_and_reassign(self):
        pool = ProxyPool([P1, P2], cap_per_proxy=2)
        pool.assign(["a", "b"])            # a→P1 b→P2（均衡）
        affected = pool.mark_dead(P1)
        assert affected == ["a"]
        m = pool.assign(["a"])             # 重分：a → P2
        assert m["a"] == P2

    def test_dead_proxy_not_reused_for_sticky(self):
        pool = ProxyPool([P1, P2], cap_per_proxy=2)
        pool.assign(["a"])
        pool.mark_dead(P1)
        m = pool.assign(["a"])
        assert m["a"] == P2                # 旧绑定失效后不复活

    def test_all_dead_gives_none(self):
        pool = ProxyPool([P1], cap_per_proxy=1)
        pool.mark_dead(P1)
        assert pool.assign(["x"])["x"] is None


class TestLoad:
    def test_from_file_strings(self, tmp_path):
        f = tmp_path / "proxies.json"
        f.write_text(json.dumps([P1, P2]), encoding="utf-8")
        pool = ProxyPool.from_file(f)
        assert pool.alive == [P1, P2]

    def test_from_file_dicts(self, tmp_path):
        f = tmp_path / "proxies.json"
        f.write_text(json.dumps(
            [{"name": "x", "url": P1}, {"url": P2}]), encoding="utf-8")
        pool = ProxyPool.from_file(f)
        assert pool.alive == [P1, P2]

    def test_from_file_bad_item(self, tmp_path):
        f = tmp_path / "proxies.json"
        f.write_text(json.dumps([{"name": "x"}]), encoding="utf-8")
        with pytest.raises(ValueError):
            ProxyPool.from_file(f)

    def test_dedup(self):
        pool = ProxyPool([P1, P1, P2])
        assert pool.alive == [P1, P2]


class TestHealthCheck:
    async def test_dead_marked(self):
        pool = ProxyPool([P1, P2], cap_per_proxy=5)
        results = await pool.health_check(
            probe=lambda p, t: p != P1)     # P1 假死（旧式 bool 探测→IP 记 None）
        assert results == {P1: {"ok": False, "ip": None},
                           P2: {"ok": True, "ip": None}}
        assert pool.alive == [P2]

    async def test_probe_exception_counts_dead(self):
        pool = ProxyPool([P1])

        def boom(p, t):
            raise RuntimeError("net down")

        results = await pool.health_check(probe=boom)
        assert results[P1]["ok"] is False
        assert pool.alive == []


class TestExitIP:
    async def test_tuple_probe_records_exit_ip(self):
        pool = ProxyPool([P1, P2], cap_per_proxy=5)
        results = await pool.health_check(
            probe=lambda p, t: (True, "9.9.9.9") if p == P1
            else (False, None))
        assert results[P1] == {"ok": True, "ip": "9.9.9.9"}
        assert results[P2] == {"ok": False, "ip": None}
        assert pool.exit_ip(P1) == "9.9.9.9"
        assert pool.exit_ip(P2) is None
        assert pool.exit_ips == {P1: "9.9.9.9", P2: None}

    async def test_alive_but_ip_unparseable_records_none(self):
        pool = ProxyPool([P1])
        results = await pool.health_check(probe=lambda p, t: (True, None))
        assert results[P1] == {"ok": True, "ip": None}
        assert pool.alive == [P1]       # 200 但解析不到 IP：算存活、IP 记 None


class TestProbeSyncParsing:
    """_probe_sync 出口 IP 解析（curl_cffi 打桩，不触网）。"""

    FAKE_PROXY = "http://u:p@203.0.113.9:8011"     # RFC 5737 文档地址

    @staticmethod
    def _install(monkeypatch, handler):
        from curl_cffi import requests as _rq
        monkeypatch.setattr(_rq, "get", handler)

    def test_ipip_text_endpoint(self, monkeypatch):
        class R:
            status_code = 200
            text = "当前 IP：9.8.7.6  来自于：中国 广东 广州 电信"

        self._install(monkeypatch, lambda url, timeout, proxies: R())
        ok, ip = _probe_sync(self.FAKE_PROXY, 1.0)
        assert (ok, ip) == (True, "9.8.7.6")

    def test_httpbin_json_endpoint(self, monkeypatch):
        class R:
            status_code = 200
            text = '{"origin": "9.8.7.7"}'

            def json(self):
                return {"origin": "9.8.7.7"}

        def fake(url, timeout, proxies):
            if "ipip" in url:
                raise RuntimeError("ipip down")    # 首端点失败回落 httpbin
            return R()

        self._install(monkeypatch, fake)
        ok, ip = _probe_sync(self.FAKE_PROXY, 1.0)
        assert (ok, ip) == (True, "9.8.7.7")

    def test_200_unparseable_alive_ip_none(self, monkeypatch):
        class R:
            status_code = 200
            text = "no ip here"

            def json(self):
                return {}

        self._install(monkeypatch, lambda url, timeout, proxies: R())
        ok, ip = _probe_sync(self.FAKE_PROXY, 1.0)
        assert (ok, ip) == (True, None)

    def test_all_endpoints_fail(self, monkeypatch):
        def fake(url, timeout, proxies):
            raise RuntimeError("dead")

        self._install(monkeypatch, fake)
        ok, ip = _probe_sync(self.FAKE_PROXY, 1.0)
        assert (ok, ip) == (False, None)


class TestPreferredIds:
    """proxy_id 显式绑定（2026-07-29 粘性出口语义）。"""

    def _pool_with_ids(self, tmp_path):
        f = tmp_path / "proxies.json"
        f.write_text(json.dumps([
            {"id": "exit-1", "name": "出口一", "url": P1},
            {"id": "exit-2", "name": "出口二", "url": P2},
            {"id": "exit-3", "url": P3},          # name 可缺
        ]), encoding="utf-8")
        return ProxyPool.from_file(f, cap_per_proxy=1)

    def test_preferred_binding_wins(self, tmp_path):
        pool = self._pool_with_ids(tmp_path)
        m = pool.assign(["a", "b"], preferred_ids={"a": "exit-2"})
        assert m["a"] == P2                  # 显式绑定优先于均衡
        assert m["b"] == P1                  # 未绑定的自动落位

    def test_preferred_ignores_cap(self, tmp_path):
        pool = self._pool_with_ids(tmp_path)  # cap=1
        m = pool.assign(["a", "b"],
                        preferred_ids={"a": "exit-1", "b": "exit-1"})
        assert m["a"] == P1 and m["b"] == P1  # 显式绑定不受 cap 限制

    def test_preferred_dead_exit_not_migrated(self, tmp_path):
        pool = self._pool_with_ids(tmp_path)
        pool.mark_dead(P2)
        m = pool.assign(["a", "b"], preferred_ids={"a": "exit-2"})
        assert m["a"] is None                # 绑死出口不静默迁移
        assert m["b"] == P1                  # 未绑定账号不受影响

    def test_preferred_unknown_id(self, tmp_path):
        pool = self._pool_with_ids(tmp_path)
        m = pool.assign(["a"], preferred_ids={"a": "exit-99"})
        assert m["a"] is None

    def test_id_url_lookup(self, tmp_path):
        pool = self._pool_with_ids(tmp_path)
        assert pool.url_for_id("exit-3") == P3
        assert pool.url_for_id("nope") is None
        assert pool.id_of(P2) == "exit-2"
        assert pool.id_of("http://x@9.9.9.9:1") is None

    def test_default_id_when_missing(self, tmp_path):
        f = tmp_path / "proxies.json"
        f.write_text(json.dumps([{"name": "x", "url": P1}]),
                     encoding="utf-8")
        pool = ProxyPool.from_file(f)
        assert pool.url_for_id("exit-1") == P1   # 缺 id 退化为序号
