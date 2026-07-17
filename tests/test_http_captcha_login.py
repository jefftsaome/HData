import pytest


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
