"""验证码识别器抽象接口 + JfbymSolver 实现。

设计原则:
  - TokenManager 只依赖 CaptchaSolver 接口，不关心具体打码平台
  - 新增平台只需实现 solve() + info() 两个方法
  - jfbym 挂了直接抛 CaptchaSolveError，不做自动降级（换平台行为完全不同）

用法:
    solver = JfbymSolver(api_token="xxx")
    solution = await solver.solve(challenge)
    # solution.coords = "74,124|235,132|176,65"
    # solution.pts = [[74,124],[235,132],[176,65]]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════
# 数据类型
# ═══════════════════════════════════════════════════════════


@dataclass
class CaptchaChallenge:
    """GeeTest 验证码原始数据 — 由 fetch_captcha() 产生。

    Attributes:
        lot_number: 挑战批次 ID（GeeTest load API 返回）
        payload: 加密的挑战数据
        process_token: 服务端会话 token
        bg_url: 验证码背景图 URL（300x200 JPG，江城正君体中文字符）
        ques_urls: 3 张参考字图 URL（64x65 RGBA PNG）
        captcha_id: Botion 验证码 ID（固定值）
        pow_detail: Proof-of-Work 参数（hashfunc, version, bits, datetime）
        pt: 协议版本号
        payload_protocol: payload 协议版本
    """

    lot_number: str
    payload: str
    process_token: str
    bg_url: str
    ques_urls: list[str]
    captcha_id: str
    pow_detail: dict = field(default_factory=dict)
    pt: str = "1"
    payload_protocol: str = "1"


@dataclass
class CaptchaSolution:
    """打码平台返回的坐标识别结果。

    Attributes:
        coords: 坐标字符串 "x1,y1|x2,y2|x3,y3"（GeeTest 格式）
        pts: 二维坐标数组 [[x1,y1],[x2,y2],[x3,y3]]
        raw_response: 平台原始响应（保留用于调试）
        latency_ms: 实际耗时（毫秒）
    """

    coords: str
    pts: list[list[int]]
    raw_response: dict = field(default_factory=dict)
    latency_ms: float = 0.0


@dataclass
class SolverInfo:
    """打码平台元信息（调试/监控用）。"""

    name: str  # "jfbym"
    type_code: str  # "31111"
    avg_latency_ms: int  # 典型耗时


# ═══════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════


class CaptchaSolveError(Exception):
    """验证码识别失败。

    包含失败原因和平台名，方便上层做日志/告警。
    """

    def __init__(self, solver_name: str, reason: str, raw_error: str = ""):
        self.solver_name = solver_name
        self.reason = reason
        self.raw_error = raw_error
        super().__init__(f"[{solver_name}] {reason}" + (f": {raw_error}" if raw_error else ""))


# ═══════════════════════════════════════════════════════════
# 抽象接口
# ═══════════════════════════════════════════════════════════


class CaptchaSolver(ABC):
    """验证码识别器抽象 — 新增打码平台只需实现此接口。

    子类必须实现:
      - solve(challenge) -> CaptchaSolution
      - info() -> SolverInfo

    solve() 失败应抛出 CaptchaSolveError，不要返回 None。
    """

    @abstractmethod
    async def solve(self, challenge: CaptchaChallenge) -> CaptchaSolution:
        """识别验证码，返回点击坐标。

        Args:
            challenge: 从 GeeTest load API 获取的验证码数据

        Returns:
            CaptchaSolution 包含坐标信息

        Raises:
            CaptchaSolveError: 识别失败（网络错误、余额不足、无结果等）
        """
        ...

    @abstractmethod
    def info(self) -> SolverInfo:
        """返回平台元信息（用于日志/监控）。"""
        ...


# ═══════════════════════════════════════════════════════════
# JfbymSolver — 首个实现
# ═══════════════════════════════════════════════════════════


class JfbymSolver(CaptchaSolver):
    """jfbym 打码平台 — GeeTest v4 文字点选识别。

    使用 jfbym API type=31111 "je4_click" 一键识别：
    传入背景图 + 3 张参考字图，直接返回点击坐标。

    需要 jfbym API token（从 jfbym.com 获取）。

    Usage:
        solver = JfbymSolver(api_token="xxx")
        solution = await solver.solve(challenge)
    """

    DEFAULT_API_URL = "http://api.jfbym.com/api/YmServer/customApi"
    USER_INFO_URL = "http://api.jfbym.com/api/YmServer/getUserInfoApi"
    TYPE_CODE = "31111"
    EXTRA = "je4_click"

    def __init__(self, api_token: str, api_url: str = ""):
        if not api_token:
            raise ValueError("jfbym API token 不能为空")
        self._token = api_token
        self._url = api_url or self.DEFAULT_API_URL

    def info(self) -> SolverInfo:
        return SolverInfo(name="jfbym", type_code=self.TYPE_CODE, avg_latency_ms=3000)

    def get_balance(self) -> str | None:
        """查询 jfbym 账户余额积分。返回字符串金额，失败返回 None。"""
        from curl_cffi import requests as cr
        try:
            r = cr.post(self.USER_INFO_URL,
                        json={"token": self._token, "type": "score"},
                        headers={"Content-Type": "application/json"},
                        timeout=10)
            data = r.json()
            if data.get("code") == 10001:
                return (data.get("data", {}) or {}).get("score", "")
        except Exception:
            pass
        return None

    async def solve(self, challenge: CaptchaChallenge) -> CaptchaSolution:
        """jfbym 31111 一键识别 — 传背景图 + 3 张参考字图，返回坐标。"""
        import asyncio
        import base64
        import json
        import time
        from curl_cffi import requests as cr

        t0 = time.time()

        # 1. 下载背景图
        bg_b64 = base64.b64encode(
            cr.get(challenge.bg_url, impersonate="chrome110", timeout=15).content
        ).decode()

        # 2. 构建请求体
        body = {
            "token": self._token,
            "type": self.TYPE_CODE,
            "image": bg_b64,
            "extra": self.EXTRA,
        }
        for i, url in enumerate(challenge.ques_urls):
            ref_b64 = base64.b64encode(
                cr.get(url, impersonate="chrome110", timeout=10).content
            ).decode()
            body[f"image_label{i + 1}"] = ref_b64

        # 3. 调用 jfbym（最多 6 次重试）
        last_error = ""
        for attempt in range(6):
            try:
                r = cr.post(
                    self._url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=60,
                ).json()
            except Exception as e:
                last_error = f"网络错误(attempt {attempt + 1}): {e}"
                await asyncio.sleep(2)
                continue

            code = r.get("code")
            if code == 10000:
                data = r.get("data", {}) or {}
                coords = data.get("data", "")
                if not coords:
                    last_error = f"jfbym 返回空坐标: {r}"
                    continue

                # 解析坐标
                pts = [
                    [int(p.split(",")[0]), int(p.split(",")[1])]
                    for p in coords.split("|")
                ]

                return CaptchaSolution(
                    coords=coords,
                    pts=pts,
                    raw_response=r,
                    latency_ms=(time.time() - t0) * 1000,
                )

            elif code == 10009:
                # 平台繁忙，等待后重试
                await asyncio.sleep(3)
                continue
            else:
                last_error = f"jfbym code={code}: {r.get('msg', str(r)[:200])}"
                break

        raise CaptchaSolveError("jfbym", "solve 失败 (6 次重试用尽)", last_error)
