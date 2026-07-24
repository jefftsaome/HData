"""登录帧（Fs.Login=10000）构造测试：protocolCodecConfig 六哈希 + deviceId 三段式。

背景：2026-07-24 浏览器登录帧抓包确认，真实客户端登录必须携带
protocolCodecConfig 六个 schema 哈希（静态值），hdata 历史版本发空表 {}；
deviceId 为三段式 "{fixedDeviceId两段}-{每连接随机8位}"，hdata 历史版本
只生成两段。两处均已对齐真实客户端，本文件锁定行为防回归。
"""
import json
import re

from hdata.auth.session import ensure_device_id_suffix, generate_device_id
from hdata.protocol.codec import PROTOCOL_CODEC_CONFIG, build_login_msg


def _login_param(msg: dict) -> dict:
    """build_login_msg 返回 {jsonData: str}，jsonData.param 是嵌套 JSON 字符串。"""
    outer = json.loads(msg["jsonData"])
    return json.loads(outer["param"])


class TestProtocolCodecConfig:
    def test_login_msg_carries_six_hashes(self):
        param = _login_param(build_login_msg("tok", 1, "dev"))
        cfg = param["protocolCodecConfig"]
        assert len(cfg) == 6
        for key, val in cfg.items():
            # 键形态 {protocolId}_{schema版本}
            assert re.fullmatch(r"\d+_\d+", key), key
            # 值为 64 hex（schema 内容哈希）
            assert re.fullmatch(r"[0-9a-f]{64}", val), (key, val)

    def test_config_matches_constant(self):
        param = _login_param(build_login_msg("tok", 1, "dev"))
        assert param["protocolCodecConfig"] == PROTOCOL_CODEC_CONFIG

    def test_returned_config_is_a_copy(self):
        msg = build_login_msg("tok", 1, "dev")
        param = _login_param(msg)
        param["protocolCodecConfig"]["10053_7"] = "tampered"
        assert PROTOCOL_CODEC_CONFIG["10053_7"] != "tampered"

    def test_other_fields_unchanged(self):
        param = _login_param(build_login_msg("tok123", 42, "dev-x"))
        assert param["jwtToken"] == "tok123"
        assert param["deviceType"] == 15
        assert param["deviceId"] == "dev-x"
        assert param["version"] == "1.1.1"


class TestDeviceId:
    _THREE_SEG = re.compile(r"^\d{19}-\d{8}-\d{8}$")

    def test_generate_is_three_segments(self):
        did = generate_device_id()
        assert self._THREE_SEG.fullmatch(did), did

    def test_generate_random_third_segment(self):
        a, b = generate_device_id(), generate_device_id()
        assert a.split("-")[-1] != b.split("-")[-1]

    def test_ensure_two_segments_appends_third(self):
        fixed = "1784893260093339843-95557669"
        out = ensure_device_id_suffix(fixed)
        assert out.startswith(fixed + "-")
        assert self._THREE_SEG.fullmatch(out), out

    def test_ensure_three_segments_passthrough(self):
        full = "1784893260093339843-95557669-55953617"
        assert ensure_device_id_suffix(full) == full

    def test_ensure_empty_generates_new(self):
        out = ensure_device_id_suffix("")
        assert self._THREE_SEG.fullmatch(out), out
