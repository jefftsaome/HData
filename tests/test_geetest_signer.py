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
