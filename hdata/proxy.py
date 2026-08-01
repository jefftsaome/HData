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
    await pool.health_check()                 # 剔除死代理，记录各出口实测 IP
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
import re
from pathlib import Path

from htools.utils.logger import get_logger

logger = get_logger(__name__)

# 默认每出口连接预算：实测安全工作点（3s 间隔 10 条并发全成功，
# 硬上限未知，见 平台边界试探.md §2.2）；探针压测前不建议调大
DEFAULT_CAP_PER_PROXY = 10

_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def _extract_ip(text: str) -> str | None:
    """从 echo 服务返回体里抠第一个 IPv4 地址。"""
    m = _IPV4_RE.search(text or "")
    return m.group(1) if m else None


async def probe_exit_connect(proxy: str, dest_host: str, dest_port: int,
                             timeout: float = 12.0) -> bool:
    """SOCKS5 建连探测：通过出口实际 TCP 连接目标站点，通 = 存活。

    比 HTTP echo 探测更贴近真实用途（echo 通不代表能到平台），且
    单点单测 ~1s，适合并行全量预检。python-socks 不认 socks5h
    scheme（h=域名在代理侧解析），这里统一降级为 socks5（本地解析，
    对"域名绑 IP"的出口形式等价）。
    """
    from python_socks.async_.asyncio import Proxy
    try:
        p = Proxy.from_url(proxy.replace("socks5h://", "socks5://"))
        sock = await asyncio.wait_for(
            p.connect(dest_host=dest_host, dest_port=dest_port), timeout)
        sock.close()
        return True
    except Exception:
        return False


def _probe_sync(proxy: str, timeout: float) -> tuple[bool, str | None]:
    """HTTP 出口探测（同步，供 asyncio.to_thread 包装）。

    依次尝试多个 echo 服务，任一返回 200 即视为存活；并从返回体
    解析真实出口 IP（myip.ipip.net 为含 IP 文本，httpbin.org/ip 为
    JSON {"origin": ...}）。解析不到 IP 但 200 的算存活、IP 记 None。

    Returns:
        (存活, 出口IP|None)
    """
    from curl_cffi import requests
    endpoints = ["https://myip.ipip.net", "http://httpbin.org/ip"]
    for url in endpoints:
        try:
            r = requests.get(
                url, timeout=timeout,
                proxies={"http": proxy, "https": proxy})
            if r.status_code != 200:
                continue
            ip: str | None = None
            if "httpbin" in url:
                try:
                    ip = _extract_ip(str(r.json().get("origin", "")))
                except Exception:
                    ip = None
            else:
                ip = _extract_ip(r.text)
            return True, ip
        except Exception:
            continue
    return False, None


class ProxyPool:
    """出口代理池（粘性分配 + 容量预算 + 失败摘除）。"""

    def __init__(self, proxies: list[str],
                 cap_per_proxy: int = DEFAULT_CAP_PER_PROXY,
                 ids: dict[str, str] | None = None):
        if cap_per_proxy < 1:
            raise ValueError("cap_per_proxy 必须 >= 1")
        # 去重保序
        self._proxies = list(dict.fromkeys(p for p in proxies if p))
        self._cap = cap_per_proxy
        self._dead: set[str] = set()
        self._bindings: dict[str, str] = {}      # account -> proxy
        self._exit_ips: dict[str, str | None] = {}  # proxy -> 实测出口IP
        self._ids: dict[str, str] = dict(ids or {})  # 出口 id -> proxy url

    # ── 加载 ──────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | Path,
                  cap_per_proxy: int = DEFAULT_CAP_PER_PROXY
                  ) -> "ProxyPool":
        """从 JSON 文件加载代理列表。

        支持两种元素形式:
          ["http://user:pass@host:port", ...]
          [{"id": "exit-1", "name": "xxx", "url": "http://..."}, ...]
          （id 为稳定出口标识，账号 proxy_id 绑定它；name 仅展示用；
          id 缺失时退化为该条目在列表中的序号 "exit-{i+1}"）
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"{path}: 代理文件必须是 JSON 数组")
        urls: list[str] = []
        ids: dict[str, str] = {}
        for i, item in enumerate(data):
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict) and item.get("url"):
                url = str(item["url"])
                urls.append(url)
                pid = str(item.get("id") or f"exit-{i + 1}")
                ids[pid] = url
            else:
                raise ValueError(f"{path}: 第 {i + 1} 项无法解析为代理 URL")
        return cls(urls, cap_per_proxy=cap_per_proxy, ids=ids)

    # ── 状态 ──────────────────────────────────────────

    @property
    def alive(self) -> list[str]:
        """存活出口列表（保序）。"""
        return [p for p in self._proxies if p not in self._dead]

    @property
    def proxies(self) -> list[str]:
        """全部出口列表（保序，含已剔除的）。"""
        return list(self._proxies)

    @property
    def cap_per_proxy(self) -> int:
        return self._cap

    @property
    def exit_ips(self) -> dict[str, str | None]:
        """各出口最近一次健康检查实测的出口 IP（未探测过的不在列）。"""
        return dict(self._exit_ips)

    def exit_ip(self, proxy: str) -> str | None:
        """单个出口的实测 IP（未探测 / 探测失败 / 解析不到均为 None）。"""
        return self._exit_ips.get(proxy)

    def url_for_id(self, proxy_id: str) -> str | None:
        """出口 id → URL（未知 id 返回 None）。"""
        return self._ids.get(proxy_id)

    def id_of(self, proxy: str) -> str | None:
        """URL → 出口 id（无 id 配置的出口返回 None）。"""
        for pid, url in self._ids.items():
            if url == proxy:
                return pid
        return None

    def _load(self, proxy: str) -> int:
        return sum(1 for p in self._bindings.values() if p == proxy)

    # ── 分配 ──────────────────────────────────────────

    def assign(self, accounts: list[str],
               preferred_ids: dict[str, str] | None = None
               ) -> dict[str, str | None]:
        """粘性均衡分配账号到出口。

        - preferred_ids：{account: 出口 id} 显式绑定（config.json 的
          proxy_id）。显式绑定最优先：出口存活即绑定之（不受 cap 限制，
          超 cap 打 warning）；出口已死则映射 None + warning，
          **不静默迁移到其他出口**（避免账号在出口间乱跳）；
        - 已有绑定的账号保持不变（粘性，出口仍存活时）；
        - 新账号分给当前绑定数最少且未满预算的存活出口；
        - 总容量不足时多出的账号映射为 None（调用方告警/弃用）。

        Returns:
            {account: proxy_url | None}
        """
        result: dict[str, str | None] = {}
        preferred_ids = preferred_ids or {}
        for acc in accounts:
            pid = preferred_ids.get(acc)
            if pid:
                url = self._ids.get(pid)
                if url is None:
                    logger.warning(f"[ProxyPool] {acc} 绑定的出口 id "
                                   f"'{pid}' 在代理文件中不存在")
                    result[acc] = None
                    continue
                if url not in self.alive:
                    logger.warning(f"[ProxyPool] {acc} 绑定的出口 "
                                   f"'{pid}' 探测已死，不自动迁移，"
                                   "等代理文件更新 IP 后复活")
                    result[acc] = None
                    continue
                self._bindings[acc] = url
                result[acc] = url
                if self._load(url) > self._cap:
                    logger.warning(f"[ProxyPool] 出口 '{pid}' 显式绑定 "
                                   f"{self._load(url)} 个账号，超 cap "
                                   f"{self._cap}（显式绑定不受限，注意密度）")
                continue
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
        self._exit_ips[proxy] = None
        affected = [a for a, p in self._bindings.items() if p == proxy]
        for a in affected:
            del self._bindings[a]
        if affected:
            logger.warning(f"[ProxyPool] 出口失效，{len(affected)} 个账号"
                           f"待换绑: {affected}")
        return affected

    # ── 健康检查 ──────────────────────────────────────

    async def health_check(self, timeout: float = 10.0,
                           probe=None,
                           connect_dest: tuple[str, int] | None = None,
                           retry: int = 1) -> dict[str, dict]:
        """并行探测全部出口存活并记录实测 IP，失败出口自动 mark_dead。

        Args:
            timeout: 单出口单次探测超时（秒）
            probe: 可注入的探测函数 (proxy, timeout) -> bool（旧式，IP 记
                   None）或 (bool, 出口IP|None)，默认 HTTP echo 探测
            connect_dest: (host, port) 提供时改用 SOCKS5 建连探测
                          （probe_exit_connect，并行、贴真实目标站），
                          IP 记 None
            retry: 失败重试次数（总尝试 = retry；仅 connect 模式）

        Returns:
            {proxy: {"ok": bool, "ip": 出口IP|None}}——死出口 ip 恒 None
        """
        async def one(p: str) -> tuple[bool, str | None]:
            attempts = max(1, retry if connect_dest else 1)
            for i in range(attempts):
                try:
                    if connect_dest:
                        ok = await probe_exit_connect(
                            p, connect_dest[0], connect_dest[1], timeout)
                        if ok:
                            return True, None
                    else:
                        res = await asyncio.to_thread(
                            probe or _probe_sync, p, timeout)
                        if isinstance(res, tuple):
                            if res[0]:
                                return True, (res[1] or None)
                        elif res:
                            return True, None
                except Exception:
                    pass
                if i + 1 < attempts:
                    await asyncio.sleep(2)
            return False, None

        pairs = await asyncio.gather(*[one(p) for p in self._proxies])
        results: dict[str, dict] = {}
        for p, (ok, ip) in zip(self._proxies, pairs):
            results[p] = {"ok": ok, "ip": ip if ok else None}
            self._exit_ips[p] = results[p]["ip"]
            if ok:
                # 复活：之前被判死的出口这次通了（代理商切了 IP 映射）
                if p in self._dead:
                    self._dead.discard(p)
                    logger.info(f"[ProxyPool] 出口复活: {p}")
            else:
                self.mark_dead(p)
                logger.warning(f"[ProxyPool] 出口探测失败已剔除: {p}")
        return results

    def mark_alive(self, proxy: str) -> bool:
        """出口复活（运行期复探测通后调用），返回是否从死集里捞回。"""
        if proxy in self._dead:
            self._dead.discard(proxy)
            logger.info(f"[ProxyPool] 出口复活: {proxy}")
            return True
        return False

    async def probe_dead_revive(self, connect_dest: tuple[str, int],
                                timeout: float = 12.0) -> list[str]:
        """运行期复活巡检：只对死出口做 SOCKS5 建连复探，通的标活并
        返回复活列表（挂起账号由调用方归队）。"""
        revived: list[str] = []
        if not self._dead:
            return revived
        oks = await asyncio.gather(*[
            probe_exit_connect(p, connect_dest[0], connect_dest[1], timeout)
            for p in sorted(self._dead)])
        for p, ok in zip(sorted(self._dead), oks):
            if ok and self.mark_alive(p):
                revived.append(p)
        return revived

    # ── 状态持久化（sidecar，不改代理文件）──────────────

    def save_state(self, path: str | Path):
        """把死出口集合与实测 IP 写 sidecar JSON（下次启动免重复打码
        试探死线；复活信息不持久——以启动 health_check 实测为准）。"""
        import time as _t
        data = {"ts": int(_t.time()),
                "dead": sorted(self._dead),
                "exit_ips": self._exit_ips}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    def load_state(self, path: str | Path):
        """从 sidecar 恢复死出口集合与实测 IP（启动 health_check 之前的
        先验；health_check 实测会覆盖/复活）。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return
        for p in data.get("dead") or []:
            if p in self._proxies:
                self._dead.add(p)
        for p, ip in (data.get("exit_ips") or {}).items():
            if p in self._proxies and p not in self._exit_ips:
                self._exit_ips[p] = ip

    def __repr__(self):
        return (f"ProxyPool(alive={len(self.alive)}/{len(self._proxies)}, "
                f"cap={self._cap}, bindings={len(self._bindings)})")
