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

from htools.utils.logger import get_logger

from hdata.auth.session import (
    LoginError,
)
from hdata.auth.session import (
    get_login as _session_login,
)

from ._shared import (
    _FORCE_101_GAME_TYPES,
    _GAME_TYPE_NAMES,
    _HEARTBEAT_INTERVAL,
    _HT_SEAT,
    _IT_MULTIPLAY,
    _OT_MULTIPLE,
    _PT_BASE,
    _QS_GAME_LIST_SWITCH_TAB,
    _QS_HEARTBEAT,
    _QS_INTER_GAME,
    _QS_INTER_MULTIPLE,
    _QS_NEW_INTER_GAME,
    _QS_NOTICE,
    _QS_OUT_GAME,
    _QS_PROT_DECODE_CONFIG,
    _QS_TABLE_DATA_UPDATE,
    _QS_TABLE_LIST_ALL,
    _QS_TABLE_LIST_LIMIT,
    _RECV_SILENCE_S,
    _SHARD_CONNECT_INTERVAL_S,
    _SHARD_CONNECT_RETRIES,
    _SHARD_RETRY_BACKOFF_S,
    _YT_ALL_GAME,
    GOOD_ROAD_NAMES,
    OT_HALL,
    TableInfo,
    build_hall_switch_msg,
    road_streak,
    round_result_token,
)
from .gateway import _gateway_request, _json_loads
from .tables import (
    MultiplaySession,
    MultiTableSession,
    TableMonitor,
    TableSession,
    _classify_event,
    _EnterPacer,
    _table_info_from_snapshot,
)
from .transport import _extract_lobby_tables, _WSConnection

logger = get_logger("hdata.client")

# ── game_token 刷新节流 ──────────────────────────────
#
# 平台对同 IP 的 JWT 刷新接口有速率限制（2026-07-20 实测：缓存
# 全部命中时多账号密集建连，前 5 次刷新成功、第 6 次被拒，精确
# 阈值未实测）。刷新被拒若直接兜底完整重登（打码），代价高且会
# 进一步放大请求密度。策略：
#   1. 进程级最小间隔：所有刷新串行排队，间隔 >= MIN_INTERVAL；
#   2. 新鲜跳过：session["_refresh_ts"] 在每次成功刷新后记录，
#      SKIP_S 内不再重复刷新（登录流程刚刷过的 token 直接复用）——
#      但 jti 单连接消费：一张 token 被一条 WS 连接登录成功后即死，
#      跨连接复用必 10026。故跳过前提追加"未被消费"
#      （session["_token_consumed"]，由 _WSConnection._login 成功时置位、
#      每次刷新成功后复位）；
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
        entry_urls: 入口站候选列表（多品牌平台域名，共用同一系统/API）。
                    登录/刷新链路会尝试其中任一入口解析真实域名；任一
                    入口存活即认为平台在线。优先级低于 entry_url。
        geepass_token: geepass 打码平台 token（纯 HTTP 登录用）
        jfbym_token: jfbym 打码平台 token（纯 HTTP 登录用）
        proxy: 默认代理 URL（可选）。token 绑定登录 IP——传入后
               login/refresh/WS 全部走该出口；login() 的 proxy
               参数可逐次覆盖
    """

    def __init__(self, entry_url: str,
                 geepass_token: str = "", jfbym_token: str = "",
                 proxy: str | None = None,
                 entry_urls: list[str] | None = None):
        self._entry_url = entry_url
        self._entry_urls = list(entry_urls) if entry_urls else None
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
            entry_urls=self._entry_urls,
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
        session["_token_consumed"] = False   # 新 token 尚未被任何连接消费
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
                          game_type_id: int = 2001,
                          road_init: str = "") -> TableSession:
        """进入指定桌台，返回 TableSession（异步上下文管理器）。

        Args:
            table_id: 目标桌台 ID（来自 get_tables）
            game_type_id: 游戏类型（默认 2001 经典百家乐）
            road_init: 可选，进桌前已知的路纸初值（通常取 get_tables()
                返回项的 road_flat）。401 快照的 beatPlateRoad 进桌瞬间
                通常为空，传了初值则进桌后 road_flat() 立即可读；
                第一条 116 全长路纸到达后会重置为权威值。

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
        ts = TableSession(conn, table_id, game_type_id, road_init=road_init)
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
        if (not session.get("_token_consumed")
                and time.time() - session.get("_refresh_ts", 0) < _REFRESH_SKIP_S):
            return session
        for attempt in (1, 2):
            try:
                return await self._refresh_game_token(account, session)
            except Exception as e:
                if attempt == 1:
                    logger.warning(f"[{account}] 建连前刷新失败"
                                   f"（{e}），"
                                   f"{_REFRESH_RETRY_DELAY_S:.0f}s 后重试")
                    await asyncio.sleep(_REFRESH_RETRY_DELAY_S)
                else:
                    logger.warning(f"[{account}] 建连前刷新重试仍失败"
                                   f"（{e}）")
        # 站点会话整体失效 → 用会话所属账号完整重新登录
        password = session.get("_password") or (
            self._password if account == self._account else "")
        if not password:
            raise LoginError(f"[{account}] game_token 刷新失败且无密码可兜底重登")
        logger.warning(f"[{account}] 站点会话失效，完整重登兜底（可能打码）")
        fresh = await _session_login(
            account, password,
            entry_url=self._entry_url, entry_urls=self._entry_urls,
            force_refresh=True,
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
                           kick_policy: str = "stay") -> MultiTableSession:
        """同时进入多张桌台监控（共享一条 WS 连接）。

        实测确认：同一账号在**一条连接**上可同时进多桌（服务端按连接
        限制而非按桌限制），事件流按 table_id 区分。**不需要多账号**。

        Args:
            tables: 桌台列表，每项至少含 {"table_id": int,
                    "game_type_id": int}（即 get_tables() 的返回项）
            kick_policy: 被系统踢出（连续5局未下注）时的策略——
                "stay"（默认）：被踢后自动重进该桌，监控不中断；
                "follow_system"：遵循系统踢出，该桌停止监控。
                （"rotate" 对单连接无意义：无其他账号可换，被踢桌
                摘除后不再重进，仅空桌后连接保活——请改用
                monitor_tables 多账号模式。）

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
                             kick_policy: str = "stay",
                             connect_interval_s: float = 0,
                             readd_interval_s: float = 18.0,
                             readd_jitter_s: float = 5.0
                             ) -> TableMonitor:
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
                "stay"（默认）：被踢后同账号自动重进该桌，监控不中断；
                "rotate"：被踢后换另一个账号分片重进该桌（降低单账号
                    反复被踢的曝光；仅一个存活分片时退回同账号重进）；
                "follow_system"：遵循系统踢出，该桌停止监控。
            connect_interval_s: 同一代理出口的 WS 建连间隔（秒），
                <=0 时用模块默认 _SHARD_CONNECT_INTERVAL_S（18s）。
                不同出口的连接并行建立；无代理全部视为同一组。
            readd_interval_s: **每账号**进桌间隔均值（秒，默认 18）。
                各分片独立节奏器并行：同一账号的进桌指令（首轮铺桌/
                补桌/被踢 rotate/失效接管四路统一排队）按
                readd_interval_s ± readd_jitter_s 随机间隔串行发送。
            readd_jitter_s: 进桌间隔随机抖动幅度（秒，默认 ±5）。

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
                entry_url=self._entry_url, entry_urls=self._entry_urls,
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
            shards.append(MultiTableSession(
                conn, ts, kick_policy=kick_policy,
                readd_interval_s=readd_interval_s,
                readd_jitter_s=readd_jitter_s))
        return TableMonitor(shards, self._make_refresh_cb,
                            connect_interval_s=connect_interval_s)

    def _make_refresh_cb(self):
        """生成与账号无关的刷新回调（复用 _refresh_cb 的兜底逻辑）。"""
        async def _cb(session: dict) -> dict:
            return await self._refresh_cb(session)
        return _cb


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
