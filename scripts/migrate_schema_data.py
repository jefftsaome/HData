"""DEPRECATED(一次性迁移): 将 _schema_data.SCHEMA_CONFIG 落盘为 schema_data.json。

Deprecated: 一次性迁移脚本，schema_data.json 已随包落盘，无需再运行。
"""
import json
from pathlib import Path

from hdata.protocol._schema_data import SCHEMA_CONFIG

out = Path(__file__).resolve().parent.parent / "hdata" / "protocol" / "schema_data.json"
blob = json.dumps(SCHEMA_CONFIG, ensure_ascii=False, indent=1) + "\n"
out.write_text(blob, encoding="utf-8")
loaded = json.loads(blob)
assert loaded == SCHEMA_CONFIG, "round-trip mismatch"
print(f"written {out} ({out.stat().st_size} bytes), {len(SCHEMA_CONFIG)} keys")
