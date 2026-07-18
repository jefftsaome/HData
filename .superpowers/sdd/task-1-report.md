# Task 1: Public token resolution and propagation

## Implementation

- Added public `geepass_token` and `jfbym_token` keyword-only arguments to
  `hdata.auth.api.get_login`.
- Resolved platform tokens independently: explicit platform arguments take
  precedence over their respective environment variables; `captcha_token` and
  `CAPTCHA_TOKEN` are retained only as fallbacks for `jfbym_token`.
- Passed an empty legacy `captcha_token` plus explicit platform tokens to the
  session boundary, preventing cross-platform token reuse.
- Added the same public token arguments to `hdata.auth.session.get_login` and
  forwarded them explicitly to the HTTP login implementation. Existing cache,
  refresh, and browser fallback paths were left unchanged.

## Tests and TDD evidence

### RED

1. Added the three requested API token-routing tests before modifying production
   code.
2. Ran `uv run pytest tests/test_http_captcha_login.py -v`.
3. Result: 3 failed as expected:
   - `TypeError: get_login() got an unexpected keyword argument 'geepass_token'`
     for the two explicit-token cases.
   - Missing `geepass_token` forwarding for the legacy-token case.

### GREEN

1. Implemented the minimal public-resolution and explicit-forwarding changes.
2. Ran `uv run pytest tests/test_http_captcha_login.py -v`.
3. Result: 3 passed in 0.09s.
4. Added a direct, fully mocked session-boundary regression test to assert the
   HTTP login call receives separate explicit platform keywords; fresh focused
   verification result: 4 passed in 0.10s.

## Verification commands and results

| Command | Result |
| --- | --- |
| `uv run pytest tests/test_http_captcha_login.py -v` (RED) | 3 failed, expected missing routing interface/forwarding |
| `uv run pytest tests/test_http_captcha_login.py -v` (GREEN) | 3 passed in 0.09s |
| `uv run pytest tests/test_http_captcha_login.py -v` (fresh final) | 4 passed in 0.10s |
| `uv run pytest` | 55 passed, 11 skipped in 24.52s |
| `git diff --check --no-index NUL <each Task 1 file>` | No whitespace errors |

No captcha service or credential was invoked or printed; every new focused test
uses mocked login boundaries.

## Files changed

- `hdata/auth/api.py`
- `hdata/auth/session.py`
- `tests/test_http_captcha_login.py`

## Commit

- `8aec1de fix: route captcha platform tokens independently`

## Self-review

- Confirmed the precedence sequence is exactly platform-specific: explicit
  token, then platform environment value; legacy values are considered solely
  for JFBYM.
- Confirmed API passes `captcha_token=""` to session, so a legacy token cannot
  accidentally be used as a geepass token downstream.
- Confirmed session retains legacy direct-call compatibility by mapping its
  `captcha_token` only to `jfbym_token`.
- Confirmed HTTP login receives `geepass_token` and `jfbym_token` as explicit
  keywords, avoiding its legacy shared-token behavior.

## Concerns

None. The workspace contained unrelated pre-existing modifications and
untracked files; they were not staged for this task.

---

## Review follow-up: sanitized HTTP login errors

### Fixes

- Replaced raw exception interpolation in the HTTP-login fallback warning with
  a fixed `HTTP login stage failed` message and the exception class name only.
  The warning no longer includes `str(e)`, so it cannot expose a password,
  captcha token, HTTP response body, or `w` value carried by that exception.
- Cleared `CAPTCHA_TOKEN`, `GEEPASS_TOKEN`, and `JFBYM_TOKEN` in the legacy
  captcha-token test to make it deterministic and prevent configured values
  from reaching a failing assertion.
- Added a fully local regression test that raises a sentinel secret from the
  mocked HTTP login, captures session warnings, and confirms the sentinel is
  absent while the existing browser fallback completes through a fake browser.

### TDD and verification

| Command | Result |
| --- | --- |
| `uv run pytest tests/test_http_captcha_login.py -v` (RED) | 1 failed, 4 passed; the captured warning contained the sentinel exception secret. |
| `uv run pytest tests/test_http_captcha_login.py -v` (GREEN) | 5 passed in 0.11s. |
| `uv run pytest tests -v` | 56 passed, 11 skipped in 24.55s. |
| `git diff --check -- hdata/auth/session.py tests/test_http_captcha_login.py` | No whitespace errors. |

No live captcha or browser service was used by the regression test, and no
credential value was printed during verification.

Follow-up commit: `14b34f3 fix: sanitize http login fallback errors`.
