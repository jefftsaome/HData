"""代理池：出口代理的粘性分配、健康检查与失败摘除。

设计背景（见 docs/代理接入.md）：
- **token 绑定登录 IP**（对照实验实锤）→ 账号必须与出口粘性绑定，
  登录/刷新/WS 全程同一出口，不可漂移；
- 每个出口一份连接预算（默认 10 条，实测安全工作点，见
  docs/平台边界试探.md §2.2），多账号可共享同一出口；
- 未提供代理时调用方走直连，不经过本模块；
  **提供了代理就只用代理**——本模块不含"本机直连"出口。

使用流程:
    pool = ProxyPool.from_file("data/proxies.json", cap_per_proxy=10)
    await pool.health_check()                 # 剔除死代理
    mapping = pool.assign(["acc_a", "acc_b"]) # 粘性均衡分配
    # 把 mapping[acc] 写进各账号的 cred["proxy"]，之后
    # GameClient(proxy=...) / monitor_tables(accounts) 自动继承

运行中代理失效时:
    affected = pool.mark_dead(proxy)          # 其名下账号解绑
    mapping = pool.assign(affected)           # 重分到存活出口（需经新
                                              # 出口重新登录拿新 token）
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from htools.utils.logger import get_logger

logger = get_logger(__name__)

# 默认每出口连接预算：实测安全工作点（3s 间隔 10 条并发全成功，
# 硬上限未知，见 平台边界试探.md §2.2）；探针压测前不建议调大
DEFAULT_CAP_PER_PROXY = 10


def _probe_sync(proxy: str, timeout: float) -> bool:
    """HTTP 出口探测（同步，供 asyncio.to_thread 包装）。

    依次尝试多个 echo 服务，任一返回 200 即视为存活。
    """
    from curl_cffi import requests
    endpoints = ["https://myip.ipip.net", "http://httpbin.org/ip"]
    for url in endpoints:
        try:
            r = requests.get(
                url, timeout=timeout,
                proxies={"http": proxy, "https": proxy})
            if r.status_code == 200:
                return True
        except Exception:
            continue
    return False


class ProxyPool:
    """出口代理池（粘性分配 + 容量预算 + 失败摘除）。"""

    def __init__(self, proxies: list[str],
                 cap_per_proxy: int = DEFAULT_CAP_PER_PROXY):
        if cap_per_proxy < 1:
            raise ValueError("cap_per_proxy 必须 >= 1")
        # 去重保序
        self._proxies = list(dict.fromkeys(p for p in proxies if p))
        self._cap = cap_per_proxy
        self._dead: set[str] = set()
        self._bindings: dict[str, str] = {}      # account -> proxy

    # ── 加载 ──────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | Path,
                  cap_per_proxy: int = DEFAULT_CAP_PER_PROXY
                  ) -> "ProxyPool":
        """从 JSON 文件加载代理列表。

        支持两种元素形式:
          ["http://user:pass@host:port", ...]
          [{"name": "xxx", "url": "http://..."}, ...]（name 仅展示用）
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"{path}: 代理文件必须是 JSON 数组")
        urls: list[str] = []
        for i, item in enumerate(data):
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
            else:
                raise ValueError(f"{path}: 第 {i + 1} 项无法解析为代理 URL")
        return cls(urls, cap_per_proxy=cap_per_proxy)

    # ── 状态 ──────────────────────────────────────────

    @property
    def alive(self) -> list[str]:
        """存活出口列表（保序）。"""
        return [p for p in self._proxies if p not in self._dead]

    @property
    def cap_per_proxy(self) -> int:
        return self._cap

    def _load(self, proxy: str) -> int:
        return sum(1 for p in self._bindings.values() if p == proxy)

    # ── 分配 ──────────────────────────────────────────

    def assign(self, accounts: list[str]) -> dict[str, str | None]:
        """粘性均衡分配账号到出口。

        - 已有绑定的账号保持不变（粘性，出口仍存活时）；
        - 新账号分给当前绑定数最少且未满预算的存活出口；
        - 总容量不足时多出的账号映射为 None（调用方告警/弃用）。

        Returns:
            {account: proxy_url | None}
        """
        result: dict[str, str | None] = {}
        for acc in accounts:
            bound = self._bindings.get(acc)
            if bound and bound in self.alive:
                result[acc] = bound
                continue
            # 选负载最小且未满的出口
            candidates = [p for p in self.alive if self._load(p) < self._cap]
            if not candidates:
                result[acc] = None
                continue
            pick = min(candidates, key=self._load)
            self._bindings[acc] = pick
            result[acc] = pick
        return result

    # ── 故障处理 ──────────────────────────────────────

    def mark_dead(self, proxy: str) -> list[str]:
        """标记出口死亡，解除其名下账号绑定，返回受影响账号列表。

        受影响账号之后用 `assign(受影响账号)` 重新分配到存活出口
        （账号需经新出口重新登录拿新 token，token 绑 IP）。
        """
        self._dead.add(proxy)
        affected = [a for a, p in self._bindings.items() if p == proxy]
        for a in affected:
            del self._bindings[a]
        if affected:
            logger.warning(f"[ProxyPool] 出口失效，{len(affected)} 个账号"
                           f"待换绑: {affected}")
        return affected

    # ── 健康检查 ──────────────────────────────────────

    async def health_check(self, timeout: float = 10.0,
                           probe=None) -> dict[str, bool]:
        """逐出口探测存活，失败出口自动 mark_dead。

        Args:
            timeout: 单出口探测超时（秒）
            probe: 可注入的探测函数 (proxy, timeout) -> bool，
                   默认 HTTP echo 探测（测试时可替换）

        Returns:
            {proxy: True/False}
        """
        probe = probe or _probe_sync
        results: dict[str, bool] = {}
        for p in self._proxies:
            try:
                ok = await asyncio.to_thread(probe, p, timeout)
            except Exception:
                ok = False
            results[p] = ok
            if not ok:
                self.mark_dead(p)
                logger.warning(f"[ProxyPool] 出口探测失败已剔除: {p}")
        return results

    def __repr__(self):
        return (f"ProxyPool(alive={len(self.alive)}/{len(self._proxies)}, "
                f"cap={self._cap}, bindings={len(self._bindings)})")
