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

