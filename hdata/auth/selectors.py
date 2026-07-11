"""CSS 选择器版本管理。

首次登录成功后自动保存当前页面的选择器快照。
后续登录失败（弹窗未出现等）时自动尝试更新选择器。

用法:
    from hdata.auth.selectors import SelectorSnapshot

    snap = SelectorSnapshot.load_or_default()
    btn = snap.login_button  # 或内置默认值
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_SNAPSHOT_PATH = _CACHE_DIR / "selectors.json"

# 内置默认值（网站不改版时有效）
BUILTIN = {
    "login_button": "span.sV0BIdNgkCghFjH6HXzUFg__",
    "username_input": "input:not([type=password])",
    "password_input": "input[type=password]",
    "captcha_popup": '[class*="botion_click"]',
    "captcha_bg": '[class*="botion_bg"]',
    "captcha_bg_img_filter": "captcha_v4",
}


class SelectorSnapshot:
    """选择器快照。"""

    def __init__(self, data: dict):
        self.login_button = data.get("login_button", BUILTIN["login_button"])
        self.username_input = data.get("username_input", BUILTIN["username_input"])
        self.password_input = data.get("password_input", BUILTIN["password_input"])
        self.captcha_popup = data.get("captcha_popup", BUILTIN["captcha_popup"])
        self.captcha_bg = data.get("captcha_bg", BUILTIN["captcha_bg"])
        self.captcha_bg_img_filter = data.get("captcha_bg_img_filter", BUILTIN["captcha_bg_img_filter"])
        self._raw = data

    @classmethod
    def load_or_default(cls):
        """加载快照，不存在则用内置默认值。"""
        if _SNAPSHOT_PATH.exists():
            try:
                return cls(json.loads(_SNAPSHOT_PATH.read_text()))
            except Exception:
                _SNAPSHOT_PATH.unlink(missing_ok=True)
        return cls({})

    def update_from_dom(self, page_eval):
        """从当前页面 DOM 提取最新选择器并保存。

        page_eval: async callable(js) -> result — 可以是 Playwright page.evaluate
                   或 raw CDP Runtime.evaluate 的封装
        """
        # 这里只是接口定义，实际调用在 headless_login 中
        pass

    def save(self, domain: str = ""):
        """保存快照到磁盘。"""
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_PATH.write_text(json.dumps({
            "domain": domain,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "login_button": self.login_button,
            "username_input": self.username_input,
            "password_input": self.password_input,
            "captcha_popup": self.captcha_popup,
            "captcha_bg": self.captcha_bg,
            "captcha_bg_img_filter": self.captcha_bg_img_filter,
        }, indent=2, ensure_ascii=False))

    @staticmethod
    def path() -> Path:
        return _SNAPSHOT_PATH
