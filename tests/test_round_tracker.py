from hdt.protocol.round_tracker import RoundTracker, TableState


class TestTableState:
    def test_add_result_increments_streak(self):
        t = TableState(table_id=2718)
        t.add_result("B", 1)
        t.add_result("B", 2)
        assert t.streak_count == 2
        assert t.streak_side == "B"

    def test_different_result_resets_streak(self):
        t = TableState(table_id=2718)
        t.add_result("B", 1)
        t.add_result("B", 2)
        t.add_result("P", 3)  # 换方向
        assert t.streak_side == "P"
        assert t.streak_count == 1

    def test_same_round_id_does_not_duplicate(self):
        t = TableState(table_id=2718)
        t.add_result("B", 1)
        t.add_result("B", 1)  # 同一局
        assert t.streak_count == 1


class TestRoundTracker:
    def test_get_table_creates_new(self):
        rt = RoundTracker()
        t = rt.get_table(2718)
        assert t.table_id == 2718

    def test_feed_returns_signal_on_streak(self):
        rt = RoundTracker()
        for i in range(5):
            sig = rt.feed(2718, "B", i + 1)
            if i < 4:
                assert sig is None
            else:
                assert sig is not None
                assert sig["streak_count"] == 5
                assert sig["streak_side"] == "B"

    def test_feed_tie_does_not_break_streak(self):
        rt = RoundTracker()
        rt.feed(2718, "B", 1)
        rt.feed(2718, "T", 2)
        rt.feed(2718, "B", 3)
        table = rt.get_table(2718)
        assert table.streak_side == "B"
