"""_table_info_from_snapshot 构造 TableInfo 的单元测试（合成数据，不联网）。"""
from hdata.client import GOOD_ROAD_NAMES, _table_info_from_snapshot


def _snap(**kw):
    base = {
        "gameTypeId": 2001,
        "gameStatus": 2,
        "bootNo": "B0J27",
        "tableOnline": {"onlineNumber": 42, "totalAmount": 100.0},
    }
    base.update(kw)
    return base


class TestTableInfoFromSnapshot:
    def test_basic_fields(self):
        t = _table_info_from_snapshot("2718", _snap())
        assert t is not None
        assert t.table_id == 2718
        assert t.game_type_name == "经典百家乐"
        assert t.online == 42
        assert t.status == 2
        assert t.good_roads == []

    def test_meta_overrides_name(self):
        meta = {2718: {"tableName": "经典百家乐J27",
                       "gameTypeName": "经典百家乐"}}
        t = _table_info_from_snapshot("2718", _snap(), meta)
        assert t.table_name == "经典百家乐J27"

    def test_unknown_game_type(self):
        t = _table_info_from_snapshot("1", _snap(gameTypeId=2999))
        assert t.game_type_name == "类型2999"

    def test_good_roads_only_active(self):
        snap = _snap(goodRoadPoints=[
            {"goodRoadType": 2, "goodRoadFlag": True},    # 长庄 生效
            {"goodRoadType": 1, "goodRoadFlag": False},   # 长闲 未生效
            {"goodRoadType": 9, "goodRoadFlag": True},    # 逢庄连 生效
        ])
        t = _table_info_from_snapshot("1", snap)
        assert t.good_roads == ["长庄", "逢庄连"]

    def test_good_roads_unknown_type(self):
        snap = _snap(goodRoadPoints=[{"goodRoadType": 99, "goodRoadFlag": True}])
        t = _table_info_from_snapshot("1", snap)
        assert t.good_roads == ["类型99"]

    def test_good_roads_empty_and_missing(self):
        assert _table_info_from_snapshot("1", _snap()).good_roads == []
        assert _table_info_from_snapshot(
            "1", _snap(goodRoadPoints=None)).good_roads == []

    def test_no_game_type_returns_none(self):
        assert _table_info_from_snapshot("1", {"gameStatus": 2}) is None

    def test_to_dict_contains_good_roads(self):
        t = _table_info_from_snapshot("1", _snap())
        d = t.to_dict()
        assert "good_roads" in d and isinstance(d["good_roads"], list)

    def test_total_amount_normal(self):
        """tableOnline.totalAmount 正常下发时要接到 TableInfo 上。"""
        t = _table_info_from_snapshot("1", _snap())
        assert t.total_amount == 100.0
        assert t.to_dict()["total_amount"] == 100.0

    def test_total_amount_missing(self):
        """tableOnline 缺键或整个没有时兜底 0，不报错。"""
        t = _table_info_from_snapshot("1", _snap(
            tableOnline={"onlineNumber": 7}))
        assert t.total_amount == 0
        t2 = _table_info_from_snapshot("1", _snap(tableOnline=None))
        assert t2.total_amount == 0 and t2.online == 0

    def test_total_amount_zero(self):
        """平台恒推 0 的现状：如实存 0（数据样本.md：121 次观测全 0）。"""
        t = _table_info_from_snapshot("1", _snap(
            tableOnline={"onlineNumber": 6, "totalAmount": 0}))
        assert t.total_amount == 0 and t.online == 6

    def test_good_road_names_complete(self):
        assert GOOD_ROAD_NAMES[1] == "长闲"
        assert GOOD_ROAD_NAMES[2] == "长庄"
        assert len(GOOD_ROAD_NAMES) == 11
