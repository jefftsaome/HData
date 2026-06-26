"""DOM 提取器 — 通过 CDP 在浏览器中执行 JS 提取游戏页面数据"""

import json
import re
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

DYNAMIC_EXTRACT_JS = r"""(function() {
    const g = s => {
        const e = document.querySelector(s);
        return e ? e.innerText.replace(/\n/g, ' ').trim() : '';
    };
    const now = Date.now();

    const roundRaw = g('.roundNo');
    const roundId = roundRaw.replace('\u5c40\u53f7:', '').trim();
    const status = g('.countdown-state');
    const ctext = g('.countdown-text');
    const timeRaw = (g('.time-display') || '').replace(/\s+/g, ' ');
    const tableName = g('.tableName');
    const playerRaw = g('.baccarat-card-area-player');
    const bankerRaw = g('.baccarat-card-area-banker');
    const betRaw = (g('.baccarat-bet-info') || '').replace(/\n/g, '').trim();

    const bootEls = document.querySelectorAll('.baccarat-boot-total-item');
    const bootItems = [];
    for (const item of bootEls) {
        const v = item.querySelector('.baccarat-boot-total-item-value');
        const i = item.querySelector('.baccarat-boot-total-item-icon');
        bootItems.push({
            value: v ? v.innerText.trim() : '',
            icon: i ? i.innerText.trim() : '',
        });
    }

    const bodyText = document.body.innerText;
    const streaks = (bodyText.match(/\d+[\u5c40\u672a\u51fa]/g) || []).map(s => s.trim());

    const urlMatch = window.location.href.match(/\/game\/(\d+)\/(\d+)/);
    const urlTableId = urlMatch ? parseInt(urlMatch[2]) : 0;
    const urlGameType = urlMatch ? parseInt(urlMatch[1]) : 0;
    const gcTableId = (window.GameConst && window.GameConst.JOIN_TABLE_ID)
        ? parseInt(window.GameConst.JOIN_TABLE_ID) : 0;
    const myTableId = urlTableId > 0 ? urlTableId : gcTableId;
    const myGameType = urlGameType > 0 ? urlGameType
        : parseInt((window.GameConst && window.GameConst._GAME_TYPE) || '0');

    return JSON.stringify({
        ts: now,
        roundId: roundId,
        status: status,
        countdownText: ctext,
        timeDisplay: timeRaw || '',
        tableName: tableName,
        playerCards: playerRaw,
        bankerCards: bankerRaw,
        betRaw: betRaw,
        bootItems: bootItems,
        streaks: streaks,
        urlTableId: myTableId,
        urlGameType: myGameType,
        canvasRoad: null,
    });
})()"""


def parse_fixed_info(data: dict) -> dict:
    """从 JS 返回的固定信息中提取结构化数据。

    Args:
        data: FIXED_INFO_JS 返回的 dict，包含 tableName/dealer/limit/allBetPoints

    Returns:
        FixedData dict：{game_name, table_id, gameplay, bet_limit, dealer, odds}
    """
    name = data.get("tableName", "")
    # 提取桌台 ID：末尾的大写字母+数字（如 "A01"、"U11"），
    # 或纯数字（如 "龙争虎斗 01" → "01"）
    tid = ""
    gameplay = name
    m_tid = re.search(r"([A-Z]+\d+)$", name)
    if not m_tid:
        m_tid = re.search(r"(\d+)$", name)
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
