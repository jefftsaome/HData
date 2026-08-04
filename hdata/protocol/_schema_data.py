"""二进制 schema 协议配置基线(数据外置为同目录 schema_data.json)。

原 2402 行 dict 字面量已迁移;本模块是 SCHEMA_CONFIG 唯一所有者,
运行时热更新(schemacodec.update_schema_config)仍原地修改此 dict。
"""
import json
from pathlib import Path

SCHEMA_CONFIG: dict = json.loads(
    (Path(__file__).with_name("schema_data.json")).read_text(encoding="utf-8")
)
