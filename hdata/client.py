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
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

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

# ── 协议常量（内部使用，不导出） ──
_QS_TABLE_LIST_ALL = 10089
_QS_NEW_INTER_GAME = 401
_QS_INTER_GAME = 101
_QS_OUT_GAME = 102
_KICK_OUT_GAME = 123
_FORCE_101_GAME_TYPES = {2003, 2004, 2014, 2020}

_HT_SEAT = 1
_PT_BASE = 2


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
        }


# gameTypeId → 名称（常见值，未知用原始 id）
_GAME_TYPE_NAMES = {
    2001: "经典百家乐", 2002: "快速百家乐", 2003: "竞价百家乐",
    2004: "VIP百家乐", 2005: "咪牌百家乐", 2006: "龙虎",
    2007: "轮盘", 2008: "骰宝", 2009: "牛牛", 2010: "赢三张",
    2013: "多台", 2016: "保险百家乐",
}


class GameClient:
    """游戏平台数据采集客户端（对外门面）。

    平台无关设计：内部按平台适配层实现，leyu 为当前已接入平台；
    后续接入其他子平台时本类接口保持不变。

    Args:
        entry_url: 平台入口种子站（由调用者提供，如平台官网域名）
        geepass_token: geepass 打码平台 token（纯 HTTP 登录用）
        jfbym_token: jfbym 打码平台 token（纯 HTTP 登录用）
    """

    def __init__(self, entry_url: str,
                 geepass_token: str = "", jfbym_token: str = ""):
        self._entry_url = entry_url
        self._geepass_token = geepass_token
        self._jfbym_token = jfbym_token
        self._session: dict | None = None

    # ── 1. 登录 ───────────────────────────────────────

    async def login(self, account: str, password: str = "",
                    force_refresh: bool = False) -> dict:
        """登录，返回会话凭证 dict。

        Args:
            account: 平台账号
            password: 密码（有有效缓存时可为空）
            force_refresh: 跳过缓存强制重新登录

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
        )
        # 确保 game_token 是服务端当前认可的最新一张：
        # 缓存里的旧 token 可能已被服务端作废（jti 踢出），先刷新一次。
        session = await self._refresh_game_token(account, session)
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
        """用站点会话刷新 game_token 并写回缓存。"""
        from hdata.auth.params import decode_jwt
        from hdata.auth.session import refresh_game_session, save_session
        try:
            params = await refresh_game_session(account, session)
        except Exception:
            return session  # 刷新失败则沿用原 token（可能仍有效）
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
        tables = [_table_info_from_snapshot(tid, t)
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
        """每次新建 WS 连接前刷新 game_token（服务端不允许同 token 重连）。"""
        account = session.get("account", "")
        return await self._refresh_game_token(account, session)


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
            except Exception:
                pass  # 刷新失败则沿用现有 token
        self._rebuild_cfg()
        self._player_id = self._session.get("game_player_id", 0)
        self._ws = await websockets.connect(
            self._cfg["ws_url"], open_timeout=12, close_timeout=3,
            max_size=50 * 1024 * 1024)
        await self._login()
        return self

    async def __aexit__(self, *exc):
        if self._ws:
            await self._ws.close()
            self._ws = None

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
        # 等 401 响应（全量快照）
        frame = await self._conn.recv_until(
            lambda f: f.get("protocolId") == proto, timeout=15)
        if frame:
            info = extract_param(frame) or {}
            payload = info.get("param") or info.get("data")
            import json as _json
            if isinstance(payload, str):
                payload = _json.loads(payload)
            self.snapshot = (payload or {}).get("gameTableInfo") or {}
        self._entered = True

    async def _leave(self):
        if not self._entered:
            return
        try:
            await self._conn.send(build_message(
                _QS_OUT_GAME, {}, player_id=self._conn._player_id,
                game_type_id=self.game_type_id, table_id=self.table_id,
                service_type_id=OT_GAME))
        except Exception:
            pass
        self._entered = False

    # ── 公开读取接口 ──

    def road_flat(self) -> str:
        """当前珠盘 B/P/T 序列（从快照路纸解码）。"""
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
            if pid == _KICK_OUT_GAME:
                # 桌台级踢出：自动重进后继续
                await self._enter()
                yield {"type": "kick", "protocol_id": pid,
                       "table_id": self.table_id,
                       "data": {"action": "auto_reenter"}}
                continue

            yield {
                "type": _classify_event(pid),
                "protocol_id": pid,
                "table_id": self.table_id,
                "data": payload if isinstance(payload, dict) else {"raw": payload},
            }


# ── 内部工具 ──────────────────────────────────────────


def _classify_event(protocol_id: int) -> str:
    """协议号 → 事件类型名。"""
    return {
        104: "round",      # 局状态（roundNo/countdown/bootIndex）
        106: "card",       # 发牌
        107: "card",       # 牌局事件
        110: "bet",        # 桌台动态（在线/投注/奖池）
        116: "road",       # 路纸
        160: "road",       # 路纸更新
        161: "road",       # 路纸更新
        171: "status",     # 桌台状态
        10052: "lobby",    # 大厅快照
    }.get(protocol_id, "other")


def _table_info_from_snapshot(table_id: str, t: dict) -> Optional[TableInfo]:
    """从 10052 快照构造 TableInfo。"""
    gt = t.get("gameTypeId")
    if not gt:
        return None
    try:
        tid = int(table_id)
    except (TypeError, ValueError):
        return None
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
    return TableInfo(
        table_id=tid,
        game_type_id=gt,
        game_type_name=_GAME_TYPE_NAMES.get(gt, f"类型{gt}"),
        table_name=t.get("tableName", "") or "",
        status=t.get("gameStatus", 0) or 0,
        online=online,
        boot_no=t.get("bootNo", "") or "",
        road_flat=flat,
        road_count=len(flat),
    )


__all__ = [
    "GameClient",
    "TableInfo",
    "TableSession",
    "LoginError",
]
