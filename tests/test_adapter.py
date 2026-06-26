import pytest
from htools.types import TickSide, MarketTick
from hdt.adapters.leyu_adapter import LeyuAdapter, BET_AREA_MAP


class TestLeyuAdapter:
    def setup_method(self):
        self.adapter = LeyuAdapter()

    def test_banker_maps_to_long(self):
        tick = self.adapter.create_tick("banker", 8, table_id=2718)
        assert tick.side == TickSide.LONG
        assert tick.metadata["table_no"] == 2718
        assert tick.score == 8

    def test_player_maps_to_short(self):
        tick = self.adapter.create_tick("player", 2, table_id=2718)
        assert tick.side == TickSide.SHORT

    def test_tie_maps_to_flat(self):
        tick = self.adapter.create_tick("tie", 5, table_id=2718)
        assert tick.side == TickSide.FLAT

    def test_counter_id_and_trade_seq(self):
        tick = self.adapter.create_tick(
            "banker", 9, table_id=2718,
            counter_id="U11", trade_seq="GB05266066BD",
        )
        assert tick.counter_id == "U11"
        assert tick.trade_seq == "GB05266066BD"

    def test_status_and_countdown(self):
        tick = self.adapter.create_tick(
            "banker", 8, table_id=2718, status="结算中", countdown=9,
        )
        assert tick.status == "结算中"
        assert tick.countdown == 9

    def test_countdown_none(self):
        tick = self.adapter.create_tick("banker", 8, table_id=2718)
        assert tick.countdown is None

    def test_long_short_score(self):
        tick = self.adapter.create_tick(
            "banker", 8, table_id=2718, long_score=9, short_score=2,
        )
        assert tick.long_score == 9
        assert tick.short_score == 2

    def test_side_sequence(self):
        """side_sequence 为标准字段，非 metadata"""
        tick = self.adapter.create_tick(
            "banker", 1, table_id=2718,
            road_sequence=["B", "P", "B", "B", "T"],
        )
        assert tick.side_sequence == ["L", "S", "L", "L", "F"]
        assert "road_seq" not in tick.metadata

    def test_empty_side_sequence(self):
        tick = self.adapter.create_tick("banker", 1, table_id=2718)
        assert tick.side_sequence == []

    def test_table_no_int(self):
        tick = self.adapter.create_tick("banker", 8, table_id=2718)
        assert tick.metadata["table_no"] == 2718
        assert isinstance(tick.metadata["table_no"], int)

    def test_table_type_id(self):
        tick = self.adapter.create_tick("banker", 8, table_id=2718, table_type_id=2001)
        assert tick.metadata["table_type_id"] == 2001

    def test_bet_fields_as_standard(self):
        """投注字段为标准字段"""
        bets = {
            "total": {"amount_raw": "39.1K", "amount": 39100, "count": 196},
            "areas": {
                "庄": {"amount_raw": "16.2K", "amount": 16200, "count": 94},
                "闲": {"amount_raw": "22.4K", "amount": 22400, "count": 95},
                "和": {"amount_raw": "350", "amount": 350, "count": 3},
            },
        }
        tick = self.adapter.create_tick("banker", 8, table_id=2718, bets=bets)
        assert tick.bet_total_amount == 39100
        assert tick.bet_total_count == 196
        assert tick.bet_long_amount == 16200
        assert tick.bet_long_count == 94
        assert tick.bet_short_amount == 22400
        assert tick.bet_short_count == 95
        assert tick.bet_flat_amount == 350
        assert tick.bet_flat_count == 3
        assert "bet_total_amount" not in tick.metadata
        assert "bet_long_amount" not in tick.metadata

    def test_extra_metadata_merged(self):
        tick = self.adapter.create_tick(
            "banker", 8, table_id=2718,
            extra_metadata={
                "player_cards": "8 9",
                "server_time": "17:59 (UTC+08)",
                "dealer": "荷官A",
            },
        )
        assert tick.metadata["player_cards"] == "8 9"
        assert tick.metadata["server_time"] == "17:59 (UTC+08)"
        assert tick.metadata["dealer"] == "荷官A"
