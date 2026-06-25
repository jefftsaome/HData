import pytest
from hdt.sources import CDPSource, WSSource


class TestCDPSource:
    @pytest.mark.asyncio
    async def test_cdp_source_id(self):
        ss = CDPSource()
        assert ss.id == "cdp_source"
        assert ss.name == "CDP Source"

    @pytest.mark.asyncio
    async def test_cdp_source_yields_ticks(self):
        ss = CDPSource()
        ticks = []
        async for tick in ss.start():
            ticks.append(tick)
            break
        await ss.stop()
        assert len(ticks) == 1
        assert ticks[0].side.name in ("LONG", "SHORT", "FLAT")


class TestWSSource:
    @pytest.mark.asyncio
    async def test_ws_source_id(self):
        ps = WSSource(table_id=2718)
        assert ps.id == "ws_source"

    @pytest.mark.asyncio
    async def test_ws_source_select_table(self):
        ps = WSSource()
        result = await ps.select_table(2718, 2001)
        assert result is True
