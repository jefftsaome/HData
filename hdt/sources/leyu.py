"""LeyuSource — 乐鱼数据源"""

from typing import AsyncIterator

from htools.interfaces import DataSource
from htools.types import MarketTick
from hdt.adapters.leyu_adapter import LeyuAdapter


class LeyuSource(DataSource):
    """乐鱼数据源 — 通过 CDP 桥接采集百家乐数据"""

    def __init__(self):
        self._adapter = LeyuAdapter()
        self._running = False

    @property
    def id(self) -> str:
        return "leyu"

    @property
    def name(self) -> str:
        return "乐鱼数据源"

    async def start(self) -> AsyncIterator[MarketTick]:
        self._running = True
        # 占位：返回一个测试 tick 验证链路
        # 后续迭代从 harvester/tools/leyu_harvester.py 迁移真实采集逻辑
        yield self._adapter.create_tick(
            side=1, price=9.0, table_id="placeholder"
        )
        # 注意：真实实现应包含 while self._running 循环

    async def stop(self):
        self._running = False
