"""schemacodec 二进制 schema 帧解码测试。

fixture 为真实抓包（2026-07-19）：tests/fixtures/schema_1008{9,53}.json
"""
import json
from pathlib import Path

import pytest

from hdata.protocol.schemacodec import (
    SCHEMA_CONFIG,
    SchemaDecodeError,
    _BitReader,
    _ByteReader,
    is_codec_frame,
    schema_decode,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ── 基础读取器 ─────────────────────────────────────────


class TestByteReader:
    def test_unsigned_varint_single_byte(self):
        r = _ByteReader(bytes([0x05]))
        assert r.read_unsigned_varint() == 5

    def test_unsigned_varint_multi_byte(self):
        # 300 = 0b100101100 → LEB128: 0xAC 0x02
        r = _ByteReader(bytes([0xAC, 0x02]))
        assert r.read_unsigned_varint() == 300

    def test_signed_varint_zigzag(self):
        # zigzag: 0→0, 1→-1, 2→1, 3→-2, 4→2
        for raw, want in [(0, 0), (1, -1), (2, 1), (3, -2), (4, 2)]:
            r = _ByteReader(bytes([raw]))
            assert r.read_signed_varint() == want

    def test_string_raw(self):
        data = "经典百家乐".encode("utf-8")
        r = _ByteReader(bytes([len(data)]) + data)
        assert r.read_string_raw() == "经典百家乐"

    def test_underflow_raises(self):
        r = _ByteReader(b"")
        with pytest.raises(SchemaDecodeError):
            r.read_unsigned_varint()


class TestBitReader:
    def test_msb_first(self):
        # 0b1011_0011：先读 3 位=101，再读 5 位=10011
        br = _BitReader(bytes([0b10110011]))
        assert br.read_bits(3) == 0b101
        assert br.read_bits(5) == 0b10011

    def test_cross_byte(self):
        br = _BitReader(bytes([0xFF, 0x80]))  # 9 个 1 + 7 个 0
        assert br.read_bits(9) == 0b111111111
        assert br.read_bits(7) == 0

    def test_underflow_raises(self):
        br = _BitReader(b"")
        with pytest.raises(SchemaDecodeError):
            br.read_bits(1)


# ── 真实帧解码（fixture） ──────────────────────────────


class TestRealFrames:
    def test_10089_decode(self):
        fx = _load("schema_10089.json")
        d = schema_decode(fx["key"], fx["data"])
        assert fx["key"] == "10089_7"
        tables = d["hallGameTable"]
        assert len(tables) > 100                 # 全量桌台 id 表
        ids = [t["tableId"] for t in tables if t.get("tableId")]
        assert all(isinstance(i, int) and i > 0 for i in ids)
        # 位段字段（strategy=BIT）也应解出
        one = next(t for t in tables if t.get("tableId"))
        assert "gameStatus" in one and "tableOpen" in one

    def test_10053_decode(self):
        fx = _load("schema_10053.json")
        d = schema_decode(fx["key"], fx["data"])
        assert fx["key"] == "10053_7"
        gtm = d["gameTableMap"]
        assert len(gtm) > 0
        t = next(iter(gtm.values()))
        assert t["tableName"]                    # 如 "极速百家乐M01"
        assert t["gameTypeName"]                 # 官方玩法名（常量池字符串）
        assert isinstance(t["gameTypeId"], int)  # 常量池数字
        assert isinstance(t["tableId"], int)

    def test_unknown_protocol_key(self):
        with pytest.raises(SchemaDecodeError):
            schema_decode("99999_7", "AAAA")

    def test_config_contains_expected_protocols(self):
        assert {"10053_7", "10089_7", "10073_7", "10075_7",
                "301_2", "302_2"} <= set(SCHEMA_CONFIG)


class TestIsCodecFrame:
    def test_true(self):
        assert is_codec_frame({"protocolId": 10053, "serviceTypeId": 7,
                               "codecFlag": True})
    def test_false_without_flag(self):
        assert not is_codec_frame({"protocolId": 10053, "serviceTypeId": 7})
    def test_false_unknown_protocol(self):
        assert not is_codec_frame({"protocolId": 10052, "serviceTypeId": 7,
                                   "codecFlag": True})
