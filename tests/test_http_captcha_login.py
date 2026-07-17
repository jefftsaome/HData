import sys
from types import SimpleNamespace

import pytest

from hdata.auth.captcha_solver import (
    CaptchaChallenge,
    CaptchaSolution,
    CaptchaSolveError,
    GeepassSolver,
    JfbymSolver,
    SolverInfo,
)


@pytest.mark.asyncio
async def test_api_routes_platform_tokens_without_cross_reuse(monkeypatch):
    from hdata.auth import api

    captured = {}

    async def fake_get_login(**kwargs):
        captured.update(kwargs)
        return {"token": "site-token"}

    monkeypatch.setattr(api, "_get_login", fake_get_login)
    monkeypatch.delenv("CAPTCHA_TOKEN", raising=False)
    monkeypatch.delenv("GEEPASS_TOKEN", raising=False)
    monkeypatch.delenv("JFBYM_TOKEN", raising=False)

    await api.get_login(
        "account",
        "password",
        geepass_token="gp-secret",
        jfbym_token="jf-secret",
    )

    assert captured["geepass_token"] == "gp-secret"
    assert captured["jfbym_token"] == "jf-secret"
    assert captured["captcha_token"] == ""


@pytest.mark.asyncio
async def test_api_legacy_captcha_token_maps_only_to_jfbym(monkeypatch):
    from hdata.auth import api

    captured = {}

    async def fake_get_login(**kwargs):
        captured.update(kwargs)
        return {"token": "site-token"}

    monkeypatch.setattr(api, "_get_login", fake_get_login)
    monkeypatch.delenv("CAPTCHA_TOKEN", raising=False)
    monkeypatch.delenv("GEEPASS_TOKEN", raising=False)
    monkeypatch.delenv("JFBYM_TOKEN", raising=False)
    await api.get_login("account", "password", captcha_token="legacy-secret")

    assert captured["geepass_token"] == ""
    assert captured["jfbym_token"] == "legacy-secret"


@pytest.mark.asyncio
async def test_api_explicit_tokens_override_environment(monkeypatch):
    from hdata.auth import api

    captured = {}

    async def fake_get_login(**kwargs):
        captured.update(kwargs)
        return {"token": "site-token"}

    monkeypatch.setattr(api, "_get_login", fake_get_login)
    monkeypatch.setenv("GEEPASS_TOKEN", "env-gp")
    monkeypatch.setenv("JFBYM_TOKEN", "env-jf")
    monkeypatch.setenv("CAPTCHA_TOKEN", "legacy-env")

    await api.get_login(
        "account",
        "password",
        geepass_token="arg-gp",
        jfbym_token="arg-jf",
    )

    assert captured["geepass_token"] == "arg-gp"
    assert captured["jfbym_token"] == "arg-jf"


@pytest.mark.asyncio
async def test_session_forwards_platform_tokens_as_explicit_http_keywords(monkeypatch):
    from hdata.auth import http_login_v2, session

    captured = {}

    async def fake_http_login(account, password, **kwargs):
        captured.update(account=account, password=password, **kwargs)
        return {"token": "site-token"}

    async def fake_refresh_game_token(account, login_session):
        return "game-token"

    monkeypatch.setattr(session, "get_cached_session", lambda account: None)
    monkeypatch.setattr(session, "refresh_game_token", fake_refresh_game_token)
    monkeypatch.setattr(session, "save_session", lambda account, login_session: None)
    monkeypatch.setattr(http_login_v2, "login", fake_http_login)

    result = await session.get_login(
        "account",
        "password",
        geepass_token="gp-secret",
        jfbym_token="jf-secret",
    )

    assert result["source"] == "http_login"
    assert captured == {
        "account": "account",
        "password": "password",
        "geepass_token": "gp-secret",
        "jfbym_token": "jf-secret",
    }


@pytest.mark.asyncio
async def test_session_http_login_failure_does_not_log_exception_secrets(monkeypatch):
    from hdata.auth import http_login_v2, session

    sentinel_secret = "password=captcha-token=raw-response-w"
    logged_warnings = []

    class FakeLogger:
        def info(self, message):
            pass

        def warning(self, message):
            logged_warnings.append(message)

    class FakeBrowserLogin:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return {"domain": "https://safe.example", "game_token": "game-token"}

    async def fake_http_login(*args, **kwargs):
        raise RuntimeError(sentinel_secret)

    monkeypatch.setattr(session, "get_cached_session", lambda account: None)
    monkeypatch.setattr(session, "get_real_domain", lambda entry_url: "https://safe.example")
    monkeypatch.setattr(session, "save_session", lambda account, login_session: None)
    monkeypatch.setattr(session, "logger", FakeLogger())
    monkeypatch.setattr(http_login_v2, "login", fake_http_login)
    monkeypatch.setitem(
        sys.modules,
        "hdata.auth.browser_login",
        SimpleNamespace(GameBrowserLogin=FakeBrowserLogin),
    )

    result = await session.get_login(
        "account",
        "password",
        geepass_token="geepass-token",
    )

    assert result["source"] == "browser_login"
    assert all(sentinel_secret not in message for message in logged_warnings)


def make_challenge() -> CaptchaChallenge:
    return CaptchaChallenge(
        lot_number="0123456789abcdef0123456789abcdef",
        payload="payload",
        process_token="process",
        bg_url="https://example.invalid/bg.jpg",
        ques_urls=["q1", "q2", "q3"],
        captcha_id="captcha-id",
        pow_detail={"hashfunc": "md5", "version": "1", "bits": 0, "datetime": "date"},
    )


class FakeSolver:
    def __init__(self, name, calls, error=None):
        self.name = name
        self.calls = calls
        self.error = error

    def info(self):
        return SolverInfo(name=self.name, type_code="test", avg_latency_ms=0)

    async def solve(self, challenge):
        self.calls.append(self.name)
        if self.error:
            raise self.error
        return CaptchaSolution(
            coords="10,20|30,40|50,60",
            pts=[[10, 20], [30, 40], [50, 60]],
        )


@pytest.mark.asyncio
async def test_solver_chain_falls_back_in_order():
    from hdata.auth.http_login_v2 import _solve_captcha

    calls = []
    solvers = [
        FakeSolver("geepass", calls, CaptchaSolveError("geepass", "failed")),
        FakeSolver("jfbym", calls),
    ]

    solution = await _solve_captcha(make_challenge(), solvers)

    assert calls == ["geepass", "jfbym"]
    assert solution.solver_name == "jfbym"


@pytest.mark.asyncio
async def test_solver_error_redacts_secret_values():
    from hdata.auth.http_login_v2 import _solve_captcha

    secret = "never-print-this-token"
    solver = FakeSolver(
        "geepass",
        [],
        CaptchaSolveError("geepass", "request rejected", raw_error=f"token={secret}"),
    )

    with pytest.raises(CaptchaSolveError) as exc_info:
        await _solve_captcha(make_challenge(), [solver])

    assert secret not in str(exc_info.value)


@pytest.mark.asyncio
async def test_login_solves_each_challenge_once_after_verify_failure(monkeypatch):
    from hdata.auth import http_login_v2

    fetched_lots = iter(["lot-a-0123456789", "lot-b-0123456789"])
    solved_lots = []

    def fake_fetch():
        lot = next(fetched_lots)
        return {
            "lot_number": lot,
            "payload": f"payload-{lot}",
            "process_token": f"process-{lot}",
            "bg_url": "bg",
            "ques_urls": ["q1", "q2", "q3"],
            "pow_detail": {"hashfunc": "md5", "version": "1", "bits": 0, "datetime": "date"},
        }

    async def fake_solve(challenge, solvers):
        solved_lots.append(challenge.lot_number)
        return CaptchaSolution(
            coords="10,20|30,40|50,60",
            pts=[[10, 20], [30, 40], [50, 60]],
            solver_name="geepass",
        )

    async def fake_verify(load_data, coords):
        raise http_login_v2.VerifyError("fail", fail_count=1)

    monkeypatch.setattr(http_login_v2, "_get_domain", lambda: "https://example.invalid")
    monkeypatch.setattr(http_login_v2, "_fetch_captcha", fake_fetch)
    monkeypatch.setattr(http_login_v2, "_build_solvers", lambda *args: [FakeSolver("geepass", [])])
    monkeypatch.setattr(http_login_v2, "_solve_captcha", fake_solve)
    monkeypatch.setattr(http_login_v2, "_verify_captcha", fake_verify)

    result = await http_login_v2.login(
        "account",
        "password",
        geepass_token="gp-secret",
        max_retries=2,
    )

    assert result is None
    assert solved_lots == ["lot-a-0123456789", "lot-b-0123456789"]


@pytest.mark.asyncio
async def test_jfbym_solver_redacts_platform_response_values(monkeypatch):
    sentinel = "jfbym-platform-secret"

    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda *args, **kwargs: SimpleNamespace(content=b"image"),
    )
    monkeypatch.setattr(
        "curl_cffi.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"code": 40001, "msg": sentinel, "token": sentinel}
        ),
    )

    with pytest.raises(CaptchaSolveError) as exc_info:
        await JfbymSolver("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == "stage=response attempt=1 code=40001"


@pytest.mark.asyncio
async def test_geepass_solver_redacts_network_exception_values(monkeypatch):
    sentinel = "geepass-network-secret"

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda *args, **kwargs: SimpleNamespace(content=b"image"),
    )
    monkeypatch.setattr(
        "curl_cffi.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )
    monkeypatch.setattr("asyncio.sleep", no_sleep)

    with pytest.raises(CaptchaSolveError) as exc_info:
        await GeepassSolver("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == "stage=submit attempt=3 exception=RuntimeError"


class FakeResponse:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_verify_fail_raises_typed_error(monkeypatch):
    from hdata.auth import http_login_v2

    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse(
            'botion_cb({"data":{"result":"fail","fail_count":2}})'
        ),
    )

    with pytest.raises(http_login_v2.VerifyError) as exc_info:
        await http_login_v2._verify_captcha(
            {
                "lot_number": "lot-secret",
                "payload": "payload-secret",
                "process_token": "process-secret",
            },
            "10,20|30,40|50,60",
        )

    assert exc_info.value.result == "fail"
    assert exc_info.value.fail_count == 2
    assert "payload-secret" not in str(exc_info.value)
    assert "process-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_verify_rejects_invalid_jsonp(monkeypatch):
    from hdata.auth import http_login_v2

    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse("not-jsonp"),
    )

    with pytest.raises(http_login_v2.VerifyError, match="invalid_jsonp"):
        await http_login_v2._verify_captcha(
            {"lot_number": "lot", "payload": "payload", "process_token": "process"},
            "10,20|30,40|50,60",
        )


@pytest.mark.asyncio
async def test_verify_requires_complete_seccode(monkeypatch):
    from hdata.auth import http_login_v2

    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse(
            'botion_cb({"data":{"result":"success","seccode":{"pass_token":"x"}}})'
        ),
    )

    with pytest.raises(http_login_v2.VerifyError, match="incomplete_seccode"):
        await http_login_v2._verify_captcha(
            {"lot_number": "lot", "payload": "payload", "process_token": "process"},
            "10,20|30,40|50,60",
        )


def assert_safe_verify_error(error, sentinel):
    assert sentinel not in str(error)
    assert sentinel not in error.result
    assert sentinel not in error.reason
    assert sentinel not in str(error.diagnostics)
    assert error.__cause__ is None


@pytest.mark.asyncio
async def test_verify_network_error_does_not_chain_raw_exception(monkeypatch):
    from hdata.auth import http_login_v2

    sentinel = "network-url-secret"
    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    with pytest.raises(http_login_v2.VerifyError) as exc_info:
        await http_login_v2._verify_captcha(
            {"lot_number": "lot", "payload": "payload", "process_token": "process"},
            "10,20|30,40|50,60",
        )

    assert exc_info.value.result == "network_error"
    assert_safe_verify_error(exc_info.value, sentinel)


@pytest.mark.asyncio
async def test_verify_malformed_json_does_not_chain_raw_body(monkeypatch):
    from hdata.auth import http_login_v2

    sentinel = "malformed-json-secret"
    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse(f"botion_cb({{bad:{sentinel}}})"),
    )

    with pytest.raises(http_login_v2.VerifyError) as exc_info:
        await http_login_v2._verify_captcha(
            {"lot_number": "lot", "payload": "payload", "process_token": "process"},
            "10,20|30,40|50,60",
        )

    assert exc_info.value.result == "invalid_jsonp"
    assert_safe_verify_error(exc_info.value, sentinel)


@pytest.mark.asyncio
async def test_verify_redacts_unexpected_result_and_invalid_fail_count(monkeypatch):
    from hdata.auth import http_login_v2

    sentinel = "unexpected-result-secret"
    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse(
            f'botion_cb({{"data":{{"result":"{sentinel}","fail_count":"{sentinel}"}}}})'
        ),
    )

    with pytest.raises(http_login_v2.VerifyError) as exc_info:
        await http_login_v2._verify_captcha(
            {"lot_number": "lot", "payload": "payload", "process_token": "process"},
            "10,20|30,40|50,60",
        )

    assert exc_info.value.result == "unexpected_result"
    assert exc_info.value.fail_count == 0
    assert_safe_verify_error(exc_info.value, sentinel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_result"),
    [
        ("[]", "invalid_jsonp"),
        ('{"data":[]}', "invalid_jsonp"),
        ('{"data":{"result":"success","seccode":[]}}', "incomplete_seccode"),
    ],
)
async def test_verify_rejects_nonmapping_protocol_values(monkeypatch, body, expected_result):
    from hdata.auth import http_login_v2

    sentinel = "nonmapping-body-secret"
    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args, **kwargs: "safe-w")
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse(f"botion_cb({body})"),
    )

    with pytest.raises(http_login_v2.VerifyError) as exc_info:
        await http_login_v2._verify_captcha(
            {"lot_number": "lot", "payload": "payload", "process_token": "process"},
            "10,20|30,40|50,60",
        )

    assert exc_info.value.result == expected_result
    assert_safe_verify_error(exc_info.value, sentinel)


@pytest.mark.asyncio
async def test_login_verify_diagnostics_redact_server_response(monkeypatch, capsys):
    from hdata.auth import http_login_v2

    sentinel = "server-response-secret"

    def fake_generate_w(*args, diagnostics=None, **kwargs):
        diagnostics.update(e_obj_fields=["safe_field"], e_obj_bytes=12)
        return "safe-w"

    async def fake_solve(challenge, solvers):
        return CaptchaSolution("10,20|30,40|50,60", [[10, 20], [30, 40], [50, 60]])

    monkeypatch.setattr(http_login_v2, "_get_domain", lambda: "https://example.invalid")
    monkeypatch.setattr(
        http_login_v2,
        "_fetch_captcha",
        lambda: {
            "lot_number": "challenge-prefix",
            "payload": "payload",
            "process_token": "process",
            "bg_url": "bg",
            "ques_urls": ["q1", "q2", "q3"],
        },
    )
    monkeypatch.setattr(http_login_v2, "_build_solvers", lambda *args: [FakeSolver("test", [])])
    monkeypatch.setattr(http_login_v2, "_solve_captcha", fake_solve)
    monkeypatch.setattr(http_login_v2, "generate_w", fake_generate_w)
    monkeypatch.setattr(
        http_login_v2.cr,
        "get",
        lambda *args, **kwargs: FakeResponse(
            f'botion_cb({{"data":{{"result":"{sentinel}"}}}})'
        ),
    )

    assert await http_login_v2.login("account", "password", geepass_token="token", max_retries=1) is None
    output = capsys.readouterr().out
    assert sentinel not in output
    assert "result=unexpected_result" in output
    assert "e_obj_bytes=12" in output
    assert "e_obj_fields=safe_field" in output


@pytest.mark.asyncio
async def test_jfbym_solver_redacts_network_exception_values(monkeypatch):
    sentinel = "jfbym-network-secret"

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda *args, **kwargs: SimpleNamespace(content=b"image"),
    )
    monkeypatch.setattr(
        "curl_cffi.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )
    monkeypatch.setattr("asyncio.sleep", no_sleep)

    with pytest.raises(CaptchaSolveError) as exc_info:
        await JfbymSolver("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == "stage=submit attempt=6 exception=RuntimeError"


@pytest.mark.asyncio
async def test_geepass_solver_redacts_platform_response_values(monkeypatch):
    sentinel = "geepass-platform-secret"

    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda *args, **kwargs: SimpleNamespace(content=b"image"),
    )
    monkeypatch.setattr(
        "curl_cffi.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"code": 40001, "msg": sentinel, "token": sentinel}
        ),
    )

    with pytest.raises(CaptchaSolveError) as exc_info:
        await GeepassSolver("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == "stage=response attempt=1 code=40001"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("solver_class", "failing_url", "expected_metadata"),
    [
        (JfbymSolver, "https://example.invalid/bg.jpg", "stage=background_download exception=RuntimeError"),
        (JfbymSolver, "q1", "stage=reference_download attempt=1 exception=RuntimeError"),
        (GeepassSolver, "https://example.invalid/bg.jpg", "stage=background_download exception=RuntimeError"),
        (GeepassSolver, "q1", "stage=reference_download attempt=1 exception=RuntimeError"),
    ],
)
async def test_solver_redacts_image_download_exception(
    monkeypatch, solver_class, failing_url, expected_metadata
):
    sentinel = f"{solver_class.__name__}-download-secret"

    def fake_get(url, **kwargs):
        if url == failing_url:
            raise RuntimeError(sentinel)
        return SimpleNamespace(content=b"image")

    monkeypatch.setattr("curl_cffi.requests.get", fake_get)

    with pytest.raises(CaptchaSolveError) as exc_info:
        await solver_class("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == expected_metadata


@pytest.mark.asyncio
async def test_jfbym_solver_redacts_malformed_success_data(monkeypatch):
    sentinel = "jfbym-malformed-coords-secret"

    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda *args, **kwargs: SimpleNamespace(content=b"image"),
    )
    monkeypatch.setattr(
        "curl_cffi.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"code": 10000, "data": {"data": sentinel}}
        ),
    )

    with pytest.raises(CaptchaSolveError) as exc_info:
        await JfbymSolver("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == "stage=response_parse attempt=1 code=10000 exception=ValueError"


@pytest.mark.asyncio
async def test_geepass_solver_redacts_malformed_success_data(monkeypatch):
    sentinel = "geepass-malformed-target-secret"

    monkeypatch.setattr(
        "curl_cffi.requests.get",
        lambda *args, **kwargs: SimpleNamespace(content=b"image"),
    )
    monkeypatch.setattr(
        "curl_cffi.requests.post",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {
                "code": 10000,
                "data": {"data": {"targets": [[sentinel, 0, 10, 10]] * 3}},
            }
        ),
    )

    with pytest.raises(CaptchaSolveError) as exc_info:
        await GeepassSolver("api-token").solve(make_challenge())

    assert sentinel not in str(exc_info.value)
    assert sentinel not in exc_info.value.raw_error
    assert exc_info.value.raw_error == "stage=response_parse attempt=1 code=10000 exception=TypeError"
