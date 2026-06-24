import pytest
from hdt.sources import StreamSource, PacketSource


class TestStreamSource:
    @pytest.mark.asyncio
    async def test_stream_source_id(self):
        ss = StreamSource()
        assert ss.id == "stream_source"
        assert ss.name == "Stream Source"

    @pytest.mark.asyncio
    async def test_stream_source_yields_ticks(self):
        ss = StreamSource()
        ticks = []
        async for tick in ss.start():
            ticks.append(tick)
            break
        await ss.stop()
        assert len(ticks) == 1
        assert ticks[0].side.name in ("LONG", "SHORT", "FLAT")


class TestPacketSource:
    @pytest.mark.asyncio
    async def test_packet_source_id(self):
        ps = PacketSource(table_id=2718)
        assert ps.id == "packet_source"

    @pytest.mark.asyncio
    async def test_packet_source_select_table(self):
        ps = PacketSource()
        result = await ps.select_table(2718, 2001)
        assert result is True
