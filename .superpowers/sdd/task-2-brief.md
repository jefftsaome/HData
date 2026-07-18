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

