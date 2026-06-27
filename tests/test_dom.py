"""测试 DOM 提取与解析模块"""

from hdt.capture.dom_parser import (
    baccarat_value, parse_number, parse_cards, parse_bets,
    parse_dynamic, detect_result, make_fingerprint,
)


class TestBaccaratValue:
    def test_a_is_1(self):
        assert baccarat_value("A") == 1

    def test_number_is_face_value(self):
        assert baccarat_value("8") == 8

    def test_ten_jqk_are_0(self):
        assert baccarat_value("10") == 0
        assert baccarat_value("J") == 0
        assert baccarat_value("Q") == 0
        assert baccarat_value("K") == 0


class TestParseNumber:
    def test_plain_number(self):
        assert parse_number("100") == 100

    def test_k_suffix(self):
        assert parse_number("16.2K") == 16200

    def test_w_suffix(self):
        assert parse_number("39.1W") == 391000

    def test_m_suffix(self):
        assert parse_number("5M") == 5000000


class TestParseCards:
    def test_two_cards(self):
        result = parse_cards("8 9")
        assert len(result) == 2
        assert result[0] == {"display": "8", "baccarat_value": 8}
        assert result[1] == {"display": "9", "baccarat_value": 9}

    def test_face_cards(self):
        result = parse_cards("A K")
        assert result[0]["baccarat_value"] == 1
        assert result[1]["baccarat_value"] == 0

    def test_placeholder_returns_empty(self):
        assert parse_cards("闲") == []


class TestParseBets:
    def test_total_only(self):
        total, areas = parse_bets("39.1K/196本局总投注")
        assert total["amount"] == 39100
        assert total["count"] == 196

    def test_with_areas(self):
        total, areas = parse_bets(
            "39.1K/196本局总投注庄16.2K/94闲22.4K/95和35/3"
        )
        assert areas["庄"]["amount"] == 16200
        assert areas["闲"]["amount"] == 22400
        assert areas["和"]["amount"] == 35


class TestParseDynamic:
    def test_full_parse(self):
        raw = {
            "ts": 1780739998979,
            "roundId": "GB05266066BD",
            "status": "结算中",
            "countdownText": "9",
            "timeDisplay": "17:59 (UTC+08)",
            "tableName": "百家乐A01",
            "player_score_text": "8 9",
            "banker_score_text": "A K",
            "betRaw": "39.1K/196本局总投注庄16.2K/94闲22.4K/95",
            "bootItems": [
                {"icon": "", "value": "57"},
                {"icon": "庄", "value": "24"},
                {"icon": "闲", "value": "26"},
            ],
            "streaks": [],
            "urlTableId": 2718,
            "urlGameType": 2001,
        }
        result = parse_dynamic(raw)
        assert result["round_id"] == "GB05266066BD"
        assert result["status"] == "结算中"
        assert result["countdown_seconds"] == 9
        assert result["cards"]["player_total"] == 7  # (8+9)%10
        assert result["cards"]["banker_total"] == 1  # (1+0)%10
        assert result["boot_stats"]["total_rounds"] == 57
        assert result["boot_stats"]["banker_wins"] == 24


class TestDetectResult:
    def test_player_wins(self):
        dyn = {"cards": {"player_total": 7, "banker_total": 1}}
        assert detect_result(dyn) == "P"

    def test_banker_wins(self):
        dyn = {"cards": {"player_total": 2, "banker_total": 9}}
        assert detect_result(dyn) == "B"

    def test_tie(self):
        dyn = {"cards": {"player_total": 5, "banker_total": 5}}
        assert detect_result(dyn) == "T"

    def test_no_cards_returns_none(self):
        assert detect_result({"cards": {}}) is None


class TestParseCanvasRoads:
    def test_returns_empty_for_none(self):
        from hdt.capture.dom_parser import parse_canvas_roads
        assert parse_canvas_roads(None) == []

    def test_returns_empty_for_empty_dict(self):
        from hdt.capture.dom_parser import parse_canvas_roads
        assert parse_canvas_roads({}) == []

    def test_passes_through_raw_sequence(self):
        from hdt.capture.dom_parser import parse_canvas_roads
        result = parse_canvas_roads({"sequence": ["B", "P", "B", "B", "T"]})
        assert result == ["B", "P", "B", "B", "T"], f"应原样返回: {result}"


class TestMakeFingerprint:
    def test_same_data_same_fingerprint(self):
        dyn1 = {"round_id": "R1", "status": "结算中", "cards": None,
                "bets": None, "boot_stats": None, "countdown_seconds": None}
        dyn2 = dict(dyn1)
        assert make_fingerprint(dyn1, None) == make_fingerprint(dyn2, None)

    def test_different_data_different_fingerprint(self):
        dyn1 = {"round_id": "R1", "status": "结算中", "cards": None,
                "bets": None, "boot_stats": None, "countdown_seconds": None}
        dyn2 = {"round_id": "R2", "status": "结算中", "cards": None,
                "bets": None, "boot_stats": None, "countdown_seconds": None}
        assert make_fingerprint(dyn1, None) != make_fingerprint(dyn2, None)
