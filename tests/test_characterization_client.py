"""client.py 纯函数特征测试网（行为锁定，非规范断言）。

本文件锁定 hdata/client.py 四个纯/叶子函数的**当前**输出，供
Task 8 拆分 client.py 时做回归网。断言值全部由实跑发现，是
"现状"而非"期望"；若日后代码行为变更导致断言失败，请先确认
是否是有意变更，再决定是否更新断言（特征测试的纪律：以现状为准）。

既有覆盖说明（2026-08-04 核对）：
  - round_result_token 核心映射已由 tests/test_roadpaper.py 覆盖，
    这里只补其未覆盖的边界分支（空白、>9 点数、幸运6平局、多段）；
  - _table_info_from_snapshot 核心字段已由 tests/test_table_info.py
    覆盖，这里只补其未覆盖的 roadPaper 解码、meta 兜底、非法 table_id 分支；
  - _classify_event、build_hall_switch_msg 为全新覆盖。
"""
import json

import pytest

import hdata.client as hc

round_result_token = hc.round_result_token
_classify_event = hc._classify_event
_table_info_from_snapshot = hc._table_info_from_snapshot
build_hall_switch_msg = hc.build_hall_switch_msg


# ── round_result_token（107 roundResult "庄点;闲点" → 路纸 token） ──
# 核心分支见 tests/test_roadpaper.py，此处只锁定其未覆盖的分支。


def test_round_result_token_strips_spaces_and_handles_over_nine():
    assert round_result_token(" 9 ; 5 ") == "B"
    assert round_result_token("10;5") == "B"
    assert round_result_token("0;10") == "P"


def test_round_result_token_banker_six_only_on_win():
    assert round_result_token("6;6") == "T"   # 幸运6只在庄胜时触发


def test_round_result_token_multi_part_or_non_int_returns_empty():
    assert round_result_token("1;2;3") == ""  # 第二段含 ";" 转 int 失败
    assert round_result_token("1;x") == ""


# ── _classify_event（协议号 → 事件类型名） ──


@pytest.mark.parametrize("protocol_id,expected", [
    (102, "leave"),      # 离桌推送（主动/被踢）
    (103, "boot"),       # 新靴/洗牌开始
    (104, "round"),      # 局状态
    (106, "card"),       # 发牌
    (107, "card"),       # 牌局事件
    (110, "bet"),        # 桌台动态
    (116, "road"),       # 路纸
    (123, "notice"),     # 系统通知
    (160, "road"),       # 路纸更新
    (161, "road"),       # 路纸更新
    (171, "status"),     # 桌台状态
    (305, "status"),     # 桌台故障状态变更（TABLEFAULT_STATUS_CHANGE）
    (10052, "lobby"),    # 大厅快照
    (3, "other"),        # 心跳等未分类协议
    (101, "other"),
    (10027, "other"),
    (10089, "other"),
    (99999, "other"),
])
def test_classify_event_locks_current_mapping(protocol_id, expected):
    assert _classify_event(protocol_id) == expected


# ── _table_info_from_snapshot（10052 快照 → TableInfo） ──
# 真实珠盘数据（table 2659, 35 张牌，其中 3 个幸运6庄 → 38 字符合并串，
# 见 tests/test_roadpaper.py::test_decode_real_bead_plate）。

_REAL_BEAD_PLATE = "IgYGpSGIUhSFKUhCnYWpGlIUh2kOQpSEIABQWCgo"
_REAL_ROAD_FLAT = "BBPTPBPBPBBBPPBB6PBBPBBPBPB6BPB6PBBPPP"


def _snap(**kw):
    base = {
        "tableId": 2659,
        "gameTypeId": 2001,
        "gameStatus": 2,
        "bootNo": "B0J27",
        "tableName": "桌台2659",
        "tableOnline": {"onlineNumber": 42, "totalAmount": 0},
        "goodRoadPoints": [
            {"goodRoadType": 2, "goodRoadFlag": True},
            {"goodRoadType": 1, "goodRoadFlag": False},
        ],
    }
    base.update(kw)
    return base


def test_road_paper_decodes_flat_and_count():
    t = _table_info_from_snapshot(
        "2659", _snap(roadPaper={"beatPlateRoad": _REAL_BEAD_PLATE}))
    assert t.road_flat == _REAL_ROAD_FLAT
    assert t.road_count == 38


def test_road_paper_broken_base64_returns_empty_flat():
    t = _table_info_from_snapshot(
        "2659", _snap(roadPaper={"beatPlateRoad": "!!!notbase64!!!"}))
    assert t.road_flat == ""
    assert t.road_count == 0


def test_boot_no_snapshot_wins_over_meta():
    meta = {2659: {"bootNo": "M-BOOT"}}
    t = _table_info_from_snapshot("2659", _snap(bootNo="T-BOOT"), meta)
    assert t.boot_no == "T-BOOT"
    t2 = _table_info_from_snapshot("2659", _snap(bootNo=""), meta)
    assert t2.boot_no == "M-BOOT"


def test_table_name_and_game_type_name_meta_override():
    meta = {2659: {"tableName": "官方桌名", "gameTypeName": "官方玩法名"}}
    t = _table_info_from_snapshot("2659", _snap(), meta)
    assert t.table_name == "官方桌名"
    assert t.game_type_name == "官方玩法名"
    t2 = _table_info_from_snapshot("2659", _snap(tableName=""), meta)
    assert t2.table_name == "官方桌名"


def test_meta_for_wrong_table_id_is_ignored():
    t = _table_info_from_snapshot("1", _snap(tableName=""), {999: {"tableName": "X"}})
    assert t.table_name == ""


def test_status_defaults_to_zero_when_missing():
    assert _table_info_from_snapshot("1", {"gameTypeId": 2001}).status == 0
    assert _table_info_from_snapshot(
        "1", {"gameTypeId": 2001, "gameStatus": 0}).status == 0


def test_invalid_table_id_returns_none():
    assert _table_info_from_snapshot("abc", _snap()) is None
    assert _table_info_from_snapshot("1", {"gameStatus": 2}) is None


def test_full_snapshot_to_dict_locked():
    out = _table_info_from_snapshot("2659", _snap()).to_dict()
    assert out == {
        "table_id": 2659,
        "game_type_id": 2001,
        "game_type_name": "经典百家乐",
        "table_name": "桌台2659",
        "status": 2,
        "online": 42,
        "total_amount": 0,
        "boot_no": "B0J27",
        "road_flat": "",
        "road_count": 0,
        "good_roads": ["长庄"],
    }


# ── build_hall_switch_msg（大厅订阅消息，10027） ──


def test_build_hall_switch_msg_locks_structure_and_param(monkeypatch):
    # 固定时区偏移：UTC+8（timezone=28800, daylight=0）→ offsetMinutes=-480。
    monkeypatch.setattr(hc.time, "daylight", 0)
    monkeypatch.setattr(hc.time, "timezone", 28800)
    monkeypatch.setattr(hc.time, "altzone", 28800)

    msg = build_hall_switch_msg(12345, "device-abc")

    assert msg["protocolId"] == 10027
    assert msg["gameTypeId"] == 2013
    assert msg["tableId"] == 0
    assert msg["serviceTypeId"] == 7
    assert msg["playerId"] == 12345
    assert set(msg) == {"jsonData", "nonce", "protocolId", "gameTypeId",
                        "sign", "timestamp", "playerId", "tableId",
                        "serviceTypeId"}
    assert isinstance(msg["nonce"], int) and isinstance(msg["timestamp"], int)
    assert isinstance(msg["sign"], str) and len(msg["sign"]) == 28

    expected_param = json.dumps(
        {"groupId": 41, "isAll": 1, "deviceType": 15,
         "deviceId": "device-abc",
         "timeZoneArea": "Asia/Shanghai", "offsetMinutes": -480},
        separators=(",", ":"))
    expected_json_data = json.dumps(
        {"id": 10027, "param": expected_param}, separators=(",", ":"))

    assert msg["jsonData"] == expected_json_data
    parsed = json.loads(msg["jsonData"])
    assert parsed["id"] == 10027
    assert parsed["param"] == expected_param
    assert json.loads(parsed["param"]) == {
        "groupId": 41, "isAll": 1, "deviceType": 15,
        "deviceId": "device-abc",
        "timeZoneArea": "Asia/Shanghai", "offsetMinutes": -480,
    }
