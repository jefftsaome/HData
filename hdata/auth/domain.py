"""集团入口站 → 真实域名解析。

同一个集团多个入口站（leyu.com、leyu.me 等），HTML 中 JS 映射表格式:
    mappings.set("入口域名", "真实域名URL");

用 Python urllib 访问入口站（TLS 栈可过，curl_cffi 指纹被 ban），
正则提取真实域名，按入口 key 缓存到 .cache/domain.json。

用法:
    from hdata.auth.domain import resolve_domain, DomainCache

    domain = resolve_domain("https://leyu.com")   # 从指定入口获取
    domain = resolve_domain()                      # 默认 leyu.com

    cache = DomainCache()
    cache.get("leyu.com")  # 只读缓存
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"

DEFAULT_ENTRIES = [
    "https://leyu.com",
    "https://leyu.me",
    "https://hth.com",
]


class DomainCache:
    """域名缓存 — 按入口 key 存取，带 TTL。

    域名是动态资源（可能小时级失效），缓存只作短期加速：
    超过 TTL 或目标域名不可用时必须重新从入口站解析。
    """

    DEFAULT_TTL = 30 * 60  # 30 分钟

    def __init__(self, ttl: int | None = None):
        self._path = _CACHE_DIR / "domain.json"
        self._ttl = ttl if ttl is not None else self.DEFAULT_TTL

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}

    def get(self, entry_url: str = "") -> str | None:
        """读取缓存的域名（未过 TTL 才返回）。不联网。"""
        data = self._load()
        if not data:
            return None
        # TTL 检查：过期视为无缓存
        updated_at = data.get("updated_at", 0)
        if updated_at and int(time.time()) - updated_at > self._ttl:
            return None
        key = _cache_key(entry_url) if entry_url else ""
        # 新格式: {"leyu.com": "https://..."}
        if key and key in data:
            return data[key]
        # 旧格式兼容: {"domain": "https://..."}
        if "domain" in data:
            return data["domain"]
        # 取第一个非 meta 的 value
        for k, v in data.items():
            if k not in ("updated_at",) and v:
                return v
        return None

    def set(self, entry_url: str, domain: str):
        """写入缓存。"""
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = self._load()
        key = _cache_key(entry_url)
        data[key] = domain
        data["updated_at"] = int(time.time())
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def invalidate(self, entry_url: str = ""):
        """使缓存失效（域名不可用时调用，强制下次重新解析）。"""
        data = self._load()
        if not data:
            return
        if entry_url:
            data.pop(_cache_key(entry_url), None)
        else:
            # 清空所有域名，保留结构
            data = {}
        data["updated_at"] = 0
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def list_all(self) -> dict[str, str]:
        """返回所有缓存的 key → domain 映射。"""
        return {k: v for k, v in self._load().items()
                if k not in ("updated_at",) and v}


def _cache_key(entry_url: str) -> str:
    """从入口 URL 提取缓存 key，如 'leyu.com'。"""
    if not entry_url:
        entry_url = DEFAULT_ENTRIES[0]
    return urlparse(entry_url).netloc.replace("www.", "")


def probe_domain(domain: str, timeout: float = 6.0) -> bool:
    """探活一个真实域名（TCP+HTTP 层面）。

    请求首页，任何 HTTP 响应（含 301/302/403）都视为"域名活着"；
    连接失败/超时/重置视为不可用。自签证书不影响判定。
    """
    import ssl

    ctx = ssl._create_unverified_context()
    try:
        req = Request(domain, headers={"User-Agent": "Mozilla/5.0"})
        r = urlopen(req, timeout=timeout, context=ctx)
        return r.status < 500
    except Exception as e:
        # HTTPError 也是"活着"（服务器有响应）
        if hasattr(e, "code"):
            return getattr(e, "code", 500) < 500
        return False


def resolve_domain(entry_url: str = "", *, validate: bool = False) -> str | None:
    """从集团入口站获取真实域名。

    入口站 HTML 里有 JS 映射表:
        mappings.set("入口域名", "真实域名URL");
    从 HTML 中提取真实域名 URL，按入口 key 缓存（带 TTL）。

    域名是动态资源（可能小时级轮换）。调用方在 API 请求出现
    连接级失败时，应 DomainCache().invalidate() 后重新解析。

    Args:
        entry_url: 入口站 URL，如 "https://leyu.com"。
                   为空时尝试所有默认入口。
        validate:  True 时对缓存/解析结果先探活，死了自动重解析。

    Returns:
        真实域名 str，如 "https://www.5ttn8v.vip:9037"；失败返回 None。
    """
    cache = DomainCache()

    entries = [entry_url] if entry_url else DEFAULT_ENTRIES

    for url in entries:
        if not url.startswith("http"):
            url = f"https://{url}"

        # 1. 缓存命中（可选探活）
        cached = cache.get(url)
        if cached:
            if not validate or probe_domain(cached):
                return cached
            cache.invalidate(url)

        # 2. 从入口站提取（支持两种格式，自动跟随 code.js）
        domain = _extract_from_entry(url)
        if domain:
            cache.set(url, domain)
            return domain

    return None


def _fetch(url: str, timeout: float = 10.0) -> str | None:
    """拉取文本内容；入口站证书可能是自签的，失败时降级跳过证书校验。"""
    import ssl

    for ctx in (None, ssl._create_unverified_context()):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = urlopen(req, timeout=timeout, context=ctx) if ctx else urlopen(req, timeout=timeout)
            if r.status == 200:
                return r.read().decode(errors="replace")
        except Exception:
            continue
    return None


def _parse_domain(text: str) -> str | None:
    """从文本中解析真实域名 URL，支持已知三种格式。"""
    # 格式 1: mappings.set("入口", "https://www.xxx.vip:端口")
    m = re.search(r'mappings\.set\(".+?",\s*"(https://[^"]+)"', text)
    if m:
        return m.group(1)
    # 格式 2 (leyu 系 code.js): lypcurls = 'https://www.xxx.vip:端口'; //PC
    m = re.search(r"lypcurls\s*=\s*'(https://[^']+)'", text)
    if m:
        return m.group(1)
    # 格式 3 兜底：任意 https://www.*.vip:端口
    m = re.search(r"https://www\.[^\"']+\.vip:\d+", text)
    if m:
        return m.group(0)
    return None


def _extract_from_entry(entry_url: str) -> str | None:
    """从入口站提取真实域名。

    入口首页通常只是引导页（document.write 加载 /code.js），
    真实映射在 /code.js 里；两种位置都尝试。
    """
    html = _fetch(entry_url)
    if html:
        domain = _parse_domain(html)
        if domain:
            return domain
    # 首页没命中 → 拉 /code.js（leyu 系入口的真实映射位置）
    js = _fetch(entry_url.rstrip("/") + "/code.js?v=0.1")
    if js:
        return _parse_domain(js)
    return None
