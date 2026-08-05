"""WS 传输层：_WSConnection（握手 + 登录 + 帧收发 + 心跳/看门狗）与大厅帧解析叶子。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from htools.utils.logger import get_logger

from hdata.auth.session import LoginError, build_ws_config
from hdata.protocol.codec import (
    DEVICE_TYPE_PC,
    FS_LOGIN,
    OT_HALL,
    build_login_msg,
    build_message,
    decode_frame,
    encode_frame,
    extract_param,
)
from hdata.protocol.schemacodec import schema_decode

from ._shared import (
    _HEARTBEAT_INTERVAL,
    _QS_HEARTBEAT,
    _QS_PROT_DECODE_CONFIG,
    _QS_TABLE_DATA_UPDATE,
    _QS_TABLE_LIST_ALL,
    _QS_TABLE_LIST_LIMIT,
    _QS_TABLE_ROAD,
    _RECV_SILENCE_S,
    build_hall_switch_msg,
)

logger = get_logger("hdata.client")

def _extract_lobby_tables(data: Any) -> dict:
    """从 10089/10052 帧载荷提取桌台表（宽容解析，未知结构返回 {}）。

    已见结构：{"gameTableMap": {"<tid>": {...}}}（10052 增量）。
    10089 响应结构未实测，按候选字段（gameTableMap / tableIds /
    tableIdList / tables / list / ids）宽容提取；元素可以是桌台 id
    整数或含 tableId/gameTypeId 的 dict。返回值统一为
    {str(tid): {...}}，缺省字段的给 {"tableId": tid}。
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}
    gtm = data.get("gameTableMap")
    if isinstance(gtm, dict):
        return gtm
    for key in ("tableIds", "tableIdList", "tables", "list", "ids",
                "allTableIds", "tableList"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        out: dict = {}
        for it in items:
            if isinstance(it, int):
                out[str(it)] = {"tableId": it}
            elif isinstance(it, dict) and it.get("tableId") is not None:
                out[str(it["tableId"])] = it
        if out:
            return out
    return {}
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
        # 是否预期有下行流量（由上层 MultiTableSession 按在监桌数同步）。
        # 空闲分片（0 桌）收不到任何帧是正常态——心跳是客户端单向发送、
        # 服务端不回，若照样按收帧静默判死，会把全部空闲分片每 120s
        # 误杀重建一轮（2026-08-02 02:25 凌晨桌少时实测发生）。空闲片
        # 无桌可损，且 TCP 半死会在 10s 心跳发送侧暴露，无需看门狗。
        self.expect_traffic = True

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

    async def __aenter__(self) -> _WSConnection:
        import websockets
        if self._on_before_connect:
            # 刷新/重登全部失败时**不沿用旧 token 建连**——旧 token 建连
            # 可能 WS 登录成功但会话已作废，进桌全哑（2026-08-01 22:52
            # 全体断连后 14 账号静默假死事故的机制）。宁可本片建连失败
            # 走死片/顶替/修复链路，也不产出"连接活着但没数据"的哑片。
            self._session = await self._on_before_connect(self._session)
        self._rebuild_cfg()
        self._player_id = self._session.get("game_player_id", 0)
        from hdata.auth.fingerprint import get_ua
        self._ws = await websockets.connect(
            self._cfg["ws_url"], open_timeout=12, close_timeout=3,
            max_size=50 * 1024 * 1024,
            # 关闭传输层 ping：官方浏览器客户端不发 WS ping 帧（JS 无此
            # 能力），库的默认 20s ping/pong 既是多余指纹，又会在事件
            # 循环被进桌风暴打满时 pong 排队超时，把全部连接以 1011
            # 自杀（2026-08-01 两次全体断连的直接凶手）。保活完全靠
            # 应用层 10s 心跳（pid=3）+ 收帧静默看门狗。
            ping_interval=None,
            additional_headers={
                "User-Agent": get_ua(self._session.get("account", "")),
            },
            proxy=self._session.get("proxy") or None)
        await self._login()
        self._last_recv = time.monotonic()
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        self._wd_task = asyncio.create_task(self._silence_watchdog())
        return self

    async def __aexit__(self, *exc):
        for attr in ("_hb_task", "_wd_task"):
            task = getattr(self, attr, None)
            if task:
                task.cancel()
                setattr(self, attr, None)
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _silence_watchdog(self):
        """收帧静默判死（关闭传输层 ping 后的连接死活检测）。

        健康连接订阅着桌台/大厅，推送不断；静默超过 _RECV_SILENCE_S
        说明底层 TCP 已半死（无关闭帧的断连不会有任何异常抛出），
        主动关闭让 recv 侧抛 ConnectionClosed，走既有分片重建链路。

        expect_traffic=False（空闲分片，0 桌在监）跳过判死：收不到
        帧是正常态，误杀会白刷一轮重建+强制会话刷新（徒增风控指纹）。
        """
        try:
            while True:
                await asyncio.sleep(10)
                silence = time.monotonic() - getattr(
                    self, "_last_recv", time.monotonic())
                if silence > _RECV_SILENCE_S and self.expect_traffic:
                    logger.warning(
                        f"[{self._session.get('account', '?')}] "
                        f"收帧静默 {silence:.0f}s 判连接死亡，主动断开重建")
                    await self._ws.close()
                    return
        except asyncio.CancelledError:
            return
        except Exception:
            return

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
                    self._apply_login_payload(info)
                    # jti 单连接消费：本连接登录成功即标记该 token 已消费，
                    # 下一条新连接建连前 _refresh_cb 会强制刷新（见节流策略注释）。
                    self._session["_token_consumed"] = True
                    return
                raise LoginError(f"WS 登录被拒: {info.get('msg')}")
            if pid == 10026:
                raise LoginError("WS 登录被踢: token 失效")
        raise LoginError("WS 登录超时")

    def _apply_login_payload(self, info: dict):
        """处理登录响应 data 内层载荷（schema 热更新真实载体）。

        登录响应 data 是 JSON 字符串，内层含：
        - protocolCodecConfig: {proto_key: {version/root/state/schemas}}
          服务器下发的**当前完整 schema 定义**——官方客户端
          onLoginResp → syncProtocolConfig 热替换解码器的数据源。
          2026-08-02 实锤：schema 热更新不靠 10115 WS 推送，靠这里。
          漏掉它=永远用旧 schema 解新编码=逐字节错位（10089 大厅
          231 桌解成 246 假元素一半丢 tableId 的事故）。
        - totalTable: 平台当前桌台总数（记日志便于核对大厅完整性）。
        失败只告警不抛错，绝不能让登录主流程崩掉。
        """
        try:
            from hdata.protocol.codec import update_protocol_codec_config
            from hdata.protocol.schemacodec import update_schema_config
            data = info.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return
            account = (self._session or {}).get("account", "?")
            total = data.get("totalTable")
            if total is not None:
                self._session["total_table"] = total
                logger.info(f"[{account}] 登录响应：平台总桌数 {total}")
            cfg = data.get("protocolCodecConfig")
            if not isinstance(cfg, dict) or not cfg:
                return
            updated = [k for k, v in cfg.items()
                       if update_schema_config(str(k), v)]
            update_protocol_codec_config(cfg)
            if updated:
                logger.warning(
                    f"[{account}] 登录响应 schema 热更新: {updated}")
        except Exception as e:  # noqa: BLE001 — 解析未知形态只告警
            logger.warning(f"登录响应 protocolCodecConfig 处理失败（忽略）: {e!r}")

    async def send(self, msg: dict):
        await self._ws.send(encode_frame(msg))

    async def recv(self) -> dict | None:
        raw = await self._ws.recv()
        self._last_recv = time.monotonic()
        if isinstance(raw, str):
            return None
        frame = decode_frame(raw)
        if frame.get("protocolId") == _QS_PROT_DECODE_CONFIG:
            self._on_prot_decode_config(frame)
        return frame

    def _on_prot_decode_config(self, frame: dict):
        """处理 10115 PROT_DECODE_CONFIG：服务器热更新 schema 指纹。

        载荷形态（JS 静态分析，未抓包实测，解析务必宽容）:
            {"protocolCodecConfig": {"10089_7": {"version": "...", "state": 1, ...}}}
        state=1(ENABLE) 更新、state=0(DISABLE) 摘除。失败只告警不抛错，
        绝不能让收帧主循环崩掉。
        """
        try:
            from hdata.protocol.codec import update_protocol_codec_config
            from hdata.protocol.schemacodec import update_schema_config
            info = extract_param(frame)
            data = info.get("param") or info.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return
            cfg = data.get("protocolCodecConfig")
            if not isinstance(cfg, dict):
                return
            # 完整 schema 定义热更新解码器（含 schemas 时；登录响应同款）
            updated = [k for k, v in cfg.items()
                       if isinstance(v, dict)
                       and update_schema_config(str(k), v)]
            changes = update_protocol_codec_config(cfg)
            account = (self._session or {}).get("account", "?")
            if updated:
                logger.warning(
                    "[%s] 10115 schema 定义热更新: %s", account, updated)
            if changes:
                logger.warning(
                    "[%s] 10115 schema 指纹热更新: %s", account,
                    {k: {"old": v[0], "new": v[1]} for k, v in changes.items()})
            if not updated and not changes:
                logger.debug("[%s] 10115 schema 配置与本地一致", account)
        except Exception as e:  # noqa: BLE001 — 解析未知形态只告警
            logger.warning("10115 schema 配置解析失败（忽略）: %r", e)

    async def recv_until(self, predicate, timeout: float) -> dict | None:
        """持续收帧直到 predicate(frame) 为真或超时。"""
        end = time.time() + timeout
        while time.time() < end:
            try:
                frame = await asyncio.wait_for(
                    self.recv(), timeout=max(0.1, end - time.time()))
            except TimeoutError:
                return None
            if frame and predicate(frame):
                return frame
        return None

    async def fetch_table_map(self) -> dict:
        """拉取大厅桌台快照（先 10027 订阅，再 10089；聚合 10089 响应 + 10052 增量）。"""
        # 官方流程：先 10027 订阅大厅推送，再 10089 拉全量桌台；
        # 只发 10089 服务器可能不下发/不下全 10052 增量
        await self.send(build_hall_switch_msg(self._player_id,
                                              self._device_id))
        await self.send(build_message(
            _QS_TABLE_LIST_ALL, {"labelTypeId": 1},
            player_id=self._player_id, game_type_id=2013,
            service_type_id=OT_HALL))
        gtm: dict = {}
        end = time.time() + 20
        # 10089 响应（全量桌台）+ 10052 分批增量推送；
        # 持续收直到超时或长时间无新桌
        last_new = time.time()
        logged_10089 = False
        while time.time() < end:
            try:
                frame = await asyncio.wait_for(
                    self.recv(), timeout=max(0.1, min(3.0, end - time.time())))
            except TimeoutError:
                if time.time() - last_new > 3:
                    break
                continue
            if not frame:
                continue
            pid = frame.get("protocolId")
            if pid not in (_QS_TABLE_LIST_ALL, _QS_TABLE_DATA_UPDATE):
                continue
            info = extract_param(frame) or {}
            data = info.get("param") or info.get("data")
            new = _extract_lobby_tables(data)
            if pid == _QS_TABLE_LIST_ALL and not logged_10089:
                logged_10089 = True
                keys = sorted(data.keys()) if isinstance(data, dict) \
                    else type(data).__name__
                logger.info(f"[大厅] 10089 响应帧：提取 {len(new)} 桌"
                            f"（载荷字段: {keys}）")
            if new:
                gtm.update(new)
                last_new = time.time()
        return gtm

    async def fetch_table_road(self, table_ids: list[int],
                               timeout: float = 10.0) -> dict:
        """主动拉取指定桌全量路纸（官方 sendReqRoadPaper → 10071 TABLE_ROAD）。

        2026-08-05 逆向官方前端：`sendReqRoadPaper(tableId)` →
        `sendGetTableListRoad([tableId])` → 发 10071(TABLE_ROAD)，响应带
        `roadPaperCacheMap`（请求桌的全量路纸缓存）。这是拉路纸的专用协议，
        比 10053(TABLE_LIST_LIMIT) 更可靠。

        Args:
            table_ids: 要拉路纸的桌 id 列表。
            timeout: 等待响应的总超时（秒）。

        Returns:
            {table_id(str): {"roadPaper": {...}, ...}} —— 收到的全量路纸缓存；
            未收到返回 {}。
        """
        await self.send(build_message(
            _QS_TABLE_ROAD,
            {"playerId": self._player_id, "tableIds": list(table_ids)},
            player_id=self._player_id, game_type_id=2013,
            service_type_id=OT_HALL))
        out: dict = {}
        end = time.time() + timeout
        while time.time() < end:
            frame = await self.recv_until(
                lambda f: f and f.get("protocolId") == _QS_TABLE_ROAD,
                end - time.time())
            if not frame:
                break
            info = extract_param(frame) or {}
            data = info.get("param") or info.get("data")
            if not isinstance(data, dict):
                continue
            rcm = data.get("roadPaperCacheMap")
            if isinstance(rcm, dict):
                out.update(rcm)
                break  # 10071 一次返回全部请求桌的路纸缓存
        return out

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
                except TimeoutError:
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
                except TimeoutError:
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
