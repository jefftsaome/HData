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
