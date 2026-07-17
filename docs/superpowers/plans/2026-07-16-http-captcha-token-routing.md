# HTTP Captcha Token Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make geepass and jfbym use independent credentials, make the solver fallback deterministic, and expose reproducible `e_obj`/verify diagnostics without calling real captcha services in tests.

**Architecture:** Public API and session layers pass explicit `geepass_token` and `jfbym_token` values into the HTTP login layer. The HTTP login layer builds an ordered `CaptchaSolver` chain, performs one solve per challenge, and reports typed stage failures. `geetest_signer.py` separates deterministic `e_obj` construction from random encryption so protocol structure can be regression-tested independently.

**Tech Stack:** Python 3.13.5, pytest 9, pytest-asyncio, dataclasses, curl-cffi, PyCryptodome.

## Global Constraints

- `captcha_token` remains backward-compatible and is interpreted only as a jfbym token.
- `GEEPASS_TOKEN` and `JFBYM_TOKEN` are the platform-specific environment variables; legacy `CAPTCHA_TOKEN` is only a jfbym fallback.
- Never log or include passwords, captcha tokens, image Base64, full authentication responses, or full generated `w` values in exceptions.
- Tests must not call geepass, jfbym, Botion/GeeTest, or the target login service.
- A failed `verify` starts a new outer attempt with a new challenge; it does not solve the same challenge a second time.
- Do not restore, stage, or modify unrelated working-tree changes.
- Do not invent missing `e_obj` fields. Deterministic tests cover only confirmed structure and captured evidence available in the repository.

## File Structure

- Create `tests/test_http_captcha_login.py`: focused async tests for token resolution, solver ordering, retry behavior, verify failures, and redaction.
- Create `tests/test_geetest_signer.py`: deterministic unit tests for coordinates, PoW input, JSON structure, and encryption boundary.
- Modify `hdata/auth/api.py`: resolve public arguments and environment variables without cross-platform token reuse.
- Modify `hdata/auth/session.py`: propagate explicit platform tokens into HTTP login and retain legacy jfbym compatibility.
- Modify `hdata/auth/http_login_v2.py`: build the solver chain, return platform-aware solutions, eliminate same-challenge re-solving, and raise typed verify errors.
- Modify `hdata/auth/geetest_signer.py`: expose deterministic `build_e_obj()` and `serialize_e_obj()` helpers used by `generate_w()`.
- Modify `test_get_login.py`: expose separate manual-test CLI flags without embedding token values.
- Modify `hdata/auth/__init__.py` and `hdata/auth/README.md`: document the new API/environment-variable contract and current verify limitation.

---

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

### Task 2: Ordered solver chain and one solve per challenge

**Files:**
- Modify: `tests/test_http_captcha_login.py`
- Modify: `hdata/auth/http_login_v2.py:34-91,271-359`
- Modify: `hdata/auth/captcha_solver.py:54-66`

**Interfaces:**
- Consumes: explicit `geepass_token` and `jfbym_token` from Task 1.
- Produces: `_build_solvers(geepass_token: str, jfbym_token: str) -> list[CaptchaSolver]`.
- Produces: `_solve_captcha(challenge: CaptchaChallenge, solvers: list[CaptchaSolver]) -> CaptchaSolution`.
- Produces: `CaptchaSolution.solver_name: str`, populated by `_solve_captcha()` from `solver.info().name`.
- Produces: minimal `VerifyError(result: str, fail_count: int = 0)` used by the outer retry loop; Task 4 extends it with redacted diagnostics.

- [ ] **Step 1: Write failing solver-chain tests**

Append:

```python
from hdata.auth.captcha_solver import (
    CaptchaChallenge,
    CaptchaSolution,
    CaptchaSolveError,
    SolverInfo,
)


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
```

Add an outer-login test that replaces `_fetch_captcha`, `_solve_captcha`, and `_verify_captcha`; make `_verify_captcha` fail twice with `max_retries=2`, then assert `_solve_captcha` receives two distinct challenge instances and is called exactly twice, not four times.

Use this concrete test:

```python
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
```

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_http_captcha_login.py -v`

Expected: FAIL because `_solve_captcha()` accepts tokens rather than solvers, `CaptchaSolution` has no `solver_name`, and login currently solves a failed challenge twice.

- [ ] **Step 3: Implement the ordered solver chain**

Add a defaulted field in `hdata/auth/captcha_solver.py`:

```python
@dataclass
class CaptchaSolution:
    coords: str
    pts: list[list[int]]
    raw_response: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    solver_name: str = ""
```

In `hdata/auth/http_login_v2.py`, build solvers without guessing credentials:

```python
class VerifyError(RuntimeError):
    def __init__(self, result: str, fail_count: int = 0):
        self.result = result
        self.fail_count = fail_count
        super().__init__(f"verify {result}; fail_count={fail_count}")


def _build_solvers(geepass_token: str, jfbym_token: str):
    solvers = []
    if geepass_token:
        solvers.append(GeepassSolver(api_token=geepass_token))
    if jfbym_token:
        solvers.append(JfbymSolver(api_token=jfbym_token))
    return solvers


async def _solve_captcha(challenge, solvers):
    failures = []
    for solver in solvers:
        name = solver.info().name
        try:
            solution = await solver.solve(challenge)
            solution.solver_name = name
            return solution
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
    raise CaptchaSolveError("solver-chain", "all solvers failed", "; ".join(failures))
```

Resolve legacy input only as jfbym in `login()`:

```python
async def login(
    user: str,
    pwd: str,
    captcha_token: str = "",
    *,
    geepass_token: str = "",
    jfbym_token: str = "",
    max_retries: int = 3,
) -> Optional[dict]:
gp_token = geepass_token or os.getenv("GEEPASS_TOKEN", "")
jf_token = (
    jfbym_token
    or captcha_token
    or os.getenv("JFBYM_TOKEN", "")
    or os.getenv("CAPTCHA_TOKEN", "")
)
solvers = _build_solvers(gp_token, jf_token)
```

Remove the second `_solve_captcha()` call after a verify failure. Let the outer retry loop fetch a new challenge.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests/test_http_captcha_login.py -v`

Expected: PASS, including ordered fallback, redaction, and exactly one solve per challenge.

- [ ] **Step 5: Commit the solver-chain behavior**

```bash
git add tests/test_http_captcha_login.py hdata/auth/http_login_v2.py hdata/auth/captcha_solver.py
git commit -m "fix: make captcha solver fallback deterministic"
```

---

### Task 3: Deterministic `e_obj` construction

**Files:**
- Create: `tests/test_geetest_signer.py`
- Modify: `hdata/auth/geetest_signer.py:148-215`

**Interfaces:**
- Produces: `build_e_obj(load_data: dict, captcha_id: str, coords: str, *, passtime: int | None = None, pow_nonce: str | None = None) -> dict`.
- Produces: `serialize_e_obj(e_obj: dict) -> str` using `json.dumps(..., separators=(",", ":"), ensure_ascii=False)`.
- `generate_w(..., diagnostics: dict | None = None)` continues to return `str`, delegates plaintext construction to these functions, and optionally fills only `e_obj_fields` and `e_obj_bytes`.

- [ ] **Step 1: Write failing deterministic signer tests**

Create `tests/test_geetest_signer.py`:

```python
import json

import pytest

from hdata.auth.geetest_signer import build_e_obj, serialize_e_obj


CAPTCHA_ID = "eaffad4f65a38a259ae369faf0c2f1a3"
LOAD_DATA = {
    "lot_number": "0123456789abcdef0123456789abcdef",
    "pow_detail": {
        "hashfunc": "md5",
        "version": "1",
        "bits": 0,
        "datetime": "2026-07-16T00:00:00Z",
    },
}


def test_build_e_obj_is_deterministic_with_injected_values():
    obj = build_e_obj(
        LOAD_DATA,
        CAPTCHA_ID,
        "10,20|30,40|50,60",
        passtime=2345,
        pow_nonce="0123456789abcdef",
    )

    assert obj["lot_number"] == LOAD_DATA["lot_number"]
    assert obj["userresponse"] == [[10, 20], [30, 40], [50, 60]]
    assert obj["passtime"] == 2345
    assert obj["pow_msg"].endswith("|0123456789abcdef")
    assert obj["pow_sign"] == __import__("hashlib").md5(obj["pow_msg"].encode()).hexdigest()
    assert set(obj) >= {
        "pow_msg", "pow_sign", "biht", "em", "gee_guard", "geetest",
        "lang", "lot_number", "userresponse", "passtime",
    }


def test_serialize_e_obj_uses_compact_stable_json():
    obj = build_e_obj(
        LOAD_DATA,
        CAPTCHA_ID,
        "10,20|30,40|50,60",
        passtime=2345,
        pow_nonce="0123456789abcdef",
    )
    text = serialize_e_obj(obj)

    assert text == json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    assert ": " not in text


@pytest.mark.parametrize(
    "coords",
    ["", "10,20", "10,20|bad|50,60", "10,20|30,40|50,60|70,80"],
)
def test_build_e_obj_rejects_invalid_coordinate_shape(coords):
    with pytest.raises(ValueError, match="coords"):
        build_e_obj(LOAD_DATA, CAPTCHA_ID, coords, passtime=2000, pow_nonce="nonce")


def test_build_e_obj_rejects_missing_pow_fields():
    with pytest.raises(ValueError, match="pow_detail"):
        build_e_obj(
            {"lot_number": LOAD_DATA["lot_number"], "pow_detail": {}},
            CAPTCHA_ID,
            "10,20|30,40|50,60",
            passtime=2000,
            pow_nonce="nonce",
        )
```

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_geetest_signer.py -v`

Expected: collection FAIL because `build_e_obj` and `serialize_e_obj` do not exist.

- [ ] **Step 3: Implement deterministic construction**

Refactor PoW and plaintext construction:

```python
def _generate_pow(lot_number, captcha_id, hash_func, version, bits, date, nonce=None):
    pow_string = f"{version}|{bits}|{hash_func}|{date}|{captcha_id}|{lot_number}||"
    if nonce is not None:
        combined = pow_string + nonce
        return {"pow_msg": combined, "pow_sign": _hash_pow(combined, hash_func)}
    # Preserve the existing random search loop for production calls.


def _hash_pow(value, hash_func):
    if hash_func not in {"md5", "sha1", "sha256"}:
        raise ValueError(f"unsupported pow hashfunc: {hash_func}")
    return getattr(hashlib, hash_func)(value.encode()).hexdigest()


def build_e_obj(load_data, captcha_id, coords, *, passtime=None, pow_nonce=None):
    required = {"hashfunc", "version", "bits", "datetime"}
    pow_detail = load_data.get("pow_detail") or {}
    if not required.issubset(pow_detail):
        raise ValueError("pow_detail is missing required fields")
    try:
        coords_array = [[int(v) for v in point.split(",")] for point in coords.split("|")]
    except (TypeError, ValueError):
        raise ValueError("coords must contain exactly three x,y integer pairs") from None
    if len(coords_array) != 3 or any(len(point) != 2 for point in coords_array):
        raise ValueError("coords must contain exactly three x,y integer pairs")
    return {
        **_generate_pow(
            load_data["lot_number"], captcha_id,
            pow_detail["hashfunc"], pow_detail["version"],
            pow_detail["bits"], pow_detail["datetime"], pow_nonce,
        ),
        **_lot_parser.get_dict(load_data["lot_number"]),
        "biht": "1426265548",
        "em": {"cp": 0, "ek": "11"},
        "gee_guard": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3", "res": "3", "rew": "3", "sep": "3", "snh": "3"},
        "geetest": "captcha",
        "lang": "zh",
        "lot_number": load_data["lot_number"],
        "userresponse": coords_array,
        "passtime": passtime if passtime is not None else random.randint(1500, 3500),
    }


def serialize_e_obj(e_obj):
    return json.dumps(e_obj, separators=(",", ":"), ensure_ascii=False)


def generate_w(load_data, captcha_id, coords, *, diagnostics=None):
    e_obj = build_e_obj(load_data, captcha_id, coords)
    plaintext = serialize_e_obj(e_obj)
    if diagnostics is not None:
        diagnostics.update(
            e_obj_fields=sorted(e_obj),
            e_obj_bytes=len(plaintext.encode()),
        )
    random_key = _rand_uid()
    encrypted_input = _encrypt_aes(plaintext, random_key)
    encrypted_key = _encrypt_rsa(random_key)
    return binascii.hexlify(encrypted_input).decode() + encrypted_key
```

Make `generate_w()` call `build_e_obj()`, then encrypt `serialize_e_obj(e_obj)` exactly as before.

- [ ] **Step 4: Run signer tests to verify GREEN**

Run: `uv run pytest tests/test_geetest_signer.py -v`

Expected: PASS for deterministic structure, stable JSON, invalid coordinates, and missing PoW fields.

- [ ] **Step 5: Run existing tests for signer regressions**

Run: `uv run pytest tests -v`

Expected: existing suite remains green; no network-dependent tests are newly introduced.

- [ ] **Step 6: Commit the signer seam**

```bash
git add tests/test_geetest_signer.py hdata/auth/geetest_signer.py
git commit -m "refactor: expose deterministic geetest payload builder"
```

---

### Task 4: Typed verify-stage failures and safe diagnostics

**Files:**
- Modify: `tests/test_http_captcha_login.py`
- Modify: `hdata/auth/http_login_v2.py:94-158,314-359`

**Interfaces:**
- Extends: Task 2's `VerifyError` to `VerifyError(result: str, fail_count: int = 0, reason: str = "", *, diagnostics: dict | None = None)` with a redacted message.
- `_verify_captcha(load_data: dict, coords: str) -> dict` returns complete seccode or raises `VerifyError`.
- Login catches `VerifyError`, records only result/fail count/challenge prefix/`e_obj` field names and byte length, and continues to a new challenge.

- [ ] **Step 1: Write failing verify parsing tests**

Append tests using a fake response object and monkeypatch `http_login_v2.cr.get`:

```python
class FakeResponse:
    def __init__(self, text):
        self.text = text


@pytest.mark.asyncio
async def test_verify_fail_raises_typed_error(monkeypatch):
    from hdata.auth import http_login_v2

    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args: "safe-w")
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

    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args: "safe-w")
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

    monkeypatch.setattr(http_login_v2, "generate_w", lambda *args: "safe-w")
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
```

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_http_captcha_login.py -v`

Expected: FAIL because `_verify_captcha()` returns `None` instead of raising a typed error.

- [ ] **Step 3: Implement typed verify errors**

Add:

```python
class VerifyError(RuntimeError):
    def __init__(
        self,
        result: str,
        fail_count: int = 0,
        reason: str = "",
        *,
        diagnostics: dict | None = None,
    ):
        self.result = result
        self.fail_count = fail_count
        self.reason = reason
        self.diagnostics = diagnostics or {}
        super().__init__(f"verify {result}: {reason}; fail_count={fail_count}")
```

Implement JSONP parsing and validation with this structure:

```python
async def _verify_captcha(load_data: dict, coords: str) -> dict:
    diagnostics = {}
    w = generate_w(load_data, CAPTCHA_ID, coords, diagnostics=diagnostics)
    callback = f"botion_{int(time.time() * 1000)}"
    params = {
        "callback": callback,
        "captcha_id": CAPTCHA_ID,
        "client_type": "web",
        "lot_number": load_data["lot_number"],
        "payload": load_data["payload"],
        "process_token": load_data["process_token"],
        "payload_protocol": load_data.get("payload_protocol", "1"),
        "pt": load_data.get("pt", "1"),
        "w": w,
    }
    url = "https://bcaptcha.botion.com/verify?" + urllib.parse.urlencode(params)
    headers = {
        "Referer": "https://www.leyu.me/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        text = cr.get(url, impersonate="chrome110", headers=headers, timeout=30).text
    except Exception as exc:
        raise VerifyError(
            "network_error",
            reason=type(exc).__name__,
            diagnostics=diagnostics,
        ) from exc

    match = re.search(r"^[^(]+\((.*)\)$", text, re.DOTALL)
    if not match:
        raise VerifyError("invalid_jsonp", diagnostics=diagnostics)
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise VerifyError("invalid_jsonp", diagnostics=diagnostics) from exc

    data = payload.get("data") or {}
    result = data.get("result", "unknown")
    fail_count = int(data.get("fail_count") or 0)
    if result != "success":
        raise VerifyError(result, fail_count, diagnostics=diagnostics)

    seccode = data.get("seccode") or {}
    required = ("captcha_output", "gen_time", "pass_token")
    if any(not seccode.get(field) for field in required):
        raise VerifyError(
            "incomplete_seccode",
            fail_count,
            diagnostics=diagnostics,
        )
    return {field: seccode[field] for field in required}
```

In `login()`, catch `VerifyError` and print/log only:

```python
except VerifyError as exc:
    fields = ",".join(exc.diagnostics.get("e_obj_fields", []))
    size = exc.diagnostics.get("e_obj_bytes", 0)
    print(
        f"  verify failed: result={exc.result}, "
        f"fail_count={exc.fail_count}, lot={load_data['lot_number'][:8]}..., "
        f"e_obj_bytes={size}, e_obj_fields={fields}"
    )
    continue
```

- [ ] **Step 4: Run verify tests to verify GREEN**

Run: `uv run pytest tests/test_http_captcha_login.py -v`

Expected: PASS for fail, malformed JSONP, incomplete seccode, redaction, and new-challenge retry behavior.

- [ ] **Step 5: Commit verify diagnostics**

```bash
git add tests/test_http_captcha_login.py hdata/auth/http_login_v2.py
git commit -m "fix: report captcha verify failures by stage"
```

---

### Task 5: CLI/documentation migration and full verification

**Files:**
- Modify: `test_get_login.py:1-20,171-192`
- Modify: `hdata/auth/__init__.py:1-34`
- Modify: `hdata/auth/README.md:46-71,120-126`

**Interfaces:**
- Manual CLI accepts `--geepass-token`, `--jfbym-token`, and deprecated `--captcha-token` as a jfbym alias.
- Documentation contains environment-variable names only, never credential values.

- [ ] **Step 1: Add a failing CLI argument-parser test seam**

Extract parser creation in `test_get_login.py`:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="get_login interface test")
    parser.add_argument("--account", default="lidongsen1")
    parser.add_argument("--password", default="")
    parser.add_argument("--captcha-token", default="", help="deprecated jfbym token alias")
    parser.add_argument("--geepass-token", default="")
    parser.add_argument("--jfbym-token", default="")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser
```

Add to `tests/test_http_captcha_login.py`:

```python
def test_manual_cli_accepts_separate_platform_tokens():
    from test_get_login import build_parser

    args = build_parser().parse_args(
        ["--http", "--geepass-token", "gp", "--jfbym-token", "jf"]
    )
    assert args.geepass_token == "gp"
    assert args.jfbym_token == "jf"
```

Run: `uv run pytest tests/test_http_captcha_login.py::test_manual_cli_accepts_separate_platform_tokens -v`

Expected: FAIL because `build_parser()` and the separate flags do not yet exist.

- [ ] **Step 2: Implement CLI propagation**

Make `test_http_login()` accept both explicit tokens and call:

```python
result = await get_login(
    account,
    password,
    geepass_token=geepass_token,
    jfbym_token=jfbym_token or captcha_token,
)
```

Require at least one of the three CLI token arguments for `--http`. Remove any concrete token from the module docstring and show only environment-variable or placeholder examples.

- [ ] **Step 3: Update package documentation**

Update `hdata/auth/__init__.py` examples to use:

```python
session = await get_login(
    "username",
    "password",
    geepass_token=os.getenv("GEEPASS_TOKEN", ""),
    jfbym_token=os.getenv("JFBYM_TOKEN", ""),
)
```

Update `hdata/auth/README.md` to document:

```text
GEEPASS_TOKEN=<geepass token>
JFBYM_TOKEN=<jfbym token>
CAPTCHA_TOKEN=<legacy jfbym token; deprecated>
```

Keep the existing statement that pure HTTP `verify` is not yet proven successful. State that this change fixes platform credential routing and diagnostics, not the unknown 76-byte `e_obj` difference.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_http_captcha_login.py tests/test_geetest_signer.py -v`

Expected: all new tests PASS with no external network calls.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests -v`

Expected: all project tests PASS. If an unrelated pre-existing failure occurs, record its exact test name and confirm it also fails against the pre-implementation commit before changing unrelated code.

- [ ] **Step 6: Run static and secret checks**

Run:

```bash
uv run python -m compileall -q hdata tests test_get_login.py
rg -n "(captcha_token|geepass_token|jfbym_token)\s*=\s*['\"][A-Za-z0-9_-]{20,}" . -g "!*.git*"
git diff --check
```

Expected: compile command exits 0; secret search returns no matches; `git diff --check` returns no whitespace errors.

- [ ] **Step 7: Commit migration and documentation**

```bash
git add test_get_login.py hdata/auth/__init__.py hdata/auth/README.md tests/test_http_captcha_login.py
git commit -m "docs: document separate captcha platform tokens"
```

- [ ] **Step 8: Review final change scope**

Run:

```bash
git status --short
git log --oneline -5
git diff HEAD~4..HEAD --stat
```

Expected: only the files named in this plan are included in the implementation commits; unrelated user changes remain uncommitted and untouched.
