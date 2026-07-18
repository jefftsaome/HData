### Task 1: Public token resolution and propagation

**Files:**
- Create: `tests/test_http_captcha_login.py`
- Modify: `hdata/auth/api.py:18-95`
- Modify: `hdata/auth/session.py:408-490`

**Interfaces:**
- Produces: `api.get_login(..., captcha_token: str = "", geepass_token: str = "", jfbym_token: str = "") -> dict`.
- Produces: `session.get_login(..., captcha_token: str = "", geepass_token: str = "", jfbym_token: str = "") -> dict`.
- Compatibility: `captcha_token` and `CAPTCHA_TOKEN` populate only `jfbym_token` when no explicit jfbym token exists.

- [ ] **Step 1: Write failing API token-routing tests**

Add these tests to `tests/test_http_captcha_login.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_http_captcha_login.py -v`

Expected: FAIL because `api.get_login()` and `session.get_login()` do not yet accept and propagate both explicit token arguments.

- [ ] **Step 3: Implement minimal public resolution**

Change `hdata/auth/api.py` so its signature and resolution are:

```python
async def get_login(
    username: str = "",
    password: str = "",
    *,
    captcha_token: str = "",
    geepass_token: str = "",
    jfbym_token: str = "",
    force_refresh: bool = False,
) -> dict:
    user = username or os.getenv("LEYU_USER", "")
    pwd = password or os.getenv("LEYU_PWD", "")
    gp_token = geepass_token or os.getenv("GEEPASS_TOKEN", "")
    jf_token = (
        jfbym_token
        or captcha_token
        or os.getenv("JFBYM_TOKEN", "")
        or os.getenv("CAPTCHA_TOKEN", "")
    )
    # Keep the existing username/password validation here.
    return await _get_login(
        account=user,
        password=pwd,
        captcha_token="",
        geepass_token=gp_token,
        jfbym_token=jf_token,
        force_refresh=force_refresh,
    )
```

Change `hdata/auth/session.py` to accept the same three keyword arguments and call HTTP login with explicit keywords:

```python
async def get_login(
    account: str,
    password: str = "",
    entry_url: str = "",
    force_refresh: bool = False,
    captcha_token: str = "",
    geepass_token: str = "",
    jfbym_token: str = "",
) -> dict:
    legacy_jfbym_token = jfbym_token or captcha_token
    # Existing cache logic remains unchanged.
    if password and (geepass_token or legacy_jfbym_token):
        http_session = await http_login_v2(
            account,
            password,
            geepass_token=geepass_token,
            jfbym_token=legacy_jfbym_token,
        )
```

Preserve the existing refresh, cache, and browser fallback behavior around this call.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests/test_http_captcha_login.py -v`

Expected: PASS for the three token-resolution tests.

- [ ] **Step 5: Commit the token-routing boundary**

```bash
git add tests/test_http_captcha_login.py hdata/auth/api.py hdata/auth/session.py
git commit -m "fix: route captcha platform tokens independently"
```

---

