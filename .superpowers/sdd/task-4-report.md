# Task 4 — Typed Verify-Stage Failures and Safe Diagnostics

## Implementation

- Extended `VerifyError` with `reason` and a structured `diagnostics` mapping while keeping its message limited to result, reason, and fail count.
- Reworked `_verify_captcha()` to pass a diagnostics dictionary to `generate_w()`, parse JSONP once, and raise `VerifyError` for network errors, invalid JSONP, unsuccessful verify results, and incomplete seccodes.
- Required all three seccode values (`captcha_output`, `gen_time`, and `pass_token`) before returning a normalized seccode dictionary.
- Updated the login retry path to print only result, fail count, an eight-character challenge prefix, and `e_obj` field names/byte length. It continues to the next freshly fetched challenge.

## TDD Evidence

### RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: 2 expected failures.

- Invalid JSONP produced the previous `invalid-response` value instead of the typed `invalid_jsonp` failure.
- A successful response with only `pass_token` returned an incomplete seccode instead of raising `incomplete_seccode`.

All new verify tests replace `generate_w` and `http_login_v2.cr.get`; no verify HTTP request is made.

### GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `21 passed in 0.23s`.

The focused coverage includes failure-result parsing, malformed JSONP, incomplete seccodes, existing challenge-retry behavior, and secret redaction.

## Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `79 passed, 11 skipped in 24.65s`.

## Changed Files

- `hdata/auth/http_login_v2.py`
- `tests/test_http_captcha_login.py`

## Self-Review

- Neither exception messages nor retry diagnostics include the generated `w`, JSONP response content, payload, process token, or full lot number.
- `VerifyError` retains only typed result/fail-count/reason metadata plus the controlled diagnostics dictionary.
- Every `VerifyError` branch exits the current iteration, preserving the one-solve-per-fresh-challenge rule.

## Concerns

- The Git metadata sandbox has previously rejected elevated staging requests in this task. The code, tests, and report are ready; commit finalization may need controller access.

## Review Follow-up

- Network and JSON-decode failures now use `from None`, so their underlying exception text cannot reappear through `__cause__` or a traceback chain.
- The verify parser accepts only fixed `success` and `fail` result literals. Any other server-controlled result maps to `unexpected_result`.
- Non-numeric `fail_count` values safely become `0`; decoded payload/data mappings are validated before access, and non-mapping seccodes become `incomplete_seccode`.
- Restored the Geepass direct-solver assertion: its exhausted submit error remains exactly `stage=submit attempt=3 exception=RuntimeError`.

### Follow-up RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: 5 expected failures. They demonstrated retained exception causes, a raw `ValueError` from server-controlled `fail_count`, untrusted result propagation, and missing mapping validation.

### Follow-up GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `28 passed in 0.25s`.

The new tests use only mocked `generate_w`, `cr.get`, fetch, and solver boundaries. They assert sentinel values are absent from exception text, fields, diagnostics, causes, and captured login diagnostics.

### Follow-up Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `86 passed, 11 skipped in 24.61s`.

## Final Review Follow-up

- Network and JSON parsing now record only fixed local status flags inside `except` blocks, then construct `VerifyError` after leaving those blocks. The resulting typed errors have both `__cause__` and `__context__` set to `None`.
- Result allowlisting now checks that the server value is a string before membership testing, so list and dictionary values safely become `unexpected_result`.

### Final RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: 4 expected failures: retained `__context__` for network/JSON failures and `TypeError` for list/dictionary result values.

### Final GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `30 passed in 0.20s`.

### Final Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `88 passed, 11 skipped in 24.60s`.
