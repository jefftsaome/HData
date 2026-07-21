"""HData 对外公共 API — 游戏平台数据采集客户端。

这是给外部项目调用的唯一入口门面。封装完整链路：

    login()              登录，返回会话凭证
    get_tables()         拉取当前桌台列表（含每张桌的状态/游戏类型/路纸摘要）
    client.enter_table() 进入指定桌台，返回 TableSession 持续读取桌内数据

用法示例:
    import asyncio
    from hdata.client import GameClient

    async def main():
        client = GameClient(geepass_token="...", jfbym_token="...")
        # 1. 登录
        session = await client.login("account", "password")
        # 2. 拉桌台列表
        tables = await client.get_tables()
        baccarat = [t for t in tables if t["game_type_id"] == 2001]
        # 3. 进桌并读数据
        async with await client.enter_table(baccarat[0]["table_id"]) as ts:
            print(ts.snapshot)            # 进桌全量快照
            async for event in ts.events():   # 持续牌局事件
                print(event)

    asyncio.run(main())

设计约束:
  - 本模块只暴露稳定的 dict 结构，不暴露内部协议/加密细节；
  - 凭证、WS、编解码、踢出重进全部在内部处理；
  - 打包为 .pyd/.so 后，外部仅依赖本模块的公开函数签名。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from loguru import logger

from hdata.auth.session import (
    LoginError,
    build_ws_config,
    get_login as _session_login,
)
from hdata.protocol.codec import (
    FS_LOGIN,
    OT_GAME,
    OT_HALL,
    DEVICE_TYPE_PC,
    build_login_msg,
    build_message,
    decode_frame,
    encode_frame,
    extract_param,
)
from hdata.protocol.roadpaper import decode_road_paper, decode_bead_plate
from hdata.protocol.schemacodec import schema_decode

# ── 协议常量（内部使用，不导出） ──
_QS_TABLE_LIST_ALL = 10089
_QS_TABLE_LIST_LIMIT = 10053   # 分页桌台元数据（二进制 schema 帧）
_QS_HEARTBEAT = 3              # 协议心跳（pid=3，与官方前端一致）
_HEARTBEAT_INTERVAL = 10       # 秒
_QS_NEW_INTER_GAME = 401
_QS_INTER_GAME = 101
_QS_OUT_GAME = 102
_QS_NOTICE = 123      # 系统通知推送（含连续3局未下注预警 noticeId=21002）
_FORCE_101_GAME_TYPES = {2003, 2004, 2014, 2020}

_HT_SEAT = 1
_PT_BASE = 2

# TableMonitor 分片建连控制（实测同 IP 密集建连会被 WAF 403/短封；
# 3s 间隔实测可稳定建 11 条并发连接）
_SHARD_CONNECT_INTERVAL_S = 3.0   # 分片建连间隔（秒）
_SHARD_CONNECT_RETRIES = 3        # 单分片失败重试次数
_SHARD_RETRY_BACKOFF_S = 5.0      # 退避基数（第 n 次失败睡 n×base 秒）


# ── 公开数据结构 ──────────────────────────────────────


@dataclass
class TableInfo:
    """一张桌台的摘要信息（来自大厅快照）。"""

    table_id: int
    game_type_id: int
    game_type_name: str
    table_name: str
    status: int                 # gameStatus：2  betting, 3  dealing, 4 开牌/结算
    online: int                 # 在线人数
    boot_no: str                # 靴号
    road_flat: str              # 珠盘 B/P/T 序列（如 "BBPTPBPB"）
    road_count: int             # 本靴已开局的局数
    good_roads: list[str]       # 服务端标记的生效好路名（如 ["长庄","逢闲连"]）

    def to_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "game_type_id": self.game_type_id,
            "game_type_name": self.game_type_name,
            "table_name": self.table_name,
            "status": self.status,
            "online": self.online,
            "boot_no": self.boot_no,
            "road_flat": self.road_flat,
            "road_count": self.road_count,
            "good_roads": list(self.good_roads),
        }


# gameTypeId → 官方名称（逆向自大厅前端 JS：枚举 It + _gameNameMap，
# 与网页大厅实际显示的 8 个分类逐一对上）
_GAME_TYPE_NAMES = {
    2001: "经典百家乐", 2002: "极速百家乐", 2003: "竞咪百家乐",
    2004: "包桌百家乐", 2005: "共咪百家乐", 2006: "龙虎",
    2007: "轮盘", 2008: "骰宝", 2009: "牛牛",
    2010: "炸金花", 2011: "三公", 2012: "21点",
    2013: "多台", 2014: "高额百家乐", 2015: "斗牛",
    2016: "保险百家乐", 2018: "百家乐大赛", 2020: "番摊",
    2027: "劲舞百家乐", 2030: "主播百家乐", 2034: "闪电百家乐",
    2038: "电投百家乐",
}

# 好路类型 id → 官方名称（逆向自前端 GoodRoadType 字典 + 中文语言包
# @grd_20001~20011；用于 set_road_filter 的参数与 goodRoadPoints 解读）
GOOD_ROAD_NAMES = {
    1: "长闲", 2: "长庄", 3: "大路单跳", 4: "长路转单跳",
    5: "一庄两闲", 6: "一闲两庄", 7: "逢庄跳", 8: "逢闲跳",
    9: "逢庄连", 10: "逢闲连", 11: "排排连",
}


# ── game_token 刷新节流 ──────────────────────────────
#
# 平台对同 IP 的 JWT 刷新接口有速率限制（2026-07-20 实测：缓存
# 全部命中时多账号密集建连，前 5 次刷新成功、第 6 次被拒，精确
# 阈值未实测）。刷新被拒若直接兜底完整重登（打码），代价高且会
# 进一步放大请求密度。策略：
#   1. 进程级最小间隔：所有刷新串行排队，间隔 >= MIN_INTERVAL；
#   2. 新鲜跳过：session["_refresh_ts"] 在每次成功刷新后记录，
#      SKIP_S 内不再重复刷新（登录流程刚刷过的 token 直接复用）；
#   3. 失败退避重试一次再兜底（见 _refresh_cb）。

_REFRESH_MIN_INTERVAL_S = 2.0   # 进程内任意两次刷新的最小间隔
_REFRESH_SKIP_S = 60.0          # 刷新成功后多少秒内视为新鲜可复用
_REFRESH_RETRY_DELAY_S = 5.0    # 刷新失败后的退避重试延迟


class _RefreshThrottle:
    """进程级刷新节流器：按事件循环分配锁，全局共享上次刷新时刻。"""

    _locks: dict[int, asyncio.Lock] = {}
    _last_ts: float = 0.0

    @classmethod
    async def acquire(cls):
        loop = asyncio.get_running_loop()
        lock = cls._locks.get(id(loop))
        if lock is None:
            lock = cls._locks[id(loop)] = asyncio.Lock()
        async with lock:
            wait = cls._last_ts + _REFRESH_MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            cls._last_ts = time.monotonic()


class GameClient:
    """游戏平台数据采集客户端（对外门面）。

    平台无关设计：内部按平台适配层实现，leyu 为当前已接入平台；
    后续接入其他子平台时本类接口保持不变。

    Args:
        entry_url: 平台入口种子站（由调用者提供，如平台官网域名）
        geepass_token: geepass 打码平台 token（纯 HTTP 登录用）
        jfbym_token: jfbym 打码平台 token（纯 HTTP 登录用）
        proxy: 默认代理 URL（可选）。token 绑定登录 IP——传入后
               login/refresh/WS 全部走该出口；login() 的 proxy
               参数可逐次覆盖
    """

    def __init__(self, entry_url: str,
                 geepass_token: str = "", jfbym_token: str = "",
                 proxy: str | None = None):
        self._entry_url = entry_url
        self._geepass_token = geepass_token
        self._jfbym_token = jfbym_token
        self._proxy = proxy
        self._session: dict | None = None

    # ── 1. 登录 ───────────────────────────────────────

    async def login(self, account: str, password: str = "",
                    force_refresh: bool = False,
                    proxy: str | None = None) -> dict:
        """登录，返回会话凭证 dict。

        Args:
            account: 平台账号
            password: 密码（有有效缓存时可为空）
            force_refresh: 跳过缓存强制重新登录
            proxy: 本次登录使用的代理 URL；None 时用构造参数里的
                   默认 proxy。登录/刷新/WS 全程同一出口（token 绑 IP）

        Returns:
            {
              "account": str,
              "player_id": int,          # 玩家 ID
              "domain": str,             # 当前主站域名
              "game_token": str,         # 游戏 JWT（凭证，敏感）
              "game_exp": int,           # JWT 过期时间戳
              "backend": str,            # 游戏后端地址
            }

        Raises:
            LoginError: 所有登录方式均失败
        """
        session = await _session_login(
            account,
            password,
            entry_url=self._entry_url,
            force_refresh=force_refresh,
            geepass_token=self._geepass_token,
            jfbym_token=self._jfbym_token,
            proxy=proxy if proxy is not None else self._proxy,
        )
        # 确保 game_token 是服务端当前认可的最新一张：
        # 缓存里的旧 token 可能已被服务端作废（jti 踢出），先刷新一次。
        # 刷新失败不致命：_session_login 返回的会话本身可能仍可用。
        try:
            session = await self._refresh_game_token(account, session)
        except Exception:
            pass
        session["_password"] = password      # 兜底重登用，仅驻内存不落盘
        self._account = account
        self._password = password
        self._session = session
        return {
            "account": account,
            "player_id": session.get("game_player_id", 0),
            "domain": session.get("domain", ""),
            "game_token": session.get("game_token", ""),
            "game_exp": session.get("game_exp", 0),
            "backend": session.get("game_backend", ""),
        }

    async def _refresh_game_token(self, account: str, session: dict) -> dict:
        """用站点会话刷新 game_token 并写回缓存。失败抛异常。

        进程级节流（平台对同 IP 刷新有速率限制）；成功后记录
        session["_refresh_ts"] 供建连前新鲜度跳过判断。
        """
        from hdata.auth.params import decode_jwt
        from hdata.auth.session import refresh_game_session, save_session
        await _RefreshThrottle.acquire()
        params = await refresh_game_session(account, session)
        new_token = params.get("token")
        if new_token:
            session["game_token"] = new_token
        if params.get("backendDomainUrl"):
            session["game_backend"] = params["backendDomainUrl"]
        if params.get("backendDomainUrlList"):
            session["backend_domain_url_list"] = params["backendDomainUrlList"]
        jwt_info = decode_jwt(new_token) if new_token else None
        if jwt_info:
            session["game_exp"] = jwt_info.get("exp", 0)
            sub = jwt_info.get("sub", {})
            if isinstance(sub, dict):
                session["game_player_id"] = sub.get("playerId", 0)
        session["_refresh_ts"] = time.time()
        try:
            save_session(account, session)
        except Exception:
            pass
        return session

    def _require_session(self) -> dict:
        if not self._session:
            raise LoginError("尚未登录，请先调用 login()")
        return self._session

    # ── 2. 桌台列表 ───────────────────────────────────

    async def get_tables(self, game_type_id: int | None = None) -> list[dict]:
        """拉取当前大厅桌台列表。

        Args:
            game_type_id: 可选，按游戏类型过滤（如 2001=经典百家乐）。
                          None 返回全部。

        Returns:
            list[TableInfo.to_dict()]，每张桌含:
              table_id / game_type_id / game_type_name / table_name /
              status / online / boot_no / road_flat / road_count
        """
        session = self._require_session()
        async with _WSConnection(session, on_before_connect=self._refresh_cb) as conn:
            raw = await conn.fetch_table_map()
            try:
                ids = [int(k) for k in raw
                       if str(k).lstrip("-").isdigit()]
                meta = await conn.fetch_table_meta(ids or None)
            except Exception:
                meta = {}
        tables = [_table_info_from_snapshot(tid, t, meta)
                  for tid, t in raw.items()]
        tables = [t for t in tables if t]
        if game_type_id is not None:
            tables = [t for t in tables if t.game_type_id == game_type_id]
        return [t.to_dict() for t in tables]

    # ── 3. 进桌 ───────────────────────────────────────

    async def enter_table(self, table_id: int,
                          game_type_id: int = 2001) -> "TableSession":
        """进入指定桌台，返回 TableSession（异步上下文管理器）。

        Args:
            table_id: 目标桌台 ID（来自 get_tables）
            game_type_id: 游戏类型（默认 2001 经典百家乐）

        Returns:
            TableSession — 用 `async with` 进入后:
                .snapshot      进桌全量快照 dict
                .events()      异步迭代器，持续产出牌局事件 dict

        Example:
            async with await client.enter_table(2659) as ts:
                print(ts.snapshot["tableName"])
                async for ev in ts.events():
                    ...
        """
        session = self._require_session()
        conn = _WSConnection(session, on_before_connect=self._refresh_cb)
        ts = TableSession(conn, table_id, game_type_id)
        return ts

    async def _refresh_cb(self, session: dict) -> dict:
        """每次新建 WS 连接前刷新 game_token（服务端按 jti 单连接）。

        三层保护：
          1. 新鲜跳过：_REFRESH_SKIP_S 内刚刷过的 token 直接复用，
             避免登录流程+建连前重复刷新触发平台速率限制；
          2. 失败退避重试一次：限流类拒绝多数几秒内自愈；
          3. 兜底完整重登（可能走打码）：必须用**本会话所属账号**
             （各分片共享本回调，self._account 只是主账号），并继承
             会话的代理出口（token 绑 IP）。
        """
        account = session.get("account", "")
        if time.time() - session.get("_refresh_ts", 0) < _REFRESH_SKIP_S:
            return session
        for attempt in (1, 2):
            try:
                return await self._refresh_game_token(account, session)
            except Exception as e:
                if attempt == 1:
                    logger.warning(f"[{account}] 建连前刷新失败"
                                   f"（{type(e).__name__}），"
                                   f"{_REFRESH_RETRY_DELAY_S:.0f}s 后重试")
                    await asyncio.sleep(_REFRESH_RETRY_DELAY_S)
                else:
                    logger.warning(f"[{account}] 建连前刷新重试仍失败"
                                   f"（{type(e).__name__}）")
        # 站点会话整体失效 → 用会话所属账号完整重新登录
        password = session.get("_password") or (
            self._password if account == self._account else "")
        if not password:
            raise LoginError(f"[{account}] game_token 刷新失败且无密码可兜底重登")
        logger.warning(f"[{account}] 站点会话失效，完整重登兜底（可能打码）")
        fresh = await _session_login(
            account, password,
            entry_url=self._entry_url, force_refresh=True,
            geepass_token=self._geepass_token,
            jfbym_token=self._jfbym_token,
            proxy=session.get("proxy") or None)
        fresh["_password"] = password
        if account == self._account:
            self._session = fresh
        return fresh

    # ── 4. 玩家设置（gateway HTTP） ───────────────────

    async def get_settings(self) -> list[dict]:
        """读取当前玩家的全部设置（含路纸筛选偏好）。

        Returns:
            设置项列表，每项:
              {playerId, settingType, settingObject, deviceType, value, defaultValue}
            其中 settingType="4" 为大厅筛选：
              settingObject="22" → 游戏类型过滤（value=gameTypeId 列表）
              settingObject="23" → 路纸类型过滤（value=好路 id 列表）
        """
        session = self._require_session()
        pid = session.get("game_player_id", 0)
        url = (f"https://gateway.{session['game_backend']}"
               f"/game-http/player/getPlayerSetting?playerId={pid}")
        r = await asyncio.to_thread(
            _gateway_request, "GET", url, None, session)
        data = r.get("data") or []
        return data if isinstance(data, list) else []

    async def set_setting(self, setting_object: str, value: str,
                          setting_type: str = "4") -> bool:
        """修改一项玩家设置（持久化到服务端）。

        Args:
            setting_object: 子项 id。"22"=游戏类型过滤 "23"=路纸类型过滤
            value: 选中的 id 列表，逗号分隔（如 "2,1,3"）
            setting_type: 设置大类，默认 "4"=大厅筛选

        Returns:
            True = 写入成功
        """
        session = self._require_session()
        pid = session.get("game_player_id", 0)
        ts = int(time.time() * 1000)
        payload = {"playerId": pid, "settingType": setting_type,
                   "settingObject": setting_object,
                   "deviceType": "6", "value": value}
        url = (f"https://gateway.{session['game_backend']}"
               f"/game-http/player/updatePlayerSetting?t={ts}")
        r = await asyncio.to_thread(
            _gateway_request, "POST", url, payload, session, ts)
        return bool(r.get("code") == 200 and r.get("data"))

    async def set_road_filter(self, road_ids: list[int] | str) -> bool:
        """修改路纸筛选偏好（settingObject=23 的便捷封装）。

        Args:
            road_ids: 好路类型 id 列表（1~11，名称见 `GOOD_ROAD_NAMES`：
                      1长闲 2长庄 3大路单跳 4长路转单跳 5一庄两闲 6一闲两庄
                      7逢庄跳 8逢闲跳 9逢庄连 10逢闲连 11排排连），
                      或逗号分隔字符串

        Example:
            await client.set_road_filter([2, 1])   # 只看长庄+长闲
        """
        value = road_ids if isinstance(road_ids, str) else ",".join(
            str(i) for i in road_ids)
        return await self.set_setting("23", value)

    # ── 5. 多桌监控（单连接） ─────────────────────────

    async def enter_tables(self, tables: list[dict],
                           kick_policy: str = "stay") -> "MultiTableSession":
        """同时进入多张桌台监控（共享一条 WS 连接）。

        实测确认：同一账号在**一条连接**上可同时进多桌（服务端按连接
        限制而非按桌限制），事件流按 table_id 区分。**不需要多账号**。

        Args:
            tables: 桌台列表，每项至少含 {"table_id": int,
                    "game_type_id": int}（即 get_tables() 的返回项）
            kick_policy: 被系统踢出（连续5局未下注）时的策略——
                "stay"（默认）：被踢后自动重进该桌，监控不中断；
                "follow_system"：遵循系统踢出，该桌停止监控。

        Returns:
            MultiTableSession — `async with` 进入后:
                .snapshots    {table_id: 进桌快照 dict}
                .events()     异步迭代器，事件 dict 含 table_id 字段

        Example:
            picked = [t for t in await client.get_tables() if ...]
            async with await client.enter_tables(picked) as mts:
                async for ev in mts.events():
                    if ev["type"] == "road":
                        side, n = road_streak(mts.road_flat(ev["table_id"]))
        """
        session = self._require_session()
        conn = _WSConnection(session, on_before_connect=self._refresh_cb)
        return MultiTableSession(conn, tables, kick_policy=kick_policy)

    # ── 6. 持续监控（单/多账号兼容） ──────────────────

    async def monitor_tables(self, tables: list[dict],
                             accounts: list[dict] | None = None,
                             kick_policy: str = "stay"
                             ) -> "TableMonitor":
        """创建持续桌台监控（人为主动控制退出，无自动超时）。

        账号策略（自动兼容两种模式）:
          - **单账号多桌**（默认）：不传 accounts，全部桌台压在当前登录
            账号的一条连接上（已实测可行）；
          - **多账号多桌**：传入 accounts，桌台轮询分配到各账号，
            每账号一条连接（每账号仍可同时多桌）。若平台日后限制
            单账号多桌，只需补账号即可无缝切换。
            每个账号都会建立自己的分片连接——即使初始没有分到桌，
            以便后续 add_table() 动态均衡到全部账号。

        Args:
            tables: 桌台列表（get_tables() 返回项，至少含 table_id）
            accounts: 可选，额外账号 [{"account":..,"password":..}, ...]
                      当前登录账号自动算第一个，无需重复传。
                      每项可带 "proxy" 键指定该账号的代理出口
                      （token 绑 IP，账号全程固定走该出口）
            kick_policy: 被系统踢出（连续5局未下注）时的策略——
                "stay"（默认）：被踢后自动重进该桌，监控不中断；
                "follow_system"：遵循系统踢出，该桌停止监控。

        Returns:
            TableMonitor — `async with` 进入后持续运行:
                .snapshots            {table_id: 快照}
                .road_flat(tid)       指定桌当前珠盘路
                .events()             统一事件流（含 table_id）
                .add_table(t)         动态加桌
                .leave_table(tid)     主动退出某桌
                .aclose()             停止全部（退出 async with 也会调）

        Example:
            async with await client.monitor_tables(picked) as mon:
                async for ev in mon.events():
                    side, n = road_streak(mon.road_flat(ev["table_id"]))
                    if n < 5:
                        await mon.leave_table(ev["table_id"])  # 断龙主动退
        """
        first = self._require_session()

        # 1. 收集所有账号会话（第一个复用当前登录）
        sessions: list[dict] = [first]
        for c in (accounts or []):
            if c.get("account") == self._account:
                continue
            s = await _session_login(
                c["account"], c.get("password", ""),
                entry_url=self._entry_url,
                geepass_token=self._geepass_token,
                jfbym_token=self._jfbym_token,
                proxy=c.get("proxy"))          # 每账号独立出口（token 绑 IP）
            s["account"] = c["account"]
            s["_password"] = c.get("password", "")  # 兜底重登用，不落盘
            sessions.append(s)

        # 2. 桌台轮询分配到各账号
        n = len(sessions)
        groups: list[list[dict]] = [[] for _ in range(n)]
        for i, t in enumerate(tables):
            groups[i % n].append(t)

        # 3. 每账号一条连接 + 一个 MultiTableSession。
        #    空组同样建分片：tables 可为空列表，后续通过
        #    TableMonitor.add_table() 把桌台均衡到各账号分片。
        shards: list[MultiTableSession] = []
        for sess, ts in zip(sessions, groups):
            conn = _WSConnection(sess, on_before_connect=self._make_refresh_cb())
            shards.append(MultiTableSession(conn, ts, kick_policy=kick_policy))
        return TableMonitor(shards, self._make_refresh_cb)

    def _make_refresh_cb(self):
        """生成与账号无关的刷新回调（复用 _refresh_cb 的兜底逻辑）。"""
        async def _cb(session: dict) -> dict:
            return await self._refresh_cb(session)
        return _cb


# ── 连胜计算 ──────────────────────────────────────────


def round_result_token(round_result) -> str:
    """把 107 牌局事件的 roundResult 解析为路纸 token。

    实测格式：`"{庄点};{闲点}"`（**庄在前**），
    如 "9;5"=庄9闲5、"6;4"=庄6闲4。判定：
      - 庄点 > 闲点 → "B"；庄点 == 6 且庄赢 → "B6"（幸运6庄）
      - 庄点 < 闲点 → "P"
      - 相等 → "T"
      - 无法解析 → ""

    Examples:
        >>> round_result_token("9;5")
        'B'
        >>> round_result_token("6;4")
        'B6'
        >>> round_result_token("4;6")
        'P'
        >>> round_result_token("5;5")
        'T'
    """
    if not isinstance(round_result, str) or ";" not in round_result:
        return ""
    try:
        b_s, p_s = round_result.split(";", 1)
        banker, player = int(b_s.strip()), int(p_s.strip())
    except (ValueError, AttributeError):
        return ""
    if banker > player:
        return "B6" if banker == 6 else "B"
    if banker < player:
        return "P"
    return "T"


def road_streak(road: str) -> tuple[str, int]:
    """计算路纸末尾连胜（对齐口径）。

    规则:
      - `T`(和) 归属于之前最近一局非和局的胜方，**不打断连胜也不计数**；
      - `B6`(幸运6庄) 视为 `B`；
      - 连胜 = 末尾同一胜方的连续非和局数（中间允许夹 T）。

    Returns:
        (side, count): side 为 "B"/"P"（无连胜为空串），count 为连胜局数。

    Examples:
        >>> road_streak("PTBBB")
        ('B', 3)
        >>> road_streak("BTTBB")   # 中间2局T视为庄和不占局数
        ('B', 3)
        >>> road_streak("BTBBT")   # 末尾T归庄
        ('B', 3)
    """
    seq = road.replace("B6", "B").rstrip("T")
    if not seq:
        return ("", 0)
    side = seq[-1]
    count = 0
    for ch in reversed(seq):
        if ch == side:
            count += 1
        elif ch == "T":
            continue
        else:
            break
    return (side, count)


# ── WS 连接（内部） ──────────────────────────────────


class _WSConnection:
    """封装一条 WS 连接：握手 + 登录 + 帧收发。"""

    def __init__(self, session: dict, on_before_connect=None):
        self._session = session
        self._on_before_connect = on_before_connect
        self._cfg: dict = {}
        self._ws: Any = None
        self._device_id = ""
        self._player_id = session.get("game_player_id", 0)

    def _rebuild_cfg(self):
        self._cfg = build_ws_config({
            "game_token": self._session["game_token"],
            "game_player_id": self._session.get("game_player_id", 0),
            "game_backend": self._session.get("game_backend", ""),
            "backend_domain_url_list": self._session.get("backend_domain_url_list", ""),
        })
        self._device_id = self._cfg["device_id"]

    @property
    def device_id(self) -> str:
        return self._device_id

    async def __aenter__(self) -> "_WSConnection":
        import websockets
        if self._on_before_connect:
            try:
                self._session = await self._on_before_connect(self._session)
            except Exception as e:
                logger.warning(f"[{self._session.get('account', '?')}] "
                               f"建连前回调失败（{type(e).__name__}: {e}），"
                               "沿用现有 token 尝试连接")
        self._rebuild_cfg()
        self._player_id = self._session.get("game_player_id", 0)
        self._ws = await websockets.connect(
            self._cfg["ws_url"], open_timeout=12, close_timeout=3,
            max_size=50 * 1024 * 1024,
            proxy=self._session.get("proxy") or None)
        await self._login()
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, *exc):
        task = getattr(self, "_hb_task", None)
        if task:
            task.cancel()
            self._hb_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _heartbeat_loop(self):
        """协议级心跳保活（pid=3，与官方前端一致）。

        无心跳时服务端约 40~60s 主动断连（实测）。
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                await self.send(build_message(
                    _QS_HEARTBEAT,
                    {"clientTime": int(time.time() * 1000),
                     "deviceType": DEVICE_TYPE_PC,
                     "deviceId": self._device_id},
                    player_id=self._player_id, game_type_id=2013,
                    service_type_id=OT_HALL))
        except asyncio.CancelledError:
            return
        except Exception:
            return  # 发送失败说明连接已坏，交由接收侧感知

    async def _login(self):
        token = self._session["game_token"]
        await self.send(build_login_msg(token, self._player_id, self._device_id))
        end = time.time() + 12
        while time.time() < end:
            frame = await self.recv()
            if not frame:
                continue
            pid = frame.get("protocolId")
            if pid == FS_LOGIN:
                info = extract_param(frame) or {}
                if info.get("status") == 1:
                    return
                raise LoginError(f"WS 登录被拒: {info.get('msg')}")
            if pid == 10026:
                raise LoginError("WS 登录被踢: token 失效")
        raise LoginError("WS 登录超时")

    async def send(self, msg: dict):
        await self._ws.send(encode_frame(msg))

    async def recv(self) -> dict | None:
        raw = await self._ws.recv()
        if isinstance(raw, str):
            return None
        return decode_frame(raw)

    async def recv_until(self, predicate, timeout: float) -> dict | None:
        """持续收帧直到 predicate(frame) 为真或超时。"""
        end = time.time() + timeout
        while time.time() < end:
            try:
                frame = await asyncio.wait_for(
                    self.recv(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                return None
            if frame and predicate(frame):
                return frame
        return None

    async def fetch_table_map(self) -> dict:
        """拉取大厅桌台快照 gameTableMap（聚合多帧 10052 增量）。"""
        await self.send(build_message(
            _QS_TABLE_LIST_ALL, {"labelTypeId": 1},
            player_id=self._player_id, game_type_id=2013,
            service_type_id=OT_HALL))
        gtm: dict = {}
        end = time.time() + 20
        # 10052 分批增量推送；持续收直到超时或长时间无新桌
        last_new = time.time()
        while time.time() < end:
            try:
                frame = await asyncio.wait_for(
                    self.recv(), timeout=max(0.1, min(3.0, end - time.time())))
            except asyncio.TimeoutError:
                if time.time() - last_new > 3:
                    break
                continue
            if not frame or frame.get("protocolId") != 10052:
                continue
            info = extract_param(frame) or {}
            data = info.get("param") or info.get("data")
            import json as _json
            if isinstance(data, str):
                data = _json.loads(data)
            new = (data or {}).get("gameTableMap") or {}
            if new:
                gtm.update(new)
                last_new = time.time()
        return gtm

    async def fetch_table_meta(self, table_ids: list[int] | None = None,
                               page_size: int = 60) -> dict:
        """拉取大厅桌台元数据（10089 → 10053，二进制 schema 帧）。

        与官方前端同流程：先发 10089 拿桌台 id 全集，再分页发 10053
        取每张桌的 tableName/gameTypeName/gameCasinoName/dealerName 等。

        Args:
            table_ids: 只取这些桌的元数据；None = 先走 10089 拿全集
            page_size: 10053 每页桌数

        Returns:
            {table_id(int): 10053 GameTable dict}
        """
        import json as _json

        def _payload(frame):
            info = extract_param(frame) or {}
            data = info.get("data")
            if isinstance(data, str):
                if frame.get("codecFlag"):
                    try:
                        return schema_decode(
                            f"{frame['protocolId']}_"
                            f"{frame.get('serviceTypeId', 7)}", data)
                    except Exception:
                        return None
                try:
                    return _json.loads(data)
                except Exception:
                    return None
            return data if isinstance(data, dict) else None

        ids: list[int] = list(table_ids or [])
        if not ids:
            # 1) 10089：桌台 id 全集
            await self.send(build_message(
                _QS_TABLE_LIST_ALL, {"labelTypeId": 1},
                player_id=self._player_id, game_type_id=2013,
                service_type_id=OT_HALL))
            end = time.time() + 12
            last_new = time.time()
            while time.time() < end:
                try:
                    frame = await asyncio.wait_for(
                        self.recv(), timeout=max(0.1, end - time.time()))
                except asyncio.TimeoutError:
                    break
                if not frame or frame.get("protocolId") != _QS_TABLE_LIST_ALL:
                    continue
                data = _payload(frame) or {}
                new = False
                for t in data.get("hallGameTable") or []:
                    tid = t.get("tableId")
                    if tid and tid not in ids:
                        ids.append(tid)
                        new = True
                if new:
                    last_new = time.time()
                # 10089 可能分多帧下发：无新 id 满 2s 才收尾
                if ids and time.time() - last_new > 2:
                    break
        if not ids:
            return {}

        # 2) 10053：分页取元数据（无新数据 3s 即收尾）
        meta: dict = {}
        want = set(ids)
        for i in range(0, len(ids), page_size):
            await self.send(build_message(
                _QS_TABLE_LIST_LIMIT,
                {"groupId": 7, "tableIds": ids[i:i + page_size],
                 "allFlag": 0},
                player_id=self._player_id, game_type_id=2013,
                service_type_id=OT_HALL))
            end = time.time() + 15
            last_new = time.time()
            while time.time() < end and not want <= set(meta):
                try:
                    frame = await asyncio.wait_for(
                        self.recv(), timeout=max(0.1, min(3.0, end - time.time())))
                except asyncio.TimeoutError:
                    if time.time() - last_new > 3:
                        break
                    continue
                if not frame or frame.get("protocolId") != _QS_TABLE_LIST_LIMIT:
                    continue
                data = _payload(frame) or {}
                new = data.get("gameTableMap") or {}
                if new:
                    last_new = time.time()
                for k, v in new.items():
                    try:
                        meta[int(k)] = v
                    except (TypeError, ValueError):
                        continue
        return meta


# ── TableSession ─────────────────────────────────────


class TableSession:
    """一张桌的会话（进桌后的数据通道）。

    用 `async with` 进入；退出时自动离桌。
    被踢出（连续5局未投注）时内部自动重进，events() 不中断。
    """

    def __init__(self, conn: _WSConnection, table_id: int, game_type_id: int):
        self._conn = conn
        self.table_id = table_id
        self.game_type_id = game_type_id
        self.snapshot: dict = {}
        self._entered = False
        self._leaving = False   # 主动离桌中（防把离桌确认误判为被踢）
        self._road_accum: list = []   # 珠盘累积（116全长重置 / 107逐局追加）
        self._last_round_id = 0       # 已入路的最大 roundId（107去重）

    async def __aenter__(self) -> "TableSession":
        await self._conn.__aenter__()
        await self._enter()
        return self

    async def __aexit__(self, *exc):
        try:
            await self._leave()
        finally:
            await self._conn.__aexit__(*exc)

    def _enter_proto(self) -> int:
        return (_QS_INTER_GAME if self.game_type_id in _FORCE_101_GAME_TYPES
                else _QS_NEW_INTER_GAME)

    async def _enter(self):
        proto = self._enter_proto()
        data = {
            "tableId": self.table_id,
            "gameTypeId": self.game_type_id,
            "identity": _HT_SEAT,
            "joinTableMode": _PT_BASE,
            "gameCasinoId": 0,
            "deviceType": DEVICE_TYPE_PC,
            "deviceId": self._conn.device_id,
        }
        await self._conn.send(build_message(
            proto, data, player_id=self._conn._player_id,
            game_type_id=self.game_type_id, table_id=self.table_id,
            service_type_id=OT_GAME))
        # 等 proto 响应（全量快照）。期间的路纸帧(116/160/161)会被
        # recv_until 式等待丢弃——116 是全长路纸的唯一来源，必须暂存消化。
        import json as _json
        stashed: list[dict] = []
        frame = None
        end = time.time() + 15
        while time.time() < end:
            try:
                f = await asyncio.wait_for(
                    self._conn.recv(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            if not f:
                continue
            if f.get("protocolId") == proto:
                frame = f
                break
            if f.get("protocolId") in (116, 160, 161):
                stashed.append(f)
        if frame:
            info = extract_param(frame) or {}
            payload = info.get("param") or info.get("data")
            if isinstance(payload, str):
                payload = _json.loads(payload)
            self.snapshot = (payload or {}).get("gameTableInfo") or {}
        self._entered = True
        for f in stashed:
            info = extract_param(f) or {}
            payload = info.get("param") or info.get("data")
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    continue
            self._apply_road(f.get("protocolId"), payload)

    def _apply_road(self, pid: int, payload):
        """消化路纸事件：仅 116=全长路纸（置换快照并重置累积）。

        160 不带 roadPaper；161 是增量短串（0~5 个 token 不等，语义
        不可靠），**不参与累积**——逐局结果由 107.roundResult 权威供给
        （见 events() 的 107 分支）。"""
        if pid != 116 or not isinstance(payload, dict):
            return
        rp = payload.get("roadPaper")
        if not rp:
            return
        self.snapshot["roadPaper"] = rp
        b64 = rp.get("beatPlateRoad") or ""
        if b64:
            try:
                flat = decode_bead_plate(b64)["flat"]
                if flat:
                    self._road_accum = flat
            except Exception:
                pass

    def _append_round_result(self, payload):
        """107 牌局事件：从 roundResult（"庄点;闲点"）取结果追加进路纸累积。"""
        if not isinstance(payload, dict):
            return
        rid = payload.get("roundId") or 0
        if rid and rid == self._last_round_id:
            return                      # 同局重复推送，去重
        token = round_result_token(payload.get("roundResult"))
        if not token:
            return
        self._last_round_id = rid or self._last_round_id
        self._road_accum.append(token)

    async def _leave(self):
        if not self._entered:
            return
        self._leaving = True
        try:
            await self._conn.send(build_message(
                _QS_OUT_GAME, {}, player_id=self._conn._player_id,
                game_type_id=self.game_type_id, table_id=self.table_id,
                service_type_id=OT_GAME))
        except Exception:
            pass
        self._entered = False
        self._leaving = False

    # ── 公开读取接口 ──

    def road_flat(self) -> str:
        """当前珠盘 B/P/T 序列（116 全长 + 161 增量合并后的最新牌路）。"""
        if self._road_accum:
            return "".join(self._road_accum)
        rp = self.snapshot.get("roadPaper") or {}
        b64 = rp.get("beatPlateRoad") or ""
        if not b64:
            return ""
        try:
            return "".join(decode_bead_plate(b64)["flat"])
        except Exception:
            return ""

    async def events(self) -> AsyncIterator[dict]:
        """持续产出桌内牌局事件（异步迭代器）。

        每个事件:
          {
            "type": str,         # 事件类型: round / card / road / status / bet / kick
            "protocol_id": int,  # 原始协议号
            "table_id": int,
            "data": dict,        # 解码后的业务数据
          }

        被踢出桌台时自动重进，迭代不中断；
        会话级踢出（token 失效）时抛 LoginError 终止迭代。
        """
        while True:
            try:
                frame = await self._conn.recv()
            except Exception:
                return
            if not frame:
                continue
            pid = frame.get("protocolId")
            info = extract_param(frame) or {}
            payload = info.get("param") or info.get("data")
            import json as _json
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    pass

            if pid == 10026:
                raise LoginError("会话被踢（token 失效），请重新 login()")
            if pid == _QS_OUT_GAME and isinstance(payload, dict):
                # 服务器离桌推送：本桌且非主动离桌 = 被系统踢出 → 自动重进
                try:
                    leave_tid = int(payload.get("tableId", 0))
                except (TypeError, ValueError):
                    leave_tid = 0
                if leave_tid == self.table_id and not self._leaving \
                        and payload.get("leaveTableType") != 1:
                    await self._enter()
                    yield {"type": "kick", "protocol_id": pid,
                           "table_id": self.table_id,
                           "data": {"action": "auto_reenter",
                                    "dropped": False, "raw": payload}}
                    continue

            # 路纸事件：116 全长置换（160/161 不参与累积）
            if pid in (116, 160, 161):
                self._apply_road(pid, payload)

            # 牌局事件：107 携带 roundResult（"庄点;闲点"），逐局追加路纸
            if pid == 107:
                self._append_round_result(payload)

            yield {
                "type": _classify_event(pid),
                "protocol_id": pid,
                "table_id": self.table_id,
                "data": payload if isinstance(payload, dict) else {"raw": payload},
            }


# ── MultiTableSession ────────────────────────────────


class MultiTableSession:
    """多桌监控会话（共享一条 WS 连接）。

    用 `async with` 进入；退出时自动离全部桌。
    某张桌被踢（5局未投注）时按 kick_policy 处理：
      - "stay"（默认）：该桌自动重进，其他桌不受影响；
      - "follow_system"：遵循系统踢出，该桌停止监控。
    """

    def __init__(self, conn: "_WSConnection", tables: list[dict],
                 kick_policy: str = "stay"):
        self._conn = conn
        self._tables: list[dict] = [
            {"table_id": int(t["table_id"]),
             "game_type_id": int(t.get("game_type_id", 2001))}
            for t in tables
        ]
        if kick_policy not in ("stay", "follow_system"):
            raise ValueError("kick_policy 只能是 'stay' 或 'follow_system'")
        self.kick_policy = kick_policy
        self.snapshots: dict[int, dict] = {}
        self._entered: set[int] = set()
        self._leaving: set[int] = set()   # 主动离桌中的桌（防误判为被踢）
        self._road_accum: dict[int, list] = {}   # 每桌珠盘累积（116重置/107追加）
        self._last_round_id: dict[int, int] = {}   # 每桌已入路的最大 roundId

    async def __aenter__(self) -> "MultiTableSession":
        await self._conn.__aenter__()
        for t in self._tables:
            await self._enter_one(t)
        return self

    async def __aexit__(self, *exc):
        for t in self._tables:
            await self._leave_one(t)
        await self._conn.__aexit__(*exc)

    def _enter_proto(self, game_type_id: int) -> int:
        return (_QS_INTER_GAME if game_type_id in _FORCE_101_GAME_TYPES
                else _QS_NEW_INTER_GAME)

    async def _enter_one(self, t: dict):
        proto = self._enter_proto(t["game_type_id"])
        data = {
            "tableId": t["table_id"],
            "gameTypeId": t["game_type_id"],
            "identity": _HT_SEAT,
            "joinTableMode": _PT_BASE,
            "gameCasinoId": 0,
            "deviceType": DEVICE_TYPE_PC,
            "deviceId": self._conn.device_id,
        }
        await self._conn.send(build_message(
            proto, data, player_id=self._conn._player_id,
            game_type_id=t["game_type_id"], table_id=t["table_id"],
            service_type_id=OT_GAME))
        self._entered.add(t["table_id"])
        # 快照通过事件循环里的 401 响应异步填充（见 events/_fill_snapshot）

    async def _leave_one(self, t: dict):
        if t["table_id"] not in self._entered:
            return
        self._leaving.add(t["table_id"])
        try:
            await self._conn.send(build_message(
                _QS_OUT_GAME, {}, player_id=self._conn._player_id,
                game_type_id=t["game_type_id"], table_id=t["table_id"],
                service_type_id=OT_GAME))
        except Exception:
            pass
        self._entered.discard(t["table_id"])
        self._leaving.discard(t["table_id"])

    def _fill_snapshot(self, payload: dict) -> int:
        """从 401/101 响应提取快照，返回 table_id（无则 0）。"""
        gti = (payload or {}).get("gameTableInfo") or {}
        tid = gti.get("tableId", 0)
        if tid:
            self.snapshots[tid] = gti
        return tid

    def road_flat(self, table_id: int) -> str:
        """指定桌当前珠盘 B/P/T 序列（116 全长 + 161 增量合并）。"""
        accum = self._road_accum.get(table_id)
        if accum:
            return "".join(accum)
        rp = (self.snapshots.get(table_id) or {}).get("roadPaper") or {}
        b64 = rp.get("beatPlateRoad") or ""
        if not b64:
            return ""
        try:
            return "".join(decode_bead_plate(b64)["flat"])
        except Exception:
            return ""

    async def events(self) -> AsyncIterator[dict]:
        """持续产出所有监控桌的事件（异步迭代器）。

        事件结构与 TableSession.events() 相同，table_id 标识来源桌。

        实测踢出机制：连续未下注满 3 局服务器推 123 预警（notice 事件，
        不踢人）；满 5 局推 102 离桌通知（leaveTableType 区分主动/被踢），
        连接保持不断。已实测：被踢前重发进桌指令(401)**不能**避免踢出，
        只能在被踢后重新进桌。

        kick_policy="stay"（默认）：被踢（102 推送）后自动重进该桌，
        产出 type="kick" 事件（data.dropped=False）。
        kick_policy="follow_system"：被踢即停止监控该桌，产出
        type="kick" 事件（data.dropped=True）；全部桌被踢后迭代结束。
        """
        import json as _json
        while True:
            try:
                frame = await self._conn.recv()
            except Exception:
                return
            if not frame:
                continue
            pid = frame.get("protocolId")
            info = extract_param(frame) or {}
            payload = info.get("param") or info.get("data")
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    pass

            if pid == 10026:
                raise LoginError("会话被踢（token 失效），请重新 login()")

            # 进桌响应：填快照
            if pid in (_QS_NEW_INTER_GAME, _QS_INTER_GAME) \
                    and isinstance(payload, dict):
                self._fill_snapshot(payload)
                continue

            table_id = (payload.get("tableId", 0)
                        if isinstance(payload, dict) else 0)

            # 服务器离桌推送（102）：主动离桌的确认 或 被系统踢出
            # 实测：leaveTableType=1 主动离桌(noticeId=21001)，
            #       leaveTableType=2 长时间未下注被踢(noticeId=21003)
            if pid == _QS_OUT_GAME and isinstance(payload, dict):
                if table_id in self._leaving \
                        or table_id not in self._entered \
                        or payload.get("leaveTableType") == 1:
                    continue            # 自己主动离桌的确认/无关桌，忽略
                if self.kick_policy == "follow_system":
                    # 遵循系统踢出：停止监控该桌
                    self._tables = [x for x in self._tables
                                    if x["table_id"] != table_id]
                    self._entered.discard(table_id)
                    self._road_accum.pop(table_id, None)
                    yield {"type": "kick", "protocol_id": pid,
                           "table_id": table_id,
                           "data": {"action": "dropped", "dropped": True,
                                    "raw": payload}}
                    if not self._tables:
                        return
                    continue
                # stay：自动重进该桌
                self._entered.discard(table_id)
                t = next((x for x in self._tables
                          if x["table_id"] == table_id), None)
                if t:
                    await self._enter_one(t)
                yield {"type": "kick", "protocol_id": pid,
                       "table_id": table_id,
                       "data": {"action": "auto_reenter", "dropped": False,
                                "raw": payload}}
                continue

            # 路纸事件：仅 116=全长（置换快照并重置累积）。
            # 161 是语义不可靠的增量短串，不参与累积；
            # 逐局结果由 107.roundResult 权威供给。
            if pid in (116, 160, 161) and isinstance(payload, dict):
                rp = payload.get("roadPaper")
                if rp and table_id and pid == 116:
                    if table_id in self.snapshots:
                        self.snapshots[table_id]["roadPaper"] = rp
                    b64 = rp.get("beatPlateRoad") or ""
                    if b64:
                        try:
                            flat = decode_bead_plate(b64)["flat"]
                            if flat:
                                self._road_accum[table_id] = flat
                        except Exception:
                            pass

            # 牌局事件：107 携带 roundResult（"庄点;闲点"），逐局追加路纸
            if pid == 107 and isinstance(payload, dict) and table_id:
                rid = payload.get("roundId") or 0
                if rid and rid == self._last_round_id.get(table_id):
                    pass                        # 同局重复推送，去重
                else:
                    token = round_result_token(payload.get("roundResult"))
                    if token:
                        if rid:
                            self._last_round_id[table_id] = rid
                        self._road_accum.setdefault(
                            table_id, []).append(token)

            yield {
                "type": _classify_event(pid),
                "protocol_id": pid,
                "table_id": table_id,
                "data": payload if isinstance(payload, dict) else {"raw": payload},
            }


# ── TableMonitor ─────────────────────────────────────


class TableMonitor:
    """持续多桌监控器（单/多账号统一门面）。

    内部分片：每个账号一条连接一个 MultiTableSession；
    对外表现为单一事件流 + 统一快照表。
    **不内置任何自动退出**——leave_table()/aclose() 由调用方控制。
    """

    def __init__(self, shards: list[MultiTableSession], refresh_cb_factory):
        self._shards = shards
        self._refresh_cb_factory = refresh_cb_factory
        self._closed = False

    # ── 生命周期 ──

    async def __aenter__(self) -> "TableMonitor":
        """逐分片建连：限速 + 重试 + 失败降级（防 WAF 连接风暴）。

        实测平台对同 IP 的 WS 新建连有速率/并发限制（密集建连会
        收到 HTTP 403 并可能触发短时封禁）。因此分片间间隔
        _SHARD_CONNECT_INTERVAL_S 秒，失败按指数退避重试
        _SHARD_CONNECT_RETRIES 次；仍失败的分片剔除出列表降级运行
        （其初始桌会丢失，日志告警）。全部分片失败才抛 LoginError。
        """
        live: list[MultiTableSession] = []
        for i, shard in enumerate(self._shards):
            if i:
                await asyncio.sleep(_SHARD_CONNECT_INTERVAL_S)
            err: Exception | None = None
            for attempt in range(_SHARD_CONNECT_RETRIES):
                try:
                    await shard.__aenter__()
                    err = None
                    break
                except Exception as e:      # 含 403 握手拒绝等
                    err = e
                    await asyncio.sleep(
                        _SHARD_RETRY_BACKOFF_S * (attempt + 1))
            if err is None:
                live.append(shard)
            else:
                logger.warning(
                    f"[TableMonitor] 分片连接失败已剔除: {err}"
                    f"（损失 {len(shard._tables)} 张初始桌）")
                try:                        # 可能半连接，兜底关闭防泄漏
                    await shard.__aexit__(None, None, None)
                except Exception:
                    pass
        if not live:
            raise LoginError("TableMonitor: 所有分片连接均失败")
        if len(live) < len(self._shards):
            logger.warning(
                f"[TableMonitor] {len(self._shards) - len(live)} 个分片"
                f"被剔除，以 {len(live)} 个分片降级运行")
        self._shards = live
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        """停止全部监控（离所有桌、断所有连接）。幂等。"""
        if self._closed:
            return
        self._closed = True
        for shard in self._shards:
            try:
                await shard.__aexit__(None, None, None)
            except Exception:
                pass

    # ── 数据访问 ──

    @property
    def snapshots(self) -> dict[int, dict]:
        """全部监控桌的快照 {table_id: snapshot}。"""
        merged: dict[int, dict] = {}
        for shard in self._shards:
            merged.update(shard.snapshots)
        return merged

    def road_flat(self, table_id: int) -> str:
        """指定桌当前珠盘路。"""
        for shard in self._shards:
            if table_id in shard.snapshots:
                return shard.road_flat(table_id)
        return ""

    @property
    def table_ids(self) -> list[int]:
        """当前监控中的桌台 id 列表。"""
        return [t["table_id"] for s in self._shards for t in s._tables]

    # ── 动态控制 ──

    async def add_table(self, table: dict):
        """动态加入一张桌（分配到负载最小的分片）。"""
        shard = min(self._shards, key=lambda s: len(s._tables))
        t = {"table_id": int(table["table_id"]),
             "game_type_id": int(table.get("game_type_id", 2001))}
        shard._tables.append(t)
        await shard._enter_one(t)

    async def leave_table(self, table_id: int):
        """主动退出某桌（其他桌不受影响）。"""
        for shard in self._shards:
            t = next((x for x in shard._tables
                      if x["table_id"] == table_id), None)
            if t:
                await shard._leave_one(t)
                shard._tables.remove(t)
                shard.snapshots.pop(table_id, None)
                shard._road_accum.pop(table_id, None)
                return

    # ── 事件流 ──

    async def events(self) -> AsyncIterator[dict]:
        """全部分片合并的统一事件流。

        每个分片一个转发任务汇入队列；aclose() 后迭代自然结束。
        """
        queue: asyncio.Queue = asyncio.Queue()
        done = asyncio.Event()

        async def pump(shard: MultiTableSession):
            try:
                async for ev in shard.events():
                    await queue.put(ev)
                    if self._closed:
                        return
            except Exception as e:
                await queue.put({"type": "error", "protocol_id": 0,
                                 "table_id": 0, "data": {"error": str(e)}})
            finally:
                done.set()

        tasks = [asyncio.create_task(pump(s)) for s in self._shards]
        try:
            while not self._closed:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if self._closed or (done.is_set() and queue.empty()):
                        break
                    continue
                yield ev
        finally:
            for task in tasks:
                task.cancel()


# ── 内部工具 ──────────────────────────────────────────


def _gateway_request(method: str, url: str, payload: dict | None,
                     session: dict, timestamp: int = 0) -> dict:
    """game-http gateway 请求（内部）。

    GET: 响应为明文 JSON；
    POST: 请求体 gateway_encrypt(payload)，另需加签的 token 头，
          响应为 gateway_encrypt 加密体，解密后返回。
    """
    import base64 as _b64
    import hashlib as _hash
    import hmac as _hmac
    from curl_cffi import requests
    from hdata.protocol.codec import (
        GATEWAY_KEY, gateway_decrypt, gateway_encrypt)

    keyid = "probinpjms7rfm26"  # release keyid（大厅 bundle 硬编码）
    headers = {
        "deviceType": "15",
        "model": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/149.0.0.0 Safari/537.36"),
        "deviceId": session.get("device_id", "") or f"{int(time.time()*1000)}-1",
        "X-Request-Token": session.get("game_token", ""),
        "keyid": keyid,
        "Content-Type": "application/json;charset=UTF-8",
    }
    body: bytes | None = None
    if method == "POST" and payload is not None:
        enc = gateway_encrypt(payload)
        sign = _b64.b64encode(_hmac.new(
            GATEWAY_KEY, (enc + "0" + str(timestamp)).encode(),
            _hash.sha1).digest()).decode()
        meta = {"encrypted": True, "gzipped": True, "platform": "h5",
                "version": "1.2.2", "application": "game_http",
                "timestamp": timestamp, "nonce": 0,
                "sign": sign, "keyid": keyid}
        headers["token"] = gateway_encrypt(meta)
        body = enc.encode()

    proxy = session.get("proxy") or ""
    resp = requests.request(
        method, url, data=body, headers=headers,
        impersonate="chrome110", timeout=15,
        proxies={"http": proxy, "https": proxy} if proxy else None)
    resp.raise_for_status()
    text = resp.text
    try:
        return _json_loads(text)
    except Exception:
        return gateway_decrypt(text)


def _json_loads(s: str):
    import json as _j
    return _j.loads(s)


def _classify_event(protocol_id: int) -> str:
    """协议号 → 事件类型名。"""
    return {
        102: "leave",      # 离桌推送（主动/被踢，leaveTableType 区分）
        104: "round",      # 局状态（roundNo/countdown/bootIndex）
        106: "card",       # 发牌
        107: "card",       # 牌局事件
        110: "bet",        # 桌台动态（在线/投注/奖池）
        116: "road",       # 路纸
        123: "notice",     # 系统通知（如连续3局未下注预警 noticeId=21002）
        160: "road",       # 路纸更新
        161: "road",       # 路纸更新
        171: "status",     # 桌台状态
        10052: "lobby",    # 大厅快照
    }.get(protocol_id, "other")


def _table_info_from_snapshot(table_id: str, t: dict,
                              meta: dict | None = None) -> Optional[TableInfo]:
    """从 10052 快照构造 TableInfo；meta（10053）提供桌名与官方玩法名。"""
    gt = t.get("gameTypeId")
    if not gt:
        return None
    try:
        tid = int(table_id)
    except (TypeError, ValueError):
        return None
    m = (meta or {}).get(tid) or {}
    rp = t.get("roadPaper") or {}
    flat = ""
    if rp.get("beatPlateRoad"):
        try:
            flat = "".join(decode_bead_plate(rp["beatPlateRoad"])["flat"])
        except Exception:
            flat = ""
    online = 0
    ton = t.get("tableOnline")
    if isinstance(ton, dict):
        online = ton.get("onlineNumber", 0) or 0
    good_roads = [
        GOOD_ROAD_NAMES.get(p.get("goodRoadType"),
                            f"类型{p.get('goodRoadType')}")
        for p in (t.get("goodRoadPoints") or [])
        if isinstance(p, dict) and p.get("goodRoadFlag")
    ]
    return TableInfo(
        table_id=tid,
        game_type_id=gt,
        game_type_name=m.get("gameTypeName")
        or _GAME_TYPE_NAMES.get(gt, f"类型{gt}"),
        table_name=m.get("tableName") or t.get("tableName", "") or "",
        status=t.get("gameStatus", 0) or 0,
        online=online,
        boot_no=t.get("bootNo", "") or m.get("bootNo", "") or "",
        road_flat=flat,
        road_count=len(flat),
        good_roads=good_roads,
    )


__all__ = [
    "GameClient",
    "TableInfo",
    "TableSession",
    "MultiTableSession",
    "TableMonitor",
    "road_streak",
    "GOOD_ROAD_NAMES",
    "LoginError",
]
