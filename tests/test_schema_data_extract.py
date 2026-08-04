"""外置后基线一致性 + 解码回归。"""
from hdata.protocol._schema_data import SCHEMA_CONFIG


def test_baseline_loaded_nonempty():
    assert len(SCHEMA_CONFIG) >= 5
    for v in SCHEMA_CONFIG.values():
        assert isinstance(v, dict)


def test_scalars_survive_round_trip():
    import importlib
    import json
    from pathlib import Path

    from hdata.protocol import _schema_data

    pristine = importlib.reload(_schema_data)
    blob = json.loads(
        Path("hdata/protocol/schema_data.json").read_text(encoding="utf-8")
    )
    assert blob == pristine.SCHEMA_CONFIG
