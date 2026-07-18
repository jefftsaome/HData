# Task 5 — CLI and Documentation Migration

## Implementation

- Extracted `test_get_login.build_parser()` with explicit `--geepass-token`
  and `--jfbym-token` flags. The deprecated `--captcha-token` remains a
  jfbym-only alias.
- Routed manual HTTP login through explicit platform keyword arguments and
  require at least one platform token for `--http`.
- Removed the concrete captcha credential from the manual script and document
  environment-variable-only credential configuration.
- Updated package and authentication documentation to state the legacy
  jfbym-only mapping and that pure HTTP verify is still unproven because of
  the unknown 76-byte `e_obj` difference.

## TDD Evidence

### RED

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py::test_manual_cli_accepts_separate_platform_tokens -v
```

Result: expected failure. Test collection imported `test_get_login`, but
`build_parser` did not yet exist.

### GREEN

Command:

```powershell
uv run pytest tests/test_http_captcha_login.py::test_manual_cli_accepts_separate_platform_tokens -v
```

Result: `1 passed in 0.13s` after extracting the parser and separate flags.

Focused command:

```powershell
uv run pytest tests/test_http_captcha_login.py tests/test_geetest_signer.py -v
```

Result: `38 passed in 0.34s`. All tests are local; the CLI parser test only
parses arguments and never calls login or the network.

## Verification

An earlier full-suite run after the Task 5 changes completed successfully:

```powershell
uv run pytest tests -v
```

Result: `89 passed, 11 skipped in 24.61s`.

During the final requested rerun, `uv` was unable to initialize its shared
cache outside the workspace (`G:\Applications\Scoop\persist\uv\cache`, access
denied). The equivalent project-venv checks were run directly:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py -v
```

Result: `31 passed in 0.17s` with one non-failing pytest-cache permission
warning.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

Result: `1 failed, 88 passed, 11 skipped in 28.15s`. The sole failure was
unrelated `tests/test_integration.py::test_auto_start`: Chrome started but the
environment rejected the CDP connection on port 9225. This contrasts with the
earlier successful full run and was not changed or debugged as part of Task 5.

```powershell
.\.venv\Scripts\python.exe -m compileall -q hdata tests test_get_login.py
```

Result: exit 0.

```powershell
rg -n '(captcha_token|geepass_token|jfbym_token)\s*=\s*["''][A-Za-z0-9_-]{20,}' test_get_login.py hdata/auth tests -g '!*.git*'
```

Result: no matches.

`git diff --check` reports existing whitespace errors in unrelated files:
`docs/captcha-flow.md`, `docs/captcha-research.md`,
`hdata/auth/token_manager.py`, and `scripts/capture_real_w.py`. It reported
no Task 5 whitespace error.

## Scope Notes

- `test_get_login.py` is a pre-existing untracked file; only the CLI migration
  edits were made.
- `hdata/auth/__init__.py` and `hdata/auth/README.md` contained pre-existing
  user edits; unrelated changes remain untouched.
- No staging or commit was attempted during the verification-only follow-up.

## Export Compatibility Review Fix

- Restored all established auth package export names in `__all__`.
- Added a module-level lazy `__getattr__` map that imports legacy symbols only
  when requested and caches them in module globals.
- `HeadlessLogin` remains listed for clean-checkout compatibility but does not
  make normal package imports fail while its deleted module is absent in this
  dirty tree.

### RED

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py::test_auth_package_keeps_legacy_exports -v
```

Result: expected failure because the legacy names were absent from `__all__`.

### GREEN

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py -v
```

Result: `32 passed in 0.25s` (with a non-failing pytest-cache permission
warning). The regression resolves `TokenManager`, `DomainCache`, and
`CaptchaSolver` without accessing deleted `HeadlessLogin`.

### Full Verification

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

Result: `1 failed, 89 passed, 11 skipped in 28.23s`. The only failure is the
same unrelated environment-dependent `tests/test_integration.py::test_auto_start`
Chrome CDP port-9225 connection failure seen in the Task 5 verification rerun.

## Final Security Review Fixes

- `refresh_game_token()` now emits only structured refresh stage, HTTP status,
  and exception-class metadata; network exceptions, response bodies, URLs, and
  JWT fragments are not carried through an exception chain or logs.
- HTTP validate/login/UUID status output reports only safe stages, statuses,
  and exception classes.
- Direct solver handling now converts top-level non-mapping provider JSON into
  a structured, redacted `CaptchaSolveError`.
- Manual CLI output reports token presence only and no token/JWT fragments.
- Added the missing `sys` import used by the HTTP login module entrypoint.

### RED

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py::test_refresh_game_token_redacts_network_and_response_values tests/test_http_captcha_login.py::test_http_login_stage_output_redacts_server_message_and_uuid_exception tests/test_http_captcha_login.py::test_http_login_module_main_dependencies_are_imported tests/test_http_captcha_login.py::test_solver_redacts_nonmapping_top_level_response tests/test_http_captcha_login.py::test_manual_cli_output_never_includes_token_values -v
```

Result: 6 expected failures: the previous paths exposed raw exception/response
values, printed token fragments, lacked `sys`, or raised `AttributeError` for
non-mapping solver responses.

### GREEN

The same command passed: `6 passed in 0.17s` (only the known non-failing
pytest-cache permission warning).

Focused suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_http_captcha_login.py -v
```

Result: `38 passed in 0.17s` with the same non-failing pytest-cache warning.
