"""查看当前游戏页面的卡牌数据

用法: uv run python scripts/debug_cards.py

需要 Chrome 在 9222 端口运行并在游戏页面。

data-value 编码:
    rank = (data-value // 4) + 1    1=A, 2=2, ..., 13=K
    suit = data-value % 4           0=♦, 1=♣, 2=♥, 3=♠
    -2 = 未翻牌
"""

import asyncio, json, aiohttp
from hdata.capture.cdp_bridge import CDPSession

RANK_NAMES = {1:'A', 11:'J', 12:'Q', 13:'K'}
SUIT = {0:'D', 1:'C', 2:'H', 3:'S'}  # ♦=D, ♣=C, ♥=H, ♠=S


def decode(dv: str) -> str:
    v = int(dv)
    if v < 0:
        return '🂠'
    rank = (v // 4) + 1
    suit = v % 4
    return f'{RANK_NAMES.get(rank, rank)}{SUIT[suit]}'


async def main():
    async with aiohttp.ClientSession() as s:
        r = await s.get('http://127.0.0.1:9222/json/version')
        ws_url = (await r.json())['webSocketDebuggerUrl']

    cdp = CDPSession(ws_url)
    await cdp.connect()

    js = '''
    (() => {
        const g = s => document.querySelector(s);
        const p = document.querySelectorAll('.baccarat-card-area-player .turn-poker');
        const b = document.querySelectorAll('.baccarat-card-area-banker .turn-poker');
        return JSON.stringify({
            player: Array.from(p).map(e => e.getAttribute('data-value')),
            banker: Array.from(b).map(e => e.getAttribute('data-value')),
            player_info: (g('.baccarat-card-area-player .all-info-wrap')||{}).innerText || '',
            banker_info: (g('.baccarat-card-area-banker .all-info-wrap')||{}).innerText || '',
        });
    })()
    '''
    r = await cdp.evaluate(js)
    raw = r.get('value', '{}')
    data = json.loads(raw) if isinstance(raw, str) else raw
    await cdp.disconnect()

    print('闲牌:', [decode(dv) for dv in data['player'] if dv != '-2'])
    print('庄牌:', [decode(dv) for dv in data['banker'] if dv != '-2'])
    print('闲总分:', data['player_info'].strip().split('\n')[0])
    print('庄总分:', data['banker_info'].strip().split('\n')[0])


asyncio.run(main())
