"""ProxyPool 单元测试（不触网，health_check 注入假探测）。"""
from __future__ import annotations

import json

import pytest

from hdata.proxy import ProxyPool

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
            probe=lambda p, t: p != P1)     # P1 假死
        assert results == {P1: False, P2: True}
        assert pool.alive == [P2]

    async def test_probe_exception_counts_dead(self):
        pool = ProxyPool([P1])

        def boom(p, t):
            raise RuntimeError("net down")

        results = await pool.health_check(probe=boom)
        assert results[P1] is False
        assert pool.alive == []
