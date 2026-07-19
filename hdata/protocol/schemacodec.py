"""自定义二进制 schema 协议解码（10053/10089 等"codec"帧）。

逆向自大厅前端 JS（egret/js/assets-*.js 中的
U3/Z3/Y3/z3/J3/W3/K3/Q3/X3 等类），行为逐点对齐：

- 帧的 `data` 字段是标准 base64，解出字节后布局为：

      varint bits_len | varint pool_len | varint body_len
      bits[bits_len]   位段：strategy=BIT 的标量字段，MSB-first 连续读取
      pool[pool_len]   常量池：varint n_str + n_str×string
                              + varint n_num + n_num×signedVarNumber
      body[body_len]   主体段：见 read_schema

- read_schema(schema)：先读 ceil(len(body_fields)/8) 字节存在掩码
  （仅非 BIT 标量字段计入，MSB-first），然后按 schema 字段顺序：
  BIT 标量字段 → 从位段读 bitWidth 位；其余字段掩码位置 1 → 从主体段读值。

- 字段类型 S3：INT=1 BOOLEAN=2 NUMBER=3 STRING=4 MESSAGE=5 ARRAY=6 MAP=7
- 策略  B3：BODY=0 BIT=1 CONST_POOL=2（CONST_POOL 读 varint 索引查常量池）
- Map key 类型 O3：INT_KEY=0 STRING_KEY=1 NUMBER_KEY=2，
  key 策略缺省为 BODY。

schema 配置见 `_schema_data.py`（从前端 H3 常量原样移植）。
"""
from __future__ import annotations

import base64
import struct

from hdata.protocol._schema_data import SCHEMA_CONFIG

# ── 字段类型 / 策略枚举（与前端 S3/B3/O3/P3 一致） ─────
INT, BOOLEAN, NUMBER, STRING, MESSAGE, ARRAY, MAP = 1, 2, 3, 4, 5, 6, 7
BODY, BIT, CONST_POOL = 0, 1, 2
INT_KEY, STRING_KEY, NUMBER_KEY = 0, 1, 2

_INT_TYPES = (INT, NUMBER, BOOLEAN)


class SchemaDecodeError(Exception):
    """schema 帧解码失败。"""


# ── 基础读取器 ─────────────────────────────────────────


class _ByteReader:
    """字节流读取器（对齐前端 U3）。"""

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def _need(self, n: int):
        if self.pos + n > len(self.buf):
            raise SchemaDecodeError(
                f"缓冲区下溢: 需要 {n}, 剩余 {len(self.buf) - self.pos}")

    def read_bool(self) -> bool:
        self._need(1)
        v = self.buf[self.pos] != 0
        self.pos += 1
        return v

    def read_unsigned_varint(self) -> int:
        v = 0
        shift = 0
        while True:
            self._need(1)
            b = self.buf[self.pos]
            self.pos += 1
            v |= (b & 0x7F) << shift
            if not (b & 0x80):
                return v
            shift += 7
            if shift > 70:
                raise SchemaDecodeError("varint 过长")

    read_unsigned_varnumber = read_unsigned_varint

    @staticmethod
    def _zigzag(v: int) -> int:
        t = v >> 1
        return -t - 1 if v & 1 else t

    def read_signed_varint(self) -> int:
        return self._zigzag(self.read_unsigned_varint())

    def read_signed_varnumber(self) -> int:
        return self._zigzag(self.read_unsigned_varint())

    def read_double(self) -> float:
        self._need(8)
        v = struct.unpack_from("<d", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def read_bytes(self, n: int) -> bytes:
        self._need(n)
        v = self.buf[self.pos:self.pos + n]
        self.pos += n
        return v

    def read_string_raw(self) -> str:
        n = self.read_unsigned_varint()
        return self.read_bytes(n).decode("utf-8")


class _BitReader:
    """位流读取器（对齐前端 Z3：每字节 MSB-first）。"""

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def read_bits(self, n: int) -> int:
        if n < 0 or n > 62:
            raise SchemaDecodeError(f"无效的位宽: {n}")
        v = 0
        for _ in range(n):
            byte_i = self.pos >> 3
            if byte_i >= len(self.buf):
                raise SchemaDecodeError("位缓冲区下溢")
            shift = 7 - (self.pos & 7)
            v = 2 * v + ((self.buf[byte_i] >> shift) & 1)
            self.pos += 1
        return v


def _mask_get(mask: bytes, idx: int) -> bool:
    """字段存在掩码（对齐前端 F3：MSB-first）。"""
    byte_i = idx >> 3
    shift = 7 - (idx & 7)
    if byte_i >= len(mask):
        raise SchemaDecodeError("掩码索引越界")
    return (mask[byte_i] >> shift) & 1 == 1


# ── schema 编译（对齐前端 Q3/w3/D3） ───────────────────


class _Field:
    __slots__ = ("name", "type", "strategy", "bit_width", "schema_ref",
                 "elem_schema_ref", "value_schema_ref", "elem_type",
                 "value_type", "key_type", "key_strategy", "key_bit_width",
                 "scalar_bit_field", "default_raw")

    def __init__(self, raw: dict, resolve):
        self.name = raw["name"]
        self.type = raw["type"]
        self.strategy = raw.get("strategy") or BODY
        self.bit_width = raw.get("bit") or 0
        self.schema_ref = resolve(raw["schema"]) \
            if self.type == MESSAGE and raw.get("schema") else None
        self.elem_schema_ref = resolve(raw["elemSchema"]) \
            if self.type == ARRAY and raw.get("elemSchema") else None
        self.value_schema_ref = resolve(raw["valueSchema"]) \
            if self.type == MAP and raw.get("valueSchema") else None
        self.elem_type = raw.get("elemType")
        self.value_type = raw.get("valueType")
        self.key_type = raw.get("keyType") or INT_KEY
        self.key_strategy = raw.get("keyStrategy") or BODY
        self.key_bit_width = raw.get("keyBit") or 0
        self.scalar_bit_field = (
            self.strategy == BIT and self.type in _INT_TYPES)
        self.default_raw = raw.get("defaultValue")


class _Schema:
    __slots__ = ("name", "fields", "body_fields")

    def __init__(self, name, fields):
        self.name = name
        self.fields = fields
        self.body_fields = [f for f in fields if not f.scalar_bit_field]


def _compile(config: dict) -> "_Schema":
    schemas: dict[str, _Schema] = {}

    def resolve(name: str) -> _Schema:
        if name in schemas:
            return schemas[name]
        raw_fields = config["schemas"].get(name)
        if raw_fields is None:
            raise SchemaDecodeError(f"未找到 schema: {name}")
        # 先占位再填字段，容忍循环引用
        schema = _Schema(name, [])
        schemas[name] = schema
        schema.fields = [_Field(f, resolve) for f in raw_fields]
        schema.body_fields = [f for f in schema.fields
                              if not f.scalar_bit_field]
        return schema

    return resolve(config.get("root") or "Root")


# ── 消息解码（对齐前端 K3/Y3/z3/J3/W3） ────────────────


class _ConstPool:
    def __init__(self, strings: list[str], numbers: list[int]):
        self.strings = strings
        self.numbers = numbers

    def get_string(self, i: int) -> str:
        if i < 0 or i >= len(self.strings):
            raise SchemaDecodeError(f"string 常量池索引越界: {i}")
        return self.strings[i]

    def get_number(self, i: int) -> int:
        if i < 0 or i >= len(self.numbers):
            raise SchemaDecodeError(f"number 常量池索引越界: {i}")
        return self.numbers[i]


def _read_const_pool(buf: bytes) -> _ConstPool:
    r = _ByteReader(buf)
    strings = [r.read_string_raw()
               for _ in range(r.read_unsigned_varint())]
    numbers = [r.read_signed_varnumber()
               for _ in range(r.read_unsigned_varint())]
    return _ConstPool(strings, numbers)


def _read_primitive(field: _Field, body: _ByteReader,
                    pool: _ConstPool):
    if field.strategy == CONST_POOL:
        idx = body.read_unsigned_varint()
        if field.type == STRING:
            return pool.get_string(idx)
        if field.type in (INT, NUMBER):
            return pool.get_number(idx)
        raise SchemaDecodeError(
            f"CONST_POOL 不支持的原始类型: {field.type}, field={field.name}")
    if field.type == INT:
        return body.read_signed_varint()
    if field.type == NUMBER:
        return body.read_signed_varnumber()
    if field.type == BOOLEAN:
        return body.read_bool()
    if field.type == STRING:
        return body.read_string_raw()
    raise SchemaDecodeError(
        f"不支持的原始类型: {field.type}, field={field.name}")


def _bit_default(field: _Field):
    if field.default_raw is not None:
        raw = field.default_raw
        if field.type == BOOLEAN:
            return raw in ("true", "1")
        try:
            return int(raw)
        except ValueError:
            return float(raw)
    if field.type == BOOLEAN:
        return False
    return 0


def _read_scalar_bit_field(field: _Field, bits: _BitReader):
    try:
        v = bits.read_bits(field.bit_width)
        if field.type == BOOLEAN:
            return v != 0
        return v
    except SchemaDecodeError:
        return _bit_default(field)


def _read_dynamic(body: _ByteReader, pool: _ConstPool):
    t = body.read_unsigned_varint()
    if t == 0:                      # NULL
        return None
    if t == 1:                      # INT
        return body.read_signed_varint()
    if t == 2:                      # NUMBER
        return body.read_signed_varnumber()
    if t == 3:                      # BOOLEAN
        return body.read_bool()
    if t == 4:                      # STRING
        return body.read_string_raw()
    if t == 5:                      # ARRAY
        return [_read_dynamic(body, pool)
                for _ in range(body.read_unsigned_varint())]
    if t == 6:                      # OBJECT（key 为常量池字符串索引）
        obj = {}
        for _ in range(body.read_unsigned_varint()):
            k = pool.get_string(body.read_unsigned_varint())
            obj[k] = _read_dynamic(body, pool)
        return obj
    if t == 7:                      # DOUBLE
        return body.read_double()
    if t == 8:                      # DECIMAL
        return body.read_string_raw()
    raise SchemaDecodeError(f"不支持的动态类型: {t}")


def _read_map_key(field: _Field, bits: _BitReader, body: _ByteReader,
                  pool: _ConstPool) -> str:
    if field.key_strategy == BODY:
        if field.key_type == INT_KEY:
            return str(body.read_unsigned_varint())
        if field.key_type == NUMBER_KEY:
            return str(body.read_unsigned_varnumber())
        return body.read_string_raw()
    if field.key_strategy == CONST_POOL:
        idx = body.read_unsigned_varint()
        if field.key_type == STRING_KEY:
            return pool.get_string(idx)
        return str(pool.get_number(idx))
    if field.key_strategy == BIT:
        v = bits.read_bits(field.key_bit_width)
        if field.key_type == STRING_KEY:
            raise SchemaDecodeError("STRING_KEY 不支持 BIT keyStrategy")
        return str(v)
    raise SchemaDecodeError(f"不支持的 keyStrategy: {field.key_strategy}")


def _read_value(field: _Field, bits: _BitReader, body: _ByteReader,
                pool: _ConstPool):
    t = field.type
    if t in (INT, BOOLEAN, NUMBER, STRING):
        return _read_primitive(field, body, pool)
    if t == MESSAGE:
        return _read_schema(field.schema_ref, bits, body, pool)
    if t == ARRAY:
        n = body.read_unsigned_varint()
        if field.strategy == BIT:
            out = []
            for _ in range(n):
                v = bits.read_bits(field.bit_width)
                if field.elem_type == BOOLEAN:
                    out.append(v != 0)
                else:
                    out.append(v)
            return out
        if field.elem_schema_ref is not None:
            return [_read_schema(field.elem_schema_ref, bits, body, pool)
                    for _ in range(n)]
        elem = _Field({"name": field.name, "type": field.elem_type,
                       "strategy": field.strategy}, None)
        return [_read_primitive(elem, body, pool) for _ in range(n)]
    if t == MAP:
        n = body.read_unsigned_varint()
        out = {}
        for _ in range(n):
            k = _read_map_key(field, bits, body, pool)
            if field.value_schema_ref is not None:
                out[k] = _read_schema(field.value_schema_ref, bits, body, pool)
            elif field.value_type is not None:
                vf = _Field({"name": field.name, "type": field.value_type,
                             "strategy": field.strategy}, None)
                out[k] = _read_primitive(vf, body, pool)
            else:
                out[k] = _read_dynamic(body, pool)
        return out
    raise SchemaDecodeError(f"不支持的字段类型: {t}")


def _read_schema(schema: _Schema, bits: _BitReader, body: _ByteReader,
                 pool: _ConstPool) -> dict:
    mask = body.read_bytes(len(schema.body_fields) + 7 >> 3)
    obj = {}
    mi = 0
    for field in schema.fields:
        if field.scalar_bit_field:
            obj[field.name] = _read_scalar_bit_field(field, bits)
        else:
            if _mask_get(mask, mi):
                obj[field.name] = _read_value(field, bits, body, pool)
            mi += 1
    return obj


# ── 对外接口 ───────────────────────────────────────────

_ROOTS: dict[str, _Schema] = {}


def _root(protocol_key: str) -> _Schema:
    if protocol_key not in _ROOTS:
        config = SCHEMA_CONFIG.get(protocol_key)
        if config is None:
            raise SchemaDecodeError(f"未注册的协议 schema: {protocol_key}")
        _ROOTS[protocol_key] = _compile(config)
    return _ROOTS[protocol_key]


def schema_decode(protocol_key: str, data_b64: str) -> dict:
    """解码一帧 codec 数据。

    Args:
        protocol_key: "{protocolId}_{serviceTypeId}"，如 "10053_7"
        data_b64: 帧 data 字段（标准 base64）

    Returns:
        解码后的业务 dict
    """
    raw = base64.b64decode(data_b64 + "=" * (-len(data_b64) % 4))
    top = _ByteReader(raw)
    bits_len = top.read_unsigned_varint()
    pool_len = top.read_unsigned_varint()
    body_len = top.read_unsigned_varint()
    bits = _BitReader(top.read_bytes(bits_len))
    pool = _read_const_pool(top.read_bytes(pool_len))
    body = _ByteReader(top.read_bytes(body_len))
    return _read_schema(_root(protocol_key), bits, body, pool)


def is_codec_frame(frame: dict) -> bool:
    """该帧是否携带 schema 二进制载荷（codecFlag 置位且协议已注册）。"""
    pid = frame.get("protocolId")
    sid = frame.get("serviceTypeId")
    return bool(frame.get("codecFlag")) \
        and f"{pid}_{sid}" in SCHEMA_CONFIG
