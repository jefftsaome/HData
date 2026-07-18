# Task 2 — Ordered Solver Chain and One Solve per Challenge

## Implementation

- Added `_build_solvers(geepass_token, jfbym_token)` to construct the deterministic geepass-then-jfbym chain.
- Reworked `_solve_captcha(challenge, solvers)` to try each supplied solver once in order, annotate the successful `CaptchaSolution.solver_name`, and raise a sanitized chain-level `CaptchaSolveError` when all solvers fail.
- Added `CaptchaSolution.solver_name` with a safe default.
- Added minimal `VerifyError(result, fail_count)` and made verification failures retry from the outer login loop. A verification failure now fetches a new challenge; it does not solve the same challenge again.
- Routed `captcha_token` only to jfbym, while geepass uses only its explicit argument or `GEEPASS_TOKEN`.
- Updated the HTTP-login examples and environment-variable copy to reflect the legacy jfbym-only token mapping.

## TDD Evidence

### RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: 3 failures, as expected before implementation.

- The old `_solve_captcha` treated a supplied solver list as a token, so it did not call the fake solver chain.
- It returned `None` instead of raising a sanitized `CaptchaSolveError` after chain failure.
- `_build_solvers` did not exist, so the outer retry regression test could not be configured.

The old direct solver path attempted to resolve the intentionally invalid `example.invalid` URL during this RED run; it received no real-service response. GREEN tests fully monkeypatch all exercised login/solver/verification boundaries and make no external calls.

### GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `8 passed in 0.11s`.

The focused suite covers ordered fallback, secret redaction, and exactly one solve for each fetched challenge after verify failure.

## Verification

Command:

```powershell
uv run pytest
```

Result: `59 passed, 11 skipped in 24.50s`.

## Changed Files

- `tests/test_http_captcha_login.py`
- `hdata/auth/http_login_v2.py`
- `hdata/auth/captcha_solver.py`

## Self-Review

- Solver order is determined only by `_build_solvers`: geepass first, then jfbym.
- Individual solver failures are recorded as type names only, so `raw_error` secrets are not carried into the chain error text.
- The outer verify retry loop continues to its next iteration, which creates a fresh challenge and performs one solve for it.
- No unscoped workspace changes will be staged.

## Concerns

- `hdata/auth/http_login_v2.py` was a pre-existing untracked workspace file, and `hdata/auth/captcha_solver.py` had pre-existing edits. Their unrelated content was preserved; only Task 2 edits are intentionally staged where Git supports hunk staging.

---

## Review follow-up: direct Solver error redaction

- Sanitized both JfbymSolver and GeepassSolver failures to stable
  `stage`/`attempt`/`code`/exception-class metadata.
- Removed exception text, platform-controlled messages, response dictionaries,
  token values, and image data from `CaptchaSolveError.raw_error`.
- Corrected the HTTP-login docstring so legacy `captcha_token` and
  `CAPTCHA_TOKEN` are documented as jfbym-only.
- Added four local regression tests covering platform responses and network
  exceptions for both solvers.

Fresh controller verification:

- `uv run pytest tests/test_http_captcha_login.py -v`: 12 passed.
- `uv run pytest tests -v`: 63 passed, 11 skipped.
- No real captcha or login endpoint was called.

## Review Follow-up

- Direct `JfbymSolver` and `GeepassSolver` error construction now reduces `raw_error` to stable stage, attempt, and code metadata; arbitrary exception text, platform messages, and response payloads are discarded before they can appear in either `raw_error` or `str(CaptchaSolveError)`.
- The HTTP-login docstring now explicitly documents that `captcha_token` and `CAPTCHA_TOKEN` are legacy jfbym-only inputs and never supply geepass.

### Follow-up RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py::test_jfbym_solver_redacts_platform_response_values tests/test_http_captcha_login.py::test_geepass_solver_redacts_network_exception_values -v
```

Result: 2 expected failures. Sentinel platform-response and network-exception values were present in `str(CaptchaSolveError)` before the sanitizer.

### Follow-up GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `10 passed in 3.14s`.

The direct-solver tests monkeypatch all `curl_cffi` request boundaries, so they make no real network calls.

### Follow-up Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `61 passed, 11 skipped in 27.54s`.

## Review-Fix Completion

- Replaced text-derived error redaction with an allowlist for stable
  `stage`, `attempt`, numeric `code`, and exception-class metadata.
- Direct JfbymSolver and GeepassSolver submit and response failures now
  construct that metadata before `CaptchaSolveError` is raised; mocked
  response payloads and exception messages are never retained in
  `raw_error` or `str(error)`.
- Confirmed the `http_login_v2.login` docstring states that legacy
  `captcha_token` and `CAPTCHA_TOKEN` map only to jfbym.

### Review-Fix RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py::test_jfbym_solver_redacts_platform_response_values tests/test_http_captcha_login.py::test_geepass_solver_redacts_network_exception_values tests/test_http_captcha_login.py::test_jfbym_solver_redacts_network_exception_values tests/test_http_captcha_login.py::test_geepass_solver_redacts_platform_response_values -v
```

Result: 4 expected failures. The old errors omitted response attempts and
exception classes, demonstrating the new assertions exercised the intended
metadata path. All request boundaries were mocked; no real network calls were
made.

### Review-Fix GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `12 passed in 0.12s`.

### Review-Fix Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `63 passed, 11 skipped in 24.55s`.

## P1 Sanitization Follow-up

- Wrapped background and reference image downloads for both direct solvers in
  `CaptchaSolveError` with only stable `background_download` or
  `reference_download` metadata.
- Wrapped malformed successful provider data and coordinate/target conversion
  failures with stable `response_parse` metadata.
- Added fully mocked sentinel tests for both download stages and malformed
  success data for JfbymSolver and GeepassSolver; they make no network calls
  and do not sleep.

### P1 RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py::test_solver_redacts_image_download_exception tests/test_http_captcha_login.py::test_jfbym_solver_redacts_malformed_success_data tests/test_http_captcha_login.py::test_geepass_solver_redacts_malformed_success_data -v
```

Result: 6 expected failures. Image-download exceptions and malformed provider
success data were propagated directly before the new wrappers were added.

### P1 GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py -v
```

Result: `18 passed in 0.13s`.

### P1 Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `69 passed, 11 skipped in 24.67s`.
