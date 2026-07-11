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
    """域名缓存 — 按入口 key 存取。"""

    def __init__(self):
        self._path = _CACHE_DIR / "domain.json"

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}

    def get(self, entry_url: str = "") -> str | None:
        """读取缓存的域名。不联网。"""
        data = self._load()
        if not data:
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

    def list_all(self) -> dict[str, str]:
        """返回所有缓存的 key → domain 映射。"""
        return {k: v for k, v in self._load().items()
                if k not in ("updated_at",) and v}


def _cache_key(entry_url: str) -> str:
    """从入口 URL 提取缓存 key，如 'leyu.com'。"""
    if not entry_url:
        entry_url = DEFAULT_ENTRIES[0]
    return urlparse(entry_url).netloc.replace("www.", "")


def resolve_domain(entry_url: str = "") -> str | None:
    """从集团入口站获取真实域名。

    入口站 HTML 里有 JS 映射表:
        mappings.set("入口域名", "真实域名URL");
    从 HTML 中提取真实域名 URL，按入口 key 缓存。

    Args:
        entry_url: 入口站 URL，如 "https://leyu.com"。
                   为空时尝试所有默认入口。

    Returns:
        真实域名 str，如 "https://www.5ttn8v.vip:9037"；失败返回 None。
    """
    cache = DomainCache()

    entries = [entry_url] if entry_url else DEFAULT_ENTRIES

    for url in entries:
        if not url.startswith("http"):
            url = f"https://{url}"

        # 1. 缓存命中
        cached = cache.get(url)
        if cached:
            return cached

        # 2. 从入口站 HTML 提取
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = urlopen(req, timeout=10)
            if r.status != 200:
                continue
            html = r.read().decode()
            # 匹配 mappings.set("xxx", "https://www.xxx.vip:端口") 中的真实域名
            m = re.search(
                r'mappings\.set\(".+?",\s*"(https://[^"]+)"',
                html
            )
            if not m:
                # 兜底：宽松匹配任意 https://www.*.vip:端口或类似格式
                m = re.search(r'https://www\.[^"\']+\.vip:\d+', html)
            if m:
                domain = m.group(1) if m.lastindex else m.group(0)
                cache.set(url, domain)
                return domain
        except Exception:
            continue

    return None
