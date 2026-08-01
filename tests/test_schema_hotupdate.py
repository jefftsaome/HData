"""schema 热更新回归测试（2026-08-02 真实事故素材）。

事故：服务器 2026-07 下旬给 10089_7 HallGameTable 插入 allGameTopSort
字段，hdata 解码仍用 7-14 bundle 内置旧 schema → 逐元素错位：
真桌半数以上丢 tableId/gameTypeId（实测 231 真桌只解出 ~116 条
带 tableId），body/bits 各剩大量字节。用官方 bundle 解码器仲裁
结果一致 → 差异在 schema 版本而非解码器实现。schema 热更新的真实
载体是**登录响应 data 的 protocolCodecConfig**（官方 onLoginResp →
syncProtocolConfig），不是 10115 WS 推送。

素材（tests/fixtures/）：
- probe_10089_frame1.b64：2026-08-02 凌晨实抓的 10089 响应原始帧
  （新 schema 编码，231 桌全量，hallDetails 合计 231）
- login_pcc_10089.json：同次登录响应 data.protocolCodecConfig 的
  10089_7 条目（完整新版 schema 定义）
"""

import copy
import json
from pathlib import Path

import pytest

from hdata.protocol import schemacodec
from hdata.protocol._schema_data import SCHEMA_CONFIG
from hdata.protocol.schemacodec import schema_decode, update_schema_config

FIX = Path(__file__).parent / "fixtures"
KEY = "10089_7"


@pytest.fixture()
def frame_b64():
    return (FIX / "probe_10089_frame1.b64").read_text("ascii").strip()


@pytest.fixture()
def server_schema_entry():
    return json.loads((FIX / "login_pcc_10089.json").read_text(
        encoding="utf-8"))[KEY]


@pytest.fixture(autouse=True)
def _restore_schema(server_schema_entry):
    """每个测试后恢复 SCHEMA_CONFIG/_ROOTS，防全局污染。"""
    saved = copy.deepcopy(SCHEMA_CONFIG.get(KEY))
    saved_roots = dict(schemacodec._ROOTS)
    yield
    if saved is None:
        SCHEMA_CONFIG.pop(KEY, None)
    else:
        SCHEMA_CONFIG[KEY] = saved
    schemacodec._ROOTS.clear()
    schemacodec._ROOTS.update(saved_roots)


class TestNewSchemaBaseline:
    """内置基线已与服务器同步：直接解新编码帧 = 231 真桌。"""

    def test_builtin_decodes_231_tables(self, frame_b64):
        obj = schema_decode(KEY, frame_b64)
        hgt = obj["hallGameTable"]
        assert len(hgt) == 231
        assert all(t.get("tableId") for t in hgt)
        # casino 分布与帧内 hallDetails 完全吻合
        from collections import Counter
        got = Counter(t["gameCasinoId"] for t in hgt)
        want = {d["gameCasinoId"]: d["tableNum"]
                for d in obj["hallDetails"]}
        assert dict(got) == want

    def test_builtin_version_matches_server(self, server_schema_entry):
        assert SCHEMA_CONFIG[KEY]["version"] \
            == server_schema_entry["version"]


class TestOldSchemaMisaligns:
    """旧 schema 解新编码帧 → 错位（事故复现，防回归）。"""

    def test_old_schema_produces_246_fake_elements(
            self, frame_b64, server_schema_entry):
        old = copy.deepcopy(SCHEMA_CONFIG[KEY])
        # 构造旧版（去掉 allGameTopSort，恢复 7-14 bundle 形态）
        fields = old["schemas"]["HallGameTable"]
        old["schemas"]["HallGameTable"] = [
            f for f in fields if f["name"] != "allGameTopSort"]
        old["version"] = "old-baseline"
        SCHEMA_CONFIG[KEY] = old
        schemacodec._ROOTS.pop(KEY, None)

        obj = schema_decode(KEY, frame_b64)
        hgt = obj["hallGameTable"]
        # 错位症状：一半丢 tableId（231 真桌只解出 ~116 有 tid）
        with_tid = [t for t in hgt if t.get("tableId")]
        assert len(with_tid) != 231
        assert len(with_tid) < len(hgt)


class TestUpdateSchemaConfig:
    def test_hot_update_switches_decoder(
            self, frame_b64, server_schema_entry):
        old = copy.deepcopy(SCHEMA_CONFIG[KEY])
        fields = old["schemas"]["HallGameTable"]
        old["schemas"]["HallGameTable"] = [
            f for f in fields if f["name"] != "allGameTopSort"]
        old["version"] = "old-baseline"
        SCHEMA_CONFIG[KEY] = old
        schemacodec._ROOTS.pop(KEY, None)
        hgt_old = schema_decode(KEY, frame_b64)["hallGameTable"]
        assert len([t for t in hgt_old if t.get("tableId")]) != 231

        changed = update_schema_config(KEY, server_schema_entry)
        assert changed is True
        obj = schema_decode(KEY, frame_b64)
        assert len(obj["hallGameTable"]) == 231
        assert all(t.get("tableId") for t in obj["hallGameTable"])

    def test_same_version_is_noop(self, server_schema_entry):
        assert update_schema_config(KEY, server_schema_entry) is False

    def test_state_zero_ignored(self, server_schema_entry):
        entry = dict(server_schema_entry, state=0)
        assert update_schema_config(KEY, entry) is False


class TestApplyLoginPayload:
    """_login 成功路径处理登录响应内层 data（schema 热更新载体）。"""

    def test_login_payload_hot_updates_schema(
            self, frame_b64, server_schema_entry):
        from hdata.client import _WSConnection
        old = copy.deepcopy(SCHEMA_CONFIG[KEY])
        fields = old["schemas"]["HallGameTable"]
        old["schemas"]["HallGameTable"] = [
            f for f in fields if f["name"] != "allGameTopSort"]
        old["version"] = "old-baseline"
        SCHEMA_CONFIG[KEY] = old
        schemacodec._ROOTS.pop(KEY, None)

        conn = _WSConnection({"account": "pytest"})
        inner = {
            "totalTable": 231,
            "protocolCodecConfig": {KEY: server_schema_entry},
        }
        # data 是 JSON 字符串（真实帧形态）
        conn._apply_login_payload(
            {"status": 1, "data": json.dumps(inner)})

        assert conn._session["total_table"] == 231
        assert SCHEMA_CONFIG[KEY]["version"] \
            == server_schema_entry["version"]
        obj = schema_decode(KEY, frame_b64)
        assert len(obj["hallGameTable"]) == 231

    def test_malformed_payload_never_raises(self):
        from hdata.client import _WSConnection
        conn = _WSConnection({"account": "pytest"})
        conn._apply_login_payload({"status": 1, "data": "not-json{{{"})
        conn._apply_login_payload({"status": 1, "data": None})
        conn._apply_login_payload({"status": 1})
