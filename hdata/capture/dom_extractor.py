"""DOM 提取器 — 通过 CDP 在浏览器中执行 JS 提取游戏页面数据"""

import json
import re
from pathlib import Path

from htools.utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
#  JavaScript 代码片段（在浏览器 iframe 中执行）
# ═══════════════════════════════════════════════════════════════

FIXED_INFO_JS = r"""(function() {
    const g = s => {
        const e = document.querySelector(s);
        return e ? e.innerText.replace(/\n/g, ' ').trim() : '';
    };
    const points = document.querySelectorAll('.main-bet-point');
    const allBetPoints = [];
    for (const p of points) {
        const t = p.innerText.replace(/\n/g, ' ').trim();
        if (t) allBetPoints.push(t);
    }
    return JSON.stringify({
        tableName: g('.tableName'),
        dealer: g('.dealerName'),
        limit: g('.game-header-betLimit'),
        allBetPoints,
    });
})()"""

_JS_PATH = Path(__file__).parent / "js" / "dynamic_extract.js"
DYNAMIC_EXTRACT_JS = _JS_PATH.read_text(encoding="utf-8")


def parse_fixed_info(data: dict) -> dict:
    """从 JS 返回的固定信息中提取结构化数据。

    Args:
        data: FIXED_INFO_JS 返回的 dict，包含 tableName/dealer/limit/allBetPoints

    Returns:
        FixedData dict：{game_name, table_id, gameplay, bet_limit, dealer, odds}
    """
    name = data.get("tableName", "")
    # 提取桌台 ID：末尾的大写字母+数字，如 "A01"、"U11"
    tid = ""
    gameplay = name
    m_tid = re.search(r"([A-Z]+\d+)$", name)
    if m_tid:
        tid = m_tid.group(1)
        gameplay = name[:m_tid.start()].strip()

    # 解析赔率
    odds = {}
    for text in data.get("allBetPoints", []):
        m = re.match(r"([\u4e00-\u9fff]+)(\d+(?:\.\d+)?)(.*)", text)
        if m:
            odds[m.group(1)] = {"odds": m.group(2), "rest": m.group(3).strip()}

    return {
        "game_name": name,
        "table_id": tid,
        "gameplay": gameplay,
        "bet_limit": data.get("limit", ""),
        "dealer": data.get("dealer", ""),
        "odds": odds,
    }


class DOMExtractor:
    """CDP DOM 提取器 — 封装 JS 注入和结果解析。

    通过 CDPSession 在游戏页面中执行 JavaScript，提取 DOM 数据，
    并调用 parse_fixed_info / parse_dynamic 做结构化解析。
    """

    def __init__(self, cdp_session):
        self._cdp = cdp_session
        self._fixed_info: dict | None = None

    @property
    def fixed_info(self) -> dict | None:
        return self._fixed_info

    async def extract_fixed_info(self) -> dict | None:
        """提取桌台固定信息（名称、庄家、限红、赔率），缓存后复用。

        Returns:
            FixedData dict，首次成功后缓存，后续直接返回缓存
        """
        if self._fixed_info is not None:
            return self._fixed_info

        result = await self._cdp.evaluate(FIXED_INFO_JS)
        if not result:
            return None

        raw_json = result.get("value")
        if not raw_json:
            return None

        try:
            data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except (json.JSONDecodeError, TypeError):
            return None

        self._fixed_info = parse_fixed_info(data)
        return self._fixed_info

    def reset_fixed_info(self):
        """清除缓存（换台时调用）"""
        self._fixed_info = None

    async def extract_dynamic(self) -> dict | None:
        """提取动态数据（局号、状态、卡牌、投注等）。

        Returns:
            原始 JS 返回的 dict，包含 roundId/status/cards/bets 等字段
        """
        result = await self._cdp.evaluate(DYNAMIC_EXTRACT_JS)
        if not result:
            return None

        raw_json = result.get("value")
        if not raw_json:
            return None

        try:
            return json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except (json.JSONDecodeError, TypeError):
            return None
