"""Playwright 隐身补丁 — 最小化原则。

参考 playwright-bot-bypass skill (25.9K installs) 的核心发现:
  - 不要 fake navigator 属性（反而成为检测特征）
  - 只删除 Playwright 特定的 artifact（__pwInitScripts 等）
  - 真正的隐身靠: channel='chrome' + headed + 真实 GPU 指纹

用法:
    await page.add_init_script(STEALTH_SCRIPT)
"""

# ═══════════════════════════════════════════════════════════
# 最小隐身脚本 — 只删 Playwright artifacts
# ═══════════════════════════════════════════════════════════

STEALTH_SCRIPT = r"""
// 仅删除 Playwright 注入的标记变量
// fake navigator 反而有害 — 真实 Chrome > 手写补丁
delete window.__pwInitScripts;
delete window.__playwright__binding__;
delete window.__playwright__;
delete window.__pwManifest;
"""
