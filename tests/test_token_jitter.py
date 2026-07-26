"""token 错峰刷新（session.token_refresh_due）单元测试。

TTL 75%~90% 区间按 (账号, iat, exp) 哈希确定性取点；到点主动走
venue/launch 廉价刷新，把 30 账号的到期时刻散开（防集体重登）。
"""
from __future__ import annotations

import base64
import json
import time

from hdata.auth.session import (_REFRESH_JITTER_HI, _REFRESH_JITTER_LO,
                                _refresh_jitter_point, token_refresh_due)


def _jwt(iat: int, exp: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"iat": iat, "exp": exp}).encode()
    ).rstrip(b"=").decode()
    return f"hdr.{payload}.sig"


def test_fresh_token_not_due():
    now = int(time.time())
    tok = _jwt(now - 100, now + 900)        # TTL 才过 10%（远未到 75%）
    assert token_refresh_due("acc1", tok) is False


def test_old_token_due():
    now = int(time.time())
    # TTL=1000，已过 950：抖动点最晚也在 90%（900），必然到点
    tok = _jwt(now - 950, now + 50)
    assert token_refresh_due("acc1", tok) is True


def test_jitter_point_within_band_and_deterministic():
    iat, exp = 1_000_000, 1_010_000          # TTL=10000
    for acc in ("a", "b", "c"):
        p = _refresh_jitter_point(acc, iat, exp)
        frac = (p - iat) / (exp - iat)
        assert _REFRESH_JITTER_LO <= frac <= _REFRESH_JITTER_HI
        assert p == _refresh_jitter_point(acc, iat, exp)   # 确定性


def test_jitter_points_spread_across_accounts():
    iat, exp = 1_000_000, 1_086_400          # TTL=24h
    points = {_refresh_jitter_point(f"acc{i:02d}", iat, exp)
              for i in range(30)}
    assert len(points) > 20                  # 30 账号散开，绝不撞车


def test_unparseable_token_treated_due():
    assert token_refresh_due("acc1", "not-a-jwt") is True
    assert token_refresh_due("acc1", "") is True


def test_missing_iat_treated_due():
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + 86400}).encode()
    ).rstrip(b"=").decode()
    assert token_refresh_due("acc1", f"h.{payload}.s") is True
