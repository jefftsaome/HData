"""桌台状态机会话：TableSession / MultiTableSession / TableMonitor / MultiplaySession、
每账号进桌节奏器 _EnterPacer 与事件分类/快照构造叶子。"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import AsyncIterator

from htools.utils.logger import get_logger

from hdata.auth.session import LoginError
from hdata.protocol.codec import (
    DEVICE_TYPE_PC,
    OT_GAME,
    build_message,
    extract_param,
)
from hdata.protocol.roadpaper import decode_bead_plate
from hdata.protocol.schemacodec import schema_decode
from hdata.time import check_offset, compute_offset, format_diff

from ._shared import (
    _FORCE_101_GAME_TYPES,
    _GAME_TYPE_NAMES,
    _HT_SEAT,
    _IT_MULTIPLAY,
    _OT_MULTIPLE,
    _PT_BASE,
    _QS_INTER_GAME,
    _QS_INTER_MULTIPLE,
    _QS_NEW_INTER_GAME,
    _QS_OUT_GAME,
    _SHARD_CONNECT_INTERVAL_S,
    _SHARD_CONNECT_RETRIES,
    _SHARD_RETRY_BACKOFF_S,
    GOOD_ROAD_NAMES,
    TableInfo,
    build_hall_switch_msg,
    round_result_token,
)
from .gateway import _json_loads
from .transport import _WSConnection

logger = get_logger("hdata.client")

# ── TableSession ─────────────────────────────────────


class TableSession:
    """一张桌的会话（进桌后的数据通道）。

    用 `async with` 进入；退出时自动离桌。
    被踢出（连续5局未投注）时内部自动重进，events() 不中断。
    """

    def __init__(self, conn: _WSConnection, table_id: int, game_type_id: int,
                 road_init: str = ""):
        self._conn = conn
        self.table_id = table_id
        self.game_type_id = game_type_id
        self.snapshot: dict = {}
        self._entered = False
        self._leaving = False   # 主动离桌中（防把离桌确认误判为被踢）
        # 珠盘累积（116全长重置 / 107逐局追加）；road_init 为进桌前初值
        #（通常来自大厅 road_flat），第一条 116 到达后被权威全长覆盖。
        self._road_accum: list = list(road_init)
        self._last_round_id = 0       # 已入路的最大 roundId（107去重）

    async def __aenter__(self) -> TableSession:
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
            # NOTE: 跨对象私有访问,待 P4 收敛
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
            except TimeoutError:
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
                # NOTE: 跨对象私有访问,待 P4 收敛
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
# ── _EnterPacer（每账号进桌节奏器） ────────────────────


class _EnterPacer:
    """单连接进桌节奏器：队列 + 后台 worker，按账号维度限速发进桌指令。

    所有动态进桌路径（首轮铺桌/重分片补桌/被踢 rotate 重进/失效接管
    重进）统一经 MultiTableSession.request_enter() 汇入本队列——同一
    账号的进桌指令绝不并发发出；不同账号（分片）各有独立节奏器，
    天然并行。节奏模型：
      - 分片首条指令立即发送；
      - 之后每条距上条成功发送间隔 uniform(interval-jitter,
        interval+jitter) 秒（下限 _MIN_GAP_S），逐条独立随机；
      - urgent=True（被踢/接管类重进）插队到队头，但仍受最小间隔
        约束，不提前发送；
      - 发送失败重排队尾再试一次，再失败丢弃并告警（桌台簿记留在
        MultiTableSession._tables，交上层收场/重建逻辑处理）。
    """

    _MIN_GAP_S = 1.0

    def __init__(self, enter_fn, interval_s: float = 18.0,
                 jitter_s: float = 5.0, rng: random.Random | None = None):
        self._enter_fn = enter_fn
        self._interval = float(interval_s)
        self._jitter = max(0.0, float(jitter_s))
        self._rng = rng or random.Random()
        self._queue: deque[tuple[dict, bool]] = deque()  # (桌, 已重试过)
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_send = 0.0   # monotonic 时刻；0=从未发送（首条立即）
        self._closing = False

    @property
    def started(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def pending(self) -> int:
        return len(self._queue)

    def start(self):
        if not self.started:
            self._closing = False
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        """停 worker 并丢弃未发队列（连接关闭后补发无意义）。幂等。"""
        self._closing = True
        self._queue.clear()
        self._wake.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    def note_sent(self):
        """直发路径（初始桌/未 start 的降级直发）后同步节拍基准。"""
        self._last_send = time.monotonic()

    def push(self, t: dict, urgent: bool = False):
        if urgent:
            self._queue.appendleft((t, False))
        else:
            self._queue.append((t, False))
        self._wake.set()

    def _next_gap(self) -> float:
        # 下限防误配（interval 配成 0 之类），但不超过 interval 本身，
        # 以免抹掉测试/特殊场景的小间隔配置
        floor = min(self._MIN_GAP_S, self._interval)
        return max(floor, self._rng.uniform(
            self._interval - self._jitter, self._interval + self._jitter))

    async def _run(self):
        while True:
            while not self._queue:
                self._wake.clear()
                await self._wake.wait()
            if self._closing:
                return
            # 最小间隔：urgent 也不提前，只是排在队头先走
            if self._last_send:
                wait = self._next_gap() \
                    - (time.monotonic() - self._last_send)
                if wait > 0:
                    await asyncio.sleep(wait)
            if self._closing or not self._queue:
                continue
            t, retried = self._queue.popleft()
            try:
                await self._enter_fn(t)
            except Exception as e:
                # 失败也算一拍（2026-07-31 修复）：连接已死时若不更新
                # _last_send，循环会无间隔地把整队全部烧掉（首轮 200 桌
                # 入队后连接被掐，10 桌 1ms 内全部"两次失败丢弃"）。
                # 记一拍后重试按正常节奏排队尾，给分片重建留出时间窗。
                self._last_send = time.monotonic()
                if not retried and not self._closing:
                    self._queue.append((t, True))
                    logger.warning(f"[EnterPacer] 桌{t.get('table_id')} "
                                   f"进桌发送失败，重排队尾再试一次: {e}")
                else:
                    logger.warning(f"[EnterPacer] 桌{t.get('table_id')} "
                                   f"进桌指令两次发送失败，丢弃"
                                   f"（桌台簿记保留，交上层收场）: {e}")
            else:
                self._last_send = time.monotonic()
# ── MultiTableSession ────────────────────────────────


class MultiTableSession:
    """多桌监控会话（共享一条 WS 连接）。

    用 `async with` 进入；退出时自动离全部桌。
    某张桌被踢（5局未投注）时按 kick_policy 处理：
      - "stay"（默认）：该桌自动重进（同账号原地重进），其他桌不受影响；
      - "follow_system"：遵循系统踢出，该桌停止监控；
      - "rotate"：该桌从本分片摘除并上报（由 TableMonitor 换账号
        分片重进）；空分片保持连接等待新分配，不终止迭代。
    """

    def __init__(self, conn: _WSConnection, tables: list[dict],
                 kick_policy: str = "stay",
                 readd_interval_s: float = 18.0,
                 readd_jitter_s: float = 5.0,
                 rng: random.Random | None = None):
        self._conn = conn
        self._tables: list[dict] = [
            {"table_id": int(t["table_id"]),
             "game_type_id": int(t.get("game_type_id", 2001))}
            for t in tables
        ]
        if kick_policy not in ("stay", "follow_system", "rotate"):
            raise ValueError(
                "kick_policy 只能是 'stay' / 'follow_system' / 'rotate'")
        self.kick_policy = kick_policy
        # 每账号（本分片）进桌节奏器：动态进桌统一经 request_enter()
        # 排队，均值 readd_interval_s ± readd_jitter_s 随机间隔串行发送
        self._pacer = _EnterPacer(self._enter_one, readd_interval_s,
                                  readd_jitter_s, rng)
        self.snapshots: dict[int, dict] = {}
        self._entered: set[int] = set()
        self._leaving: set[int] = set()   # 主动离桌中的桌（防误判为被踢）
        self._road_accum: dict[int, list] = {}   # 每桌珠盘累积（116重置/107追加）
        self._last_round_id: dict[int, int] = {}   # 每桌已入路的最大 roundId
        self._sync_expect_traffic()

    def _sync_expect_traffic(self):
        """按在监桌数同步连接的收帧静默看门狗开关。

        0 桌空闲分片收不到任何帧属正常（心跳单向无回包），看门狗
        必须关闭，否则每 120s 误杀重建一轮；有桌即打开。_tables
        的每个变更点（含外部直改 append 的调用方）都要调本方法。
        """
        self._conn.expect_traffic = bool(self._tables)

    @property
    def account(self) -> str:
        """本分片使用的监控账号。"""
        # NOTE: 跨对象私有访问,待 P4 收敛
        return str(self._conn._session.get("account", "?"))

    async def __aenter__(self) -> MultiTableSession:
        await self._conn.__aenter__()
        self._sync_expect_traffic()   # 分片重建换新连接后默认 True，需按桌数纠正
        self._pacer.start()
        for t in self._tables:
            await self._enter_one(t)   # 初始桌随建连直发（动态进桌才走节奏器）
        if self._tables:
            self._pacer.note_sent()    # 后续排队指令与初始桌保持间隔
        return self

    async def __aexit__(self, *exc):
        await self._pacer.stop()       # 撤销未发的排队进桌指令
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
            # NOTE: 跨对象私有访问,待 P4 收敛
            proto, data, player_id=self._conn._player_id,
            game_type_id=t["game_type_id"], table_id=t["table_id"],
            service_type_id=OT_GAME))
        self._entered.add(t["table_id"])
        logger.info(f"[进桌] {self.account} 进入 桌{t['table_id']}")
        # 快照通过事件循环里的 401 响应异步填充（见 events/_fill_snapshot）

    async def request_enter(self, t: dict, urgent: bool = False):
        """动态进桌统一入口（四路收敛点）：节奏器排队限速发送。

        节奏器运行中（__aenter__ 之后）：入队即返回，后台 worker 按
        readd_interval_s ± readd_jitter_s 随机间隔串行发送；
        urgent=True（被踢 rotate / 失效接管类重进）插队队头，仍受
        最小间隔约束。节奏器未启动（未进 __aenter__ 的直驱用法）
        降级为同步直发，保持旧语义。
        """
        if self._pacer.started:
            self._pacer.push(t, urgent=urgent)
        else:
            await self._enter_one(t)
            self._pacer.note_sent()

    async def _leave_one(self, t: dict):
        if t["table_id"] not in self._entered:
            return
        self._leaving.add(t["table_id"])
        try:
            await self._conn.send(build_message(
                # NOTE: 跨对象私有访问,待 P4 收敛
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
        kick_policy="rotate"：被踢即从本分片摘除，产出 type="kick"
        事件（data.dropped=True，含 account/table），由 TableMonitor
        换账号分片重进；空分片保活等待新分配，迭代不终止。
        kick_policy="follow_system"：被踢即停止监控该桌，产出
        type="kick" 事件（data.dropped=True）；全部桌被踢后迭代结束。
        """
        import json as _json
        while True:
            try:
                frame = await self._conn.recv()
            except Exception as e:
                # 连接断开必须上抛（TableMonitor 合并层捕获后转成带账号的
                # error 事件）。此前静默 return：分片死了但本迭代"正常
                # 结束"，外层分不清"单片掉线"和"全片结束"，只能全量重建
                # （streak 侧 97 桌批量掉线事故的放大链第一环）。
                raise ConnectionError(
                    f"[{self.account}] 分片连接中断: {e}") from e
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
                if self.kick_policy in ("follow_system", "rotate"):
                    # 摘除该桌并上报；rotate 由 TableMonitor 换分片重进
                    t = next((x for x in self._tables
                              if x["table_id"] == table_id), None)
                    self._tables = [x for x in self._tables
                                    if x["table_id"] != table_id]
                    self._sync_expect_traffic()
                    self._entered.discard(table_id)
                    self._road_accum.pop(table_id, None)
                    logger.info(f"[被踢] 桌{table_id} 将 {self.account} 踢出"
                                f"（leaveTableType="
                                f"{payload.get('leaveTableType')}）")
                    yield {"type": "kick", "protocol_id": pid,
                           "table_id": table_id,
                           "data": {"action": "dropped", "dropped": True,
                                    "account": self.account, "table": t,
                                    "raw": payload}}
                    if not self._tables and self.kick_policy == "follow_system":
                        return
                    continue
                # stay：自动重进该桌（经节奏器，与其他进桌路径同一队列）
                self._entered.discard(table_id)
                t = next((x for x in self._tables
                          if x["table_id"] == table_id), None)
                if t:
                    await self.request_enter(t, urgent=True)
                logger.info(f"[被踢] 桌{table_id} 将 {self.account} 踢出，"
                            "原地排队重进")
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
                # 服务端逐帧递增序号（官方客户端严格定序依据），供落库
                # 留底——历史回放按它排序才能复刻官方客户端视角
                "frame_version": frame.get("serverLastVersion"),
                "data": payload if isinstance(payload, dict) else {"raw": payload},
            }
# ── TableMonitor ─────────────────────────────────────


class TableMonitor:
    """持续多桌监控器（单/多账号统一门面）。

    内部分片：每个账号一条连接一个 MultiTableSession；
    对外表现为单一事件流 + 统一快照表。
    **不内置任何自动退出**——leave_table()/aclose() 由调用方控制。
    """

    def __init__(self, shards: list[MultiTableSession], refresh_cb_factory,
                 connect_interval_s: float = 0):
        self._shards = shards
        self._refresh_cb_factory = refresh_cb_factory
        self._closed = False
        self._rotate = bool(shards) and shards[0].kick_policy == "rotate"
        self._connect_interval_s = (connect_interval_s
                                    if connect_interval_s > 0
                                    else _SHARD_CONNECT_INTERVAL_S)

    # ── 生命周期 ──

    async def __aenter__(self) -> TableMonitor:
        """分片建连：**按代理出口分组限速**（防 WAF 连接风暴）。

        实测平台对同 IP 的 WS 新建连有速率/并发限制（密集建连会
        收到 HTTP 403 并可能触发短时封禁；2026-07-27 30 账号经 3 出口
        集中建连后整批会话被静默）。同一出口下的分片串行、间隔
        connect_interval_s 秒（默认 18s）；不同出口的分片并行建连；
        无代理（直连）全部视为同一组。单分片失败指数退避重试
        _SHARD_CONNECT_RETRIES 次；仍失败的分片剔除降级运行
        （其初始桌丢失，日志告警）。全部分片失败才抛 LoginError。
        """
        groups: dict[str, list[MultiTableSession]] = {}
        for shard in self._shards:
            # NOTE: 跨对象私有访问,待 P4 收敛
            key = shard._conn._session.get("proxy") or ""   # 空=直连组
            groups.setdefault(key, []).append(shard)
        nested = await asyncio.gather(
            *(self._connect_group(g) for g in groups.values()))
        live = [s for g in nested for s in g]
        if not live:
            raise LoginError("TableMonitor: 所有分片连接均失败")
        if len(live) < len(self._shards):
            logger.warning(
                f"[TableMonitor] {len(self._shards) - len(live)} 个分片"
                f"被剔除，以 {len(live)} 个分片降级运行")
        self._shards = live
        return self

    async def _connect_group(self,
                             shards: list[MultiTableSession]
                             ) -> list[MultiTableSession]:
        """同一出口的分片串行建连：限速 + 重试 + 失败剔除。"""
        live: list[MultiTableSession] = []
        for i, shard in enumerate(shards):
            if i:
                await asyncio.sleep(self._connect_interval_s)
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
                # NOTE: 跨对象私有访问,待 P4 收敛
                logger.warning(
                    f"[TableMonitor] 分片连接失败已剔除: {err}"
                    f"（损失 {len(shard._tables)} 张初始桌）")
                try:                        # 可能半连接，兜底关闭防泄漏
                    await shard.__aexit__(None, None, None)
                except Exception:
                    pass
        return live

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
        # NOTE: 跨对象私有访问,待 P4 收敛
        return [t["table_id"] for s in self._shards for t in s._tables]

    # ── 动态控制 ──

    async def add_table(self, table: dict, urgent: bool = False):
        """动态加入一张桌（分配到负载最小的分片，经该分片节奏器限速进桌）。

        urgent=True（失效接管/断线重进类）插队到目标分片队头，
        仍受该账号最小进桌间隔约束。
        """
        # NOTE: 跨对象私有访问,待 P4 收敛
        shard = min(self._shards, key=lambda s: len(s._tables))
        t = {"table_id": int(table["table_id"]),
             "game_type_id": int(table.get("game_type_id", 2001))}
        shard._tables.append(t)
        shard._sync_expect_traffic()
        await shard.request_enter(t, urgent=urgent)

    async def leave_table(self, table_id: int):
        """主动退出某桌（其他桌不受影响）。"""
        for shard in self._shards:
            # NOTE: 跨对象私有访问,待 P4 收敛
            t = next((x for x in shard._tables
                      if x["table_id"] == table_id), None)
            if t:
                await shard._leave_one(t)
                shard._tables.remove(t)
                shard._sync_expect_traffic()
                shard.snapshots.pop(table_id, None)
                shard._road_accum.pop(table_id, None)
                return

    # ── 事件流 ──

    async def _rotate_table(self, src: MultiTableSession, ev: dict):
        """rotate 策略：把被踢的桌换到另一个账号分片重进（原地改事件）。

        目标分片 = 源分片以外负载最小者；仅一个存活分片时退回源分片
        原账号重进（action="auto_reenter"，保监控连续性）。重进指令
        经目标分片节奏器 **urgent 插队**发送（入队成功即标记
        rotated/auto_reenter）；排队后的发送失败由节奏器重试一次再
        丢弃告警（桌留目标分片簿记）。仅节奏器未启动的降级直发路径
        会同步失败：撤回该桌、事件标记 rotate_failed 交调用方收场。
        """
        t = ev["data"].get("table")
        if not t:
            return
        others = [s for s in self._shards if s is not src]
        # NOTE: 跨对象私有访问,待 P4 收敛
        target = min(others, key=lambda s: len(s._tables)) if others else src
        nt = {"table_id": int(t["table_id"]),
              "game_type_id": int(t.get("game_type_id", 2001))}
        target._tables.append(nt)
        target._sync_expect_traffic()
        try:
            await target.request_enter(nt, urgent=True)
        except Exception as e:
            target._tables.remove(nt)
            target._sync_expect_traffic()
            logger.warning(f"[TableMonitor] 轮转重进失败 桌{nt['table_id']}"
                           f" → {target.account}: {e}")
            ev["data"].update({"action": "rotate_failed",
                               "from_account": ev["data"].get("account"),
                               "to_account": target.account})
            return
        rotated = target is not src
        ev["data"].update({
            "action": "rotated" if rotated else "auto_reenter",
            "dropped": False,
            "from_account": ev["data"].get("account"),
            "to_account": target.account,
            **({} if rotated else {"note": "仅一个存活分片，原账号重进"})})
        logger.info(f"[轮换] 桌{nt['table_id']} 被踢："
                    f"{ev['data']['from_account']} 出局，"
                    f"{target.account} 接替进桌（{ev['data']['action']}）")

    async def events(self) -> AsyncIterator[dict]:
        """全部分片合并的统一事件流。

        每个分片一个转发任务汇入队列；aclose() 后迭代自然结束。
        kick_policy="rotate" 时，被踢桌在此换账号分片重进，
        事件 data.action 标记 rotated / auto_reenter / rotate_failed。
        """
        queue: asyncio.Queue = asyncio.Queue()
        remaining = len(self._shards)       # 仍在运行的分片转发任务数

        async def pump(shard: MultiTableSession):
            nonlocal remaining
            try:
                async for ev in shard.events():
                    if self._rotate and ev.get("type") == "kick" \
                            and ev.get("data", {}).get("dropped"):
                        await self._rotate_table(shard, ev)
                    await queue.put(ev)
                    if self._closed:
                        return
            except Exception as e:
                # 分片级故障只上报本分片（data.account 标识来源），
                # 不再终止整体事件流；由调用方决定重建单片还是全部。
                await queue.put({"type": "error", "protocol_id": 0,
                                 "table_id": 0,
                                 "data": {"error": str(e),
                                          "account": shard.account}})
            finally:
                # 注意：这里绝不能是"任一 pump 结束就置全局完成标志"
                # （旧 done.set() 的 bug）——单片掉线会把整个合并流
                # 掐断，迫使调用方全量重建（streak 批量掉线事故根因）。
                remaining -= 1

        tasks = [asyncio.create_task(pump(s)) for s in self._shards]
        try:
            while not self._closed:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    # 全部分片都结束且队列排空才收尾；单片结束不影响其他片
                    if self._closed or (remaining <= 0 and queue.empty()):
                        break
                    continue
                yield ev
        finally:
            for task in tasks:
                task.cancel()
# ── 多台模式（INTER_MULTIPLE=301）全桌订阅会话 ──


class MultiplaySession:
    """多台模式（301, serviceTypeId=2）全桌台订阅会话。

    官方客户端"多台下注"通道：单条 WS 发一次 301 订阅后，服务器持续
    推送全平台桌台的实时牌局帧（103 新靴 / 104 新局 / 106 发牌 /
    107 结算 / 160 状态 / 161 路纸），载荷均为明文 JSON（codecFlag
    为 false 的协议）。2026-08-02 实测：单账号同时 170+ 桌有实时
    数据流，无单桌模式（101/401, Ot.GAME）的"每账号 ~2 桌实时流"
    配额与 5 局未下注踢出（102）。

    events() 产出与 TableSession 同构的事件 dict。迭代因连接断开
    而结束时自然返回（上层负责重建会话）；10026 会话被踢抛
    LoginError。
    """

    # 桌台数据帧协议（判活只看这些——pid=3 心跳/10005 等控制帧活着
    # 不代表数据流活着，2026-08-02 断供事故：服务器切了订阅但连接
    # 心跳照走，任何"有帧即活"的看门狗都会失效）
    _DATA_PIDS = frozenset({103, 104, 106, 107, 160, 161})
    # 服务器控制协议（$9 枚举）：4 强制重连 / 6 多台重连 / 11 服务器限流
    _CONTROL_PIDS = frozenset({4, 5, 6, 10, 11})

    def __init__(self, session: dict, group_id: int = 32,
                 on_before_connect=None):
        self._session = session
        self._group_id = group_id
        self._conn = _WSConnection(session,
                                   on_before_connect=on_before_connect)
        self._closed = False
        self._last_data = 0.0     # monotonic：最近数据帧时刻（判活依据）
        self._clock_checked = False   # 时钟校对是否已告警过（每会话一次）

    @property
    def last_data(self) -> float:
        """最近一条桌台数据帧（103/104/106/107/160/161）的 monotonic
        时刻；0 表示尚未收到。心跳等非数据帧不计。"""
        return self._last_data

    async def __aenter__(self) -> MultiplaySession:
        await self._conn.__aenter__()
        await self._conn.send(build_message(
            _QS_INTER_MULTIPLE,
            {"groupId": self._group_id, "sort": 0, "gameTypeIds": []},
            # NOTE: 跨对象私有访问,待 P4 收敛
            player_id=self._conn._player_id, game_type_id=_IT_MULTIPLAY,
            service_type_id=_OT_MULTIPLE))
        return self

    async def __aexit__(self, *exc):
        self._closed = True
        await self._conn.__aexit__(*exc)

    async def resubscribe(self):
        """在同一连接上重发 301 订阅（预防性重订阅）。

        背景（2026-08-02 实测）：服务器对多台订阅会话有固定 ~8 分钟
        TTL，到期**静默停推数据帧**（无 4/6/11 控制帧、心跳照走），
        官方客户端在浏览器里表现为"多台模式自动退出"。官方客户端只有
        重连后才重发 301，但协议本身允许同连接重复订阅——在 TTL 到期
        前主动重发 301，若服务器重置订阅时钟即可实现零缺口续订。
        """
        await self._conn.send(build_message(
            _QS_INTER_MULTIPLE,
            {"groupId": self._group_id, "sort": 0, "gameTypeIds": []},
            # NOTE: 跨对象私有访问,待 P4 收敛
            player_id=self._conn._player_id, game_type_id=_IT_MULTIPLAY,
            service_type_id=_OT_MULTIPLE))

    async def subscribe_lobby(self):
        """在同一连接上追加大厅订阅（10027），接收 10052 桌台增量
        （在线人数/桌状态/路纸摘要），用于补充多台帧缺失的桌台元数据。"""
        # NOTE: 跨对象私有访问,待 P4 收敛
        await self._conn.send(build_hall_switch_msg(self._conn._player_id,
                                                    self._conn.device_id))

    async def events(self) -> AsyncIterator[dict]:
        """持续产出多台牌局事件（异步迭代器）。

        每个事件:
          {
            "type": str,         # boot / round / card / road / status / lobby / other
            "protocol_id": int,
            "table_id": int|None,
            "data": dict,
          }
        """
        while True:
            try:
                frame = await self._conn.recv()
            except Exception:
                return
            if not frame:
                continue
            pid = frame.get("protocolId")
            if pid == 10026:
                raise LoginError("会话被踢（token 失效），请重新 login()")
            info = extract_param(frame) or {}
            payload = info.get("param") or info.get("data")
            if isinstance(payload, str):
                if frame.get("codecFlag"):
                    try:
                        payload = schema_decode(
                            f"{pid}_{frame.get('serviceTypeId', _OT_MULTIPLE)}",
                            payload)
                    except Exception:
                        pass
                else:
                    try:
                        payload = _json_loads(payload)
                    except Exception:
                        pass
            if not isinstance(payload, dict):
                # 控制帧（4/6/11 等）即使无 dict 载荷也要上抛：
                # RECONNECT_MULTI=6 是服务器要求多台重订阅的信号，
                # 官方客户端收到即重连重发 301；忽略=订阅被静默切断
                if pid in self._CONTROL_PIDS:
                    yield {"type": "control", "protocol_id": pid,
                           "table_id": None, "data": {}}
                continue
            if pid in self._DATA_PIDS:
                self._last_data = time.monotonic()
            # 104(新局)帧带服务器时间戳：每会话首条 104 校对一次时钟偏差。
            # 2026-08-06 事故：客户机比服务器慢 100s+，倒计时换算失真。
            # 偏差>30s 打 WARNING 报差值；>5min 打 ERROR(可能致严重失真)。
            if pid == 104 and not self._clock_checked \
                    and payload.get("serverTime"):
                self._clock_checked = True
                diff = compute_offset(payload["serverTime"])
                sev, _ = check_offset(payload["serverTime"])
                if sev == "error":
                    logger.error(
                        "服务器时间校对失败：本地与服务器偏差过大(%s)，"
                        "倒计时换算可能严重失真（建议同步本地时钟）",
                        format_diff(diff))
                elif sev == "warn":
                    logger.warning(
                        "服务器时间校对偏差 %s（建议同步本地时钟）",
                        format_diff(diff))
            if pid in self._CONTROL_PIDS:
                yield {"type": "control", "protocol_id": pid,
                       "table_id": None, "data": payload}
                continue
            tid = payload.get("tableId")
            yield {
                "type": _classify_event(pid),
                "protocol_id": pid,
                "table_id": tid if isinstance(tid, int) and tid > 0 else None,
                "data": payload,
            }
def _classify_event(protocol_id: int) -> str:
    """协议号 → 事件类型名。"""
    return {
        102: "leave",      # 离桌推送（主动/被踢，leaveTableType 区分）
        103: "boot",       # 新靴/洗牌开始（客户端置 SHUFFLE 清空路纸）
        104: "round",      # 局状态（roundNo/countdown/bootIndex）
        106: "card",       # 发牌
        107: "card",       # 牌局事件
        110: "bet",        # 桌台动态（在线/投注/奖池）
        116: "road",       # 路纸
        123: "notice",     # 系统通知（如连续3局未下注预警 noticeId=21002）
        160: "road",       # 路纸更新
        161: "road",       # 路纸更新
        171: "status",     # 桌台状态
        305: "status",     # 桌台故障状态变更（TABLEFAULT_STATUS_CHANGE）
        10052: "lobby",    # 大厅快照
    }.get(protocol_id, "other")
def _table_info_from_snapshot(table_id: str, t: dict,
                              meta: dict | None = None) -> TableInfo | None:
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
    total_amount = 0.0
    ton = t.get("tableOnline")
    if isinstance(ton, dict):
        online = ton.get("onlineNumber", 0) or 0
        total_amount = ton.get("totalAmount", 0) or 0
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
        total_amount=total_amount,
        boot_no=t.get("bootNo", "") or m.get("bootNo", "") or "",
        road_flat=flat,
        road_count=len(flat),
        good_roads=good_roads,
    )
