"""乐鱼路纸（roadPaper）位图解码 — 纯算法，无 IO。

协议（来自游戏前端 egret release js parseBaccaratSingleBootRoadPaper）：

  编码: base64 → 字节流 → 按 MSB-first 拼成位串，游标顺序读位
  头部: version = read(8) + 1;  n = read(8) * read(8)   # 单元格总数
  每格: flag = read(1)
        - 大路(BIG_ROAD):  flag=1 → result=read(2), tieNumber=read(4)
                           flag=0 → 空位（结果为空，仍占一格）
        - 珠盘(MAIN_ROAD): flag=1 → result=read(2), pair=read(2)
                           flag=0 → 空位
  分列: 每 6 格一列（大路/珠盘通用）

结果枚举（Pa）: 0=闲(PLAYER_WIN) 1=庄(BANKER_WIN) 2=和(TIE) 3=庄六(BANKER_SIX)
"""

from __future__ import annotations

import base64

# ── 结果枚举 ──
RESULT_PLAYER = 0   # 闲赢
RESULT_BANKER = 1   # 庄赢
RESULT_TIE = 2      # 和
RESULT_BANKER_SIX = 3  # 庄六（免佣庄6点赢）

RESULT_MAP = {
    RESULT_PLAYER: "P",
    RESULT_BANKER: "B",
    RESULT_TIE: "T",
    RESULT_BANKER_SIX: "B6",
}

COLUMN_SIZE = 6  # 每列 6 格


class BitReader:
    """MSB-first 位读取器，与 JS 端 _i 类一致。"""

    def __init__(self, b64: str):
        raw = base64.b64decode(b64)
        self._bits = "".join(f"{byte:08b}" for byte in raw)
        self._ptr = 0

    @property
    def length(self) -> int:
        return len(self._bits)

    def read(self, n: int) -> int:
        """读 n 位为整数并移动游标。"""
        if self._ptr + n > len(self._bits):
            # 越界补 0（容错）
            val = int(self._bits[self._ptr:] or "0", 2)
            self._ptr = len(self._bits)
            return val
        val = int(self._bits[self._ptr:self._ptr + n], 2)
        self._ptr += n
        return val


def _read_header(reader: BitReader) -> tuple[int, int]:
    """读头部，返回 (version, 单元格数)。"""
    version = reader.read(8) + 1
    n = reader.read(8) * reader.read(8)
    return version, n


def decode_big_road(b64: str) -> dict:
    """解码大路（bigRoad）。

    Returns:
        {
          "version": int,
          "columns": [[{"result": int, "tie": int} | None, ...], ...],
          "flat": [str, ...]   # 按顺序的 "B"/"P"/"T" 序列（仅非空格）
        }
    """
    r = BitReader(b64)
    version, n = _read_header(r)
    columns: list[list] = []
    col: list = []
    flat: list[str] = []
    for _ in range(n):
        if len(col) >= COLUMN_SIZE:
            columns.append(col)
            col = []
        flag = r.read(1)
        if flag == 1:
            result = r.read(2)
            tie = r.read(4)
            col.append({"result": result, "tie": tie})
            flat.append(RESULT_MAP.get(result, "?"))
        else:
            col.append(None)
    if col:
        columns.append(col)
    return {"version": version, "columns": columns, "flat": flat}


def decode_bead_plate(b64: str) -> dict:
    """解码珠盘（beatPlateRoad / MAIN_ROAD）。

    Returns:
        {
          "version": int,
          "columns": [[{"result": int, "pair": int} | None, ...], ...],
          "flat": [str, ...]
        }
    """
    r = BitReader(b64)
    version, n = _read_header(r)
    columns: list[list] = []
    col: list = []
    flat: list[str] = []
    for _ in range(n):
        if len(col) >= COLUMN_SIZE:
            columns.append(col)
            col = []
        flag = r.read(1)
        if flag == 1:
            result = r.read(2)
            pair = r.read(2)
            col.append({"result": result, "pair": pair})
            flat.append(RESULT_MAP.get(result, "?"))
        else:
            col.append(None)
    if col:
        columns.append(col)
    return {"version": version, "columns": columns, "flat": flat}


# 各 roadPaper 键 → 解码器
ROAD_DECODERS = {
    "bigRoad": decode_big_road,
    "notOutBigRoad": decode_big_road,
    "winLoseRoad": decode_big_road,
    "beatPlateRoad": decode_bead_plate,
    "beatPlateRoad2": decode_bead_plate,
}


def decode_road_paper(road_paper: dict) -> dict:
    """解码整包 roadPaper dict，返回 {键: 解码结果}。未知键跳过。"""
    out = {}
    for key, b64 in (road_paper or {}).items():
        decoder = ROAD_DECODERS.get(key)
        if decoder and isinstance(b64, str) and b64:
            try:
                out[key] = decoder(b64)
            except Exception:
                continue
    return out


def decode_single_round_road(b64: str, road_type: int = 1) -> dict | None:
    """解码单局增量路纸（SINGLE_ROAD=161 帧的 beatPlateRoad）。

    与 decode_bead_plate（全量 boot 格式）不同，161 帧是**单局增量**，
    头部结构为 version/row/column（非 version/n_hi/n_lo）：
        version = read(8) + 1
        row     = read(8)
        column  = read(8)
        flag    = read(1)
        MAIN_ROAD(1): flag=1 → result=read(2), pair=read(2)
                      flag=0 → read(4)（空位）
        MAIN_ROAD2(111): flag=1 → result(2)+pair(2)+isSeven(2)+isSix(2)+read(3)
                         flag=0 → read(11)

    Returns:
        {"version": int(局序号), "row": int, "column": int,
         "result": "B"/"P"/"T"/"B6"/None(空), "pair": int}
        解析失败返回 None。
    """
    if not b64:
        return None
    try:
        r = BitReader(b64)
        version = r.read(8) + 1
        row = r.read(8)
        column = r.read(8)
        flag = r.read(1)
        if road_type == 1:  # MAIN_ROAD / MAIN_ROAD3
            if flag == 1:
                result = r.read(2)
                pair = r.read(2)
                return {"version": version, "row": row, "column": column,
                        "result": RESULT_MAP.get(result, "?"), "pair": pair}
            r.read(4)
            return {"version": version, "row": row, "column": column,
                    "result": None, "pair": 0}
        if road_type == 111:  # MAIN_ROAD2
            if flag == 1:
                result = r.read(2)
                pair = r.read(2)
                r.read(2)  # isSeven
                r.read(2)  # isSix
                r.read(3)
                return {"version": version, "row": row, "column": column,
                        "result": RESULT_MAP.get(result, "?"), "pair": pair}
            r.read(11)
            return {"version": version, "row": row, "column": column,
                    "result": None, "pair": 0}
        return {"version": version, "row": row, "column": column,
                "result": None, "pair": 0}
    except Exception:
        return None
