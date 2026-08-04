import pytest

from hdata.sources import CDPSource


class TestCDPSource:
    @pytest.mark.asyncio
    async def test_cdp_source_id(self):
        ss = CDPSource()
        assert ss.id == "cdp_source"
        assert ss.name == "CDP Source"
        assert ss.status == "idle"

    @pytest.mark.asyncio
    async def test_cdp_source_set_on_status_change(self):
        ss = CDPSource()
        events = []
        ss.set_on_status_change(lambda e: events.append(e))
        # 直接调用内部 _set_status 测试回调
        ss._set_status("running")
        assert ss.status == "running"
        assert len(events) == 1
        assert events[0]["status"] == "running"
