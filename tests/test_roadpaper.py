"""roadPaper 位图解码器单元测试"""

import base64

from hdata.protocol.roadpaper import (
    BitReader,
    decode_bead_plate,
    decode_big_road,
    decode_road_paper,
)


def _encode_cells(cells, extra_bits=4):
    """构造一个最小位图：version(8) + rows(8)*cols(8) + 每格 flag+内容。

    cells: list of (flag, content_int) — content 宽度 = extra_bits+2。
    为了简单，行列乘积取 ceil(len/6)*6。
    """
    n = len(cells)
    # 头部 n = r*c >= n，取 c = ceil(n/6), r=6
    import math
    c = max(1, math.ceil(n / 6))
    r = 6
    total = r * c
    # 补空格
    cells = list(cells) + [(0, 0)] * (total - n)

    bits = f"{0:08b}{r:08b}{c:08b}"  # version-1=0, r, c
    for flag, content in cells:
        bits += str(flag)
        bits += f"{content:0{extra_bits + 2}b}"
    # 补齐到字节
    while len(bits) % 8:
        bits += "0"
    raw = int(bits, 2).to_bytes(len(bits) // 8, "big")
    return base64.b64encode(raw).decode()


def test_bit_reader_msb_first():
    raw = bytes([0b10110001, 0b01000000])
    b64 = base64.b64encode(raw).decode()
    r = BitReader(b64)
    assert r.length == 16
    assert r.read(1) == 1
    assert r.read(3) == 0b011
    assert r.read(4) == 0b0001


def test_decode_big_road_basic():
    # 3 格：庄、闲、空 → 珠盘 extra_bits=2 (result2+pair2)
    cells = [(1, 0b0100), (1, 0b0000)]  # result=1(B) pair=0; result=0(P) pair=0
    b64 = _encode_cells(cells, extra_bits=2)
    out = decode_bead_plate(b64)
    assert out["version"] == 1
    assert out["flat"][0] == "B"
    assert out["flat"][1] == "P"


def test_decode_big_road_tie_bits():
    # 大路: result(2) + tieNumber(4)
    cells = [(1, 0b01_0010), (1, 0b10_0001)]  # B tie=2; T(2) tie=1
    b64 = _encode_cells(cells, extra_bits=4)
    out = decode_big_road(b64)
    results = [c for col in out["columns"] for c in col if c]
    assert results[0]["result"] == 1 and results[0]["tie"] == 2
    assert results[1]["result"] == 2 and results[1]["tie"] == 1


def test_decode_real_bead_plate():
    """真实快照数据（table 2659, 35 局）。"""
    b64 = "IgYGpSGIUhSFKUhCnYWpGlIUh2kOQpSEIABQWCgo"
    out = decode_bead_plate(b64)
    assert out["version"] >= 1
    # flat 元素为 "B"/"P"/"T"/"B6"
    assert all(r in ("B", "P", "T", "B6") for r in out["flat"])
    assert len(out["flat"]) == 35
    assert "".join(out["flat"]) == "BBPTPBPBPBBBPPBB6PBBPBBPBPB6BPB6PBBPPP"


def test_decode_road_paper_filters_unknown():
    out = decode_road_paper({
        "bigRoad": "IgYWoUAAAAAgwAAAAAoAAAAAAgAAAAAAoAAAAAAgAAAAAAoUKAAAAgQAAAAA",
        "unknownKey": "xxx",
        "broken": "!!!notbase64!!!",
    })
    assert "bigRoad" in out
    assert "unknownKey" not in out
    assert "broken" not in out


def test_round_tracker_feed_road_paper():
    from hdata.protocol.round_tracker import RoundTracker

    # 真实 35 局珠盘（table 2659）
    rp = {"beatPlateRoad": "IgYGpSGIUhSFKUhCnYWpGlIUh2kOQpSEIABQWCgo"}
    tracker = RoundTracker()
    added = tracker.feed_road_paper(2659, rp)
    hist = tracker.get_history(2659)
    assert added == 35
    assert len(hist) == 35
    # B6 归一为 B
    assert hist[0]["result"] == "B"
    # 幂等：再喂一次不增加
    assert tracker.feed_road_paper(2659, rp) == 0
