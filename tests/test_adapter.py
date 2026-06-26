import pytest
from htools.types import TickSide, MarketTick
from hdt.adapters.leyu_adapter import LeyuAdapter


class TestLeyuAdapter:
    def setup_method(self):
        self.adapter = LeyuAdapter()

    def test_banker_maps_to_long(self):
        """庄 → LONG"""
        tick = self.adapter.create_tick("banker", 8.0, table_id=2718)
        assert tick.side == TickSide.LONG
        assert tick.metadata["table_no"] == "2718"
        assert tick.score == 8.0

    def test_player_maps_to_short(self):
        """闲 → SHORT"""
        tick = self.adapter.create_tick("player", 2.0, table_id=2718)
        assert tick.side == TickSide.SHORT

    def test_tie_maps_to_flat(self):
        """和 → FLAT"""
        tick = self.adapter.create_tick("tie", 5.0, table_id=2718)
        assert tick.side == TickSide.FLAT

    def test_counter_id_and_trade_seq(self):
        """counter_id 和 trade_seq 传递正确"""
        tick = self.adapter.create_tick(
            "banker", 9.0, table_id=2718,
            counter_id="U11", trade_seq="GB05266066BD",
        )
        assert tick.counter_id == "U11"
        assert tick.trade_seq == "GB05266066BD"

    def test_status_and_countdown(self):
        """status 和 countdown 为标准字段"""
        tick = self.adapter.create_tick(
            "banker", 8.0, table_id=2718,
            status="结算中", countdown="9",
        )
        assert tick.status == "结算中"
        assert tick.countdown == "9"

    def test_road_sequence_sanitized(self):
        """路纸序列语义化: B→L, P→S, T→F"""
        tick = self.adapter.create_tick(
            "banker", 1.0, table_id=2718,
            road_sequence=["B", "P", "B", "B", "T"],
        )
        assert tick.metadata["road_seq"] == ["L", "S", "L", "L", "F"]

    def test_empty_road_sequence_no_metadata(self):
        """无路纸时不写入 metadata"""
        tick = self.adapter.create_tick("banker", 1.0, table_id=2718)
        assert "road_seq" not in tick.metadata

    def test_table_no_in_metadata(self):
        """table_id 存入 metadata.table_no"""
        tick = self.adapter.create_tick("banker", 8.0, table_id=2718)
        assert tick.metadata["table_no"] == "2718"

    def test_extra_metadata_merged(self):
        """extra_metadata 被合并到 metadata 中"""
        tick = self.adapter.create_tick(
            "banker", 8.0, table_id=2718,
            extra_metadata={
                "player_cards": "8 9",
                "banker_cards": "A K",
            },
        )
        assert tick.metadata["player_cards"] == "8 9"
        assert tick.metadata["banker_cards"] == "A K"
        assert "road_seq" not in tick.metadata
