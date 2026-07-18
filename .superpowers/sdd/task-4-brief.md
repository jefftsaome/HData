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

