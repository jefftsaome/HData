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
    const playerScoreRaw = g('.baccarat-card-area-player');
    const bankerScoreRaw = g('.baccarat-card-area-banker');
    const betRaw = (g('.baccarat-bet-info') || '').replace(/\n/g, '').trim();

    // ── 卡牌 data-value ──
    const playerCards = document.querySelectorAll('.baccarat-card-area-player .turn-poker');
    const bankerCards = document.querySelectorAll('.baccarat-card-area-banker .turn-poker');
    const playerCardValues = Array.from(playerCards).map(e => e.getAttribute('data-value'));
    const bankerCardValues = Array.from(bankerCards).map(e => e.getAttribute('data-value'));

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
    const streaks = (bodyText.match(/\d+[\u5c40\u672a\u51fa]{3}/g) || []).map(s => s.trim());

    const urlMatch = window.location.href.match(/\/game\/(\d+)\/(\d+)/);
    const urlTableId = urlMatch ? parseInt(urlMatch[2]) : 0;
    const urlGameType = urlMatch ? parseInt(urlMatch[1]) : 0;
    const gcTableId = (window.GameConst && window.GameConst.JOIN_TABLE_ID)
        ? parseInt(window.GameConst.JOIN_TABLE_ID) : 0;
    const myTableId = urlTableId > 0 ? urlTableId : gcTableId;
    const myGameType = urlGameType > 0 ? urlGameType
        : parseInt((window.GameConst && window.GameConst._GAME_TYPE) || '0');

    // ═══════════════════════════════════════════════════════════════
    //  路纸数据：从 Canvas getImageData 读取大路像素
    //
    //  为什么在浏览器端做像素分析？
    //    - Canvas 全像素数据约 2MB（RGBA），CDP JSON 传输太慢
    //    - 浏览器端遍历 + 颜色分类 < 50ms
    //    - 只传结构化结果 {sequence, stats, matrix}，通常 < 1KB
    // ═══════════════════════════════════════════════════════════════
    // ═══════════════════════════════════════════════════════════════
    //  路纸数据：从 Canvas getImageData 读取大路像素
    //
    //  为什么要在浏览器端做像素分析？
    //    - Canvas 全像素数据约 2MB（RGBA），CDP JSON 传输太慢
    //    - 浏览器端遍历 + 颜色分类 < 50ms
    //    - 只传结构化结果 {sequence, stats, matrix}，通常 < 1KB
    // ═══════════════════════════════════════════════════════════════
    var canvasRoad = null;
    try {
        var canvases = document.querySelectorAll('canvas');
        if (canvases.length > 0) { (function(c) {
            var ctx = c.getContext('2d');
            var w = c.width, ch = c.height;

            function hsv(r,g,b) {
                r/=255; g/=255; b/=255;
                var max=Math.max(r,g,b), min=Math.min(r,g,b), d=max-min;
                var h=0, s=d/(max||1), v=max;
                if (d) {
                    if (max===r) h=((g-b)/d+(g<b?6:0))*60;
                    else if (max===g) h=((b-r)/d+2)*60;
                    else h=((r-g)/d+4)*60;
                }
                return {h:Math.round(h), s:Math.round(s*100), v:Math.round(v*100)};
            }

            function cls(r,g,b) {
                var hh=hsv(r,g,b);
                if (hh.s<30||hh.v<20) return null;
                if ((hh.h<=15||hh.h>=345)&&hh.s>=40&&hh.v>=30) return 'B';
                if (hh.h>=190&&hh.h<=260&&hh.s>=40&&hh.v>=30) return 'P';
                if (hh.h>=80&&hh.h<=160&&hh.s>=40&&hh.v>=30) return 'T';
                return null;
            }

            var mx=w,my=ch,MX=0,MY=0;
            for (var y=0;y<ch;y+=2) for (var x=0;x<w;x+=2) {
                var p=ctx.getImageData(x,y,1,1).data, l=cls(p[0],p[1],p[2]);
                if (l) { if (x<mx) mx=x; if (y<my) my=y; if (x>MX) MX=x; if (y>MY) MY=y; }
            }
            if (mx===w) { canvasRoad=null; return; }
            var bboxH=MY-my+1;

            var hProj=[];
            for (var x=mx; x<=MX; x+=2) {
                var cnt=0;
                for (var y=my; y<=MY; y+=6) { var pp=ctx.getImageData(x,y,1,1).data; if (cls(pp[0],pp[1],pp[2])) cnt++; }
                hProj.push(cnt);
            }
            var bboxW=MX-mx+1;
            var hLen=hProj.length, hMean=0;
            for (var i=0;i<hLen;i++) hMean+=hProj[i];
            hMean/=hLen;
            var hAc=[];
            for (var lag=3; lag<Math.min(hLen/2,100); lag++) {
                var num=0, den1=0, den2=0;
                for (var i=0;i<hLen-lag;i++) { var d1=hProj[i]-hMean, d2=hProj[i+lag]-hMean; num+=d1*d2; den1+=d1*d1; den2+=d2*d2; }
                if (den1>0.001&&den2>0.001) { var r=num/Math.sqrt(den1*den2); hAc.push({lag:lag,r:r}); }
            }
            var estRowH=Math.max(10,Math.round(bboxH/6));
            var cgMin=Math.max(5,Math.round(estRowH*0.7));
            var cgMax=Math.min(200,Math.round(estRowH*3));
            var CG=10, nCols=1, cols=[];
            for (var i=5;i<hAc.length;i++) {
                var p=hAc[i], cg=p.lag*2; if (cg<cgMin||cg>cgMax) continue; if (p.r<0.25) continue;
                if (p.r<hAc[i-1].r||p.r<hAc[i-2].r) continue; if (i+1<hAc.length&&p.r<hAc[i+1].r) continue;
                if (i+2<hAc.length&&p.r<hAc[i+2].r) continue; CG=cg; break;
            }
            if (CG==10) {
                var THRESH=3, det=[], inCol=false, sX=0;
                for (var xi=0;xi<hProj.length;xi++) { if (hProj[xi]>THRESH) { if (!inCol) { inCol=true; sX=xi; } } else { if (inCol) { det.push(mx+Math.round((sX+(xi-1))*2/2)); inCol=false; } } }
                if (inCol) det.push(mx+Math.round((sX+(hProj.length-1))*2/2));
                if (det.length==0) det.push(mx+Math.round(bboxW/2));
                cols=det; nCols=cols.length; CG=nCols>1?Math.round((cols[1]-cols[0])):40;
            } else {
                nCols=Math.max(1,Math.round(bboxW/CG)); CG=Math.max(10,Math.round(bboxW/nCols)); cols=[];
                for (var x=mx+Math.round(CG/2); x<=MX+CG&&cols.length<30; x+=CG) cols.push(x);
            }
            for (var ci=0;ci<cols.length;ci++) {
                var bestX=cols[ci], bestVal=0, sr=Math.round(CG*0.25);
                for (var x=cols[ci]-sr; x<=cols[ci]+sr; x+=2) { var idx=Math.round((x-mx)/2); if (idx>=0&&idx<hProj.length&&hProj[idx]>bestVal) { bestVal=hProj[idx]; bestX=x; } }
                cols[ci]=bestX;
            }

            var vProj=[];
            for (var y=my; y<=MY; y+=2) {
                var cnt=0;
                for (var x=mx; x<=MX; x+=6) { var pp=ctx.getImageData(x,y,1,1).data; if (cls(pp[0],pp[1],pp[2])) cnt++; }
                vProj.push(cnt);
            }
            var vLen=vProj.length, vMean=0;
            for (var i=0;i<vLen;i++) vMean+=vProj[i]; vMean/=vLen;
            var vBestLag=0, vBestR=0;
            for (var lag=5; lag<Math.min(vLen/2,100); lag++) {
                var num=0, den1=0, den2=0;
                for (var i=0;i<vLen-lag;i++) { var d1=vProj[i]-vMean, d2=vProj[i+lag]-vMean; num+=d1*d2; den1+=d1*d1; den2+=d2*d2; }
                if (den1>0.001&&den2>0.001) { var r=num/Math.sqrt(den1*den2); if (r>vBestR) { vBestR=r; vBestLag=lag; } }
            }
            var RG=vBestLag>=5?vBestLag*2:CG;
            var nRows=Math.max(1,Math.round(bboxH/RG)); RG=Math.max(10,Math.min(150,Math.round(bboxH/nRows)));
            var rows=[];
            for (var y=my+Math.round(RG/2); y<=MY+RG&&rows.length<6; y+=RG) rows.push(y);
            for (var ri=0;ri<rows.length;ri++) {
                var bestY=rows[ri], bestVal=0, sr=Math.round(RG*0.25);
                for (var y=rows[ri]-sr; y<=rows[ri]+sr; y+=2) { var idx=Math.round((y-my)/2); if (idx>=0&&idx<vProj.length&&vProj[idx]>bestVal) { bestVal=vProj[idx]; bestY=y; } }
                rows[ri]=bestY;
            }

            var RADIUS=Math.max(6,Math.round(CG*0.16));
            var STEP=Math.max(2,Math.round(RADIUS/2));
            var MIN_VOTES=4;
            var grid={};
            for (var ri=0;ri<rows.length;ri++) for (var ci=0;ci<cols.length;ci++) {
                var votes={B:0,P:0,T:0};
                for (var d1=-RADIUS;d1<=RADIUS;d1+=STEP) for (var d2=-RADIUS;d2<=RADIUS;d2+=STEP) {
                    var px=cols[ci]+d1, py=rows[ri]+d2;
                    if (px<0||px>=w||py<0||py>=ch) continue;
                    var pData=ctx.getImageData(px,py,1,1).data, l2=cls(pData[0],pData[1],pData[2]);
                    if (l2) votes[l2]++;
                }
                var ml=null,mv=0,tt=0;
                for (var k in votes) {tt+=votes[k]; if(votes[k]>mv){mv=votes[k];ml=k;}}
                if (ml&&mv>=MIN_VOTES&&mv/tt>0.35) { grid[ci+','+ri]=ml; }
            }

            var MIN_COL=Math.max(1,Math.floor(rows.length*0.25));
            var validCols=[];
            for (var ci=0;ci<cols.length;ci++) { var cnt=0; for (var ri=0;ri<rows.length;ri++) if(grid[ci+','+ri]) cnt++; if (cnt>=MIN_COL) validCols.push(ci); }

            var seq=[], st={B:0,P:0,T:0};
            for (var vi=0;vi<validCols.length;vi++) { var ci2=validCols[vi]; for (var ri=0;ri<rows.length;ri++) { var l=grid[ci2+','+ri]; if (l) { seq.push(l); st[l]++; } } }

            canvasRoad = { sequence: seq, stats: st, cols: validCols.length, rows: rows.length };
        })(canvases[0]); }
    } catch(e) {
        canvasRoad = null;
    }

    return JSON.stringify({
        ts: now,
        roundId: roundId,
        status: status,
        countdownText: ctext,
        timeDisplay: timeRaw || '',
        tableName: tableName,
        player_score_text: playerScoreRaw,
        banker_score_text: bankerScoreRaw,
        playerCardValues: playerCardValues,
        bankerCardValues: bankerCardValues,
        betRaw: betRaw,
        bootItems: bootItems,
        streaks: streaks,
        urlTableId: myTableId,
        urlGameType: myGameType,
        canvasRoad: canvasRoad,
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
