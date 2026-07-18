# Task 3 — Deterministic `e_obj` Construction

## Implementation

- Added `build_e_obj()` with injectable `passtime` and PoW nonce for
  deterministic plaintext construction.
- Added `serialize_e_obj()` with compact, Unicode-preserving JSON encoding.
- Refactored `generate_w()` to build and serialize the plaintext through those
  functions before preserving the existing AES/RSA encryption flow.
- Added optional diagnostics limited to sorted `e_obj_fields` and UTF-8
  `e_obj_bytes`.

## TDD Evidence

### RED

Command:

```powershell
uv run pytest tests/test_geetest_signer.py -v
```

Result: collection failed as expected with an ImportError because
`build_e_obj` and `serialize_e_obj` did not yet exist.

### GREEN

Command:

```powershell
uv run pytest tests/test_geetest_signer.py -v
```

Result: `7 passed in 0.12s`.

The focused tests use only static load data and injected values; they make no
network requests.

## Full Verification

Command:

```powershell
uv run pytest tests -v
```

Result: `76 passed, 11 skipped in 24.58s`.

## Self-Review

- Deterministic calls do not consume random values when both `passtime` and
  `pow_nonce` are injected.
- Coordinates are constrained to exactly three integer x,y pairs, and required
  PoW fields are validated before construction.
- No new `e_obj` fields were introduced; the payload uses the existing signer
  field set.
- `generate_w()` continues to return a string and preserves its encryption
  sequence.
- Pre-existing `LotParser` and payload-shape edits in `geetest_signer.py` were
  left intact.

## Human adjudication

Review noted that `em.cp` and `em.ek` differ from the last committed signer
payload. The approved implementation plan and the user's pre-task working tree
both contain these fields. On 2026-07-17 the user explicitly selected option 1:
retain `{"cp": 0, "ek": "11"}` as the governing requirement.
