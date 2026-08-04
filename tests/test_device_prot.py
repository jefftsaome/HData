"""deviceId 按账号持久化 + 10115 PROT_DECODE_CONFIG 热更新的单元测试。"""

import json

import pytest

from hdata.auth import session as session_mod
from hdata.protocol import codec as codec_mod

# ── get_persistent_device_id ────────────────────────────────────────────────

@pytest.fixture()
def device_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_DEVICE_ID_DIR", tmp_path)
    return tmp_path


class TestPersistentDeviceId:
    def test_same_account_twice_identical(self, device_dir):
        a = session_mod.get_persistent_device_id("linbing1")
        b = session_mod.get_persistent_device_id("linbing1")
        assert a == b

    def test_different_accounts_differ(self, device_dir):
        a = session_mod.get_persistent_device_id("linbing1")
        b = session_mod.get_persistent_device_id("liyu686")
        assert a != b

    def test_persisted_to_file_and_reloaded(self, device_dir):
        a = session_mod.get_persistent_device_id("tc686")
        path = device_dir / "device_id_tc686.txt"
        assert path.read_text(encoding="utf-8").strip() == a
        # 删掉内存影响无从模拟进程重启，但文件内容决定返回值——
        # 改写文件后再次调用应读到新内容（两段式有效）
        path.write_text("1784893260093339843-95557669", encoding="utf-8")
        assert session_mod.get_persistent_device_id("tc686") == \
            "1784893260093339843-95557669"

    def test_two_segment_format(self, device_dir):
        fixed = session_mod.get_persistent_device_id("acc1")
        head, tail = fixed.split("-")
        assert head.isdigit() and tail.isdigit()
        assert len(tail) == 8

    def test_legacy_three_segment_cache_regenerated(self, device_dir):
        path = device_dir / "device_id_acc2.txt"
        path.write_text("1784893260093339843-95557669-55953617", encoding="utf-8")
        fixed = session_mod.get_persistent_device_id("acc2")
        assert fixed.count("-") == 1  # 旧三段式视为无效，重新生成两段式

    def test_empty_account_not_written(self, device_dir):
        fixed = session_mod.get_persistent_device_id("")
        assert fixed.count("-") == 1
        assert list(device_dir.glob("device_id_*.txt")) == []

    def test_build_ws_config_uses_persistent_id(self, device_dir):
        gs = {
            "account": "linbing1",
            "game_token": "tok",
            "game_player_id": 123,
            "game_backend": "6pwn4i.com:4999",
        }
        c1 = session_mod.build_ws_config(gs)
        c2 = session_mod.build_ws_config(gs)
        # 前两段持久一致，第三段每条连接随机
        assert c1["device_id"].rsplit("-", 1)[0] == c2["device_id"].rsplit("-", 1)[0]
        assert c1["device_id"].count("-") == 2
        assert c1["device_id"] in c1["ws_url"]


# ── update_protocol_codec_config ─────────────────────────────────────────────

@pytest.fixture()
def codec_state(tmp_path, monkeypatch):
    """隔离 PROTOCOL_CODEC_CONFIG 与缓存文件，测试后恢复。"""
    cache_file = tmp_path / "protocol_codec_config.json"
    monkeypatch.setattr(codec_mod, "_PROTO_CODEC_CACHE", cache_file)
    saved = dict(codec_mod.PROTOCOL_CODEC_CONFIG)
    yield cache_file
    codec_mod.PROTOCOL_CODEC_CONFIG.clear()
    codec_mod.PROTOCOL_CODEC_CONFIG.update(saved)


class TestUpdateProtocolCodecConfig:
    def test_string_shorthand_updates(self, codec_state):
        changes = codec_mod.update_protocol_codec_config({"10089_7": "newhash"})
        assert changes == {"10089_7": ("488198aea6cc35d50f84b2cc327c1d68f7f26edf40fc83ae9d45a057a743a6a0", "newhash")}
        assert codec_mod.PROTOCOL_CODEC_CONFIG["10089_7"] == "newhash"

    def test_dict_form_updates(self, codec_state):
        changes = codec_mod.update_protocol_codec_config(
            {"10053_7": {"version": "v2hash", "state": 1, "schemas": []}})
        assert "10053_7" in changes
        assert codec_mod.PROTOCOL_CODEC_CONFIG["10053_7"] == "v2hash"

    def test_state_zero_removes_key(self, codec_state):
        changes = codec_mod.update_protocol_codec_config(
            {"301_2": {"version": "x", "state": 0}})
        assert changes == {"301_2": ("05af08d70e640b244d96da4e9e9f29aeddac6c102685fe6aa5c762d2dc7ce64e", None)}
        assert "301_2" not in codec_mod.PROTOCOL_CODEC_CONFIG

    def test_no_change_returns_empty_and_no_file(self, codec_state):
        same = codec_mod.PROTOCOL_CODEC_CONFIG["10089_7"]
        changes = codec_mod.update_protocol_codec_config({"10089_7": same})
        assert changes == {}
        assert not codec_state.exists()

    def test_changes_written_to_cache(self, codec_state):
        codec_mod.update_protocol_codec_config({"9999_7": "zzz"})
        data = json.loads(codec_state.read_text(encoding="utf-8"))
        assert data["9999_7"] == "zzz"
        # 默认表里的键也一并持久化
        assert data["10089_7"] == \
            "488198aea6cc35d50f84b2cc327c1d68f7f26edf40fc83ae9d45a057a743a6a0"

    def test_bad_input_tolerated(self, codec_state):
        assert codec_mod.update_protocol_codec_config(None) == {}
        assert codec_mod.update_protocol_codec_config([1, 2]) == {}
        assert codec_mod.update_protocol_codec_config(
            {"k": {"state": 1}}) == {}          # 缺 version 跳过
        assert codec_mod.update_protocol_codec_config(
            {"k": {"version": "", "state": 1}}) == {}  # 空 version 跳过
