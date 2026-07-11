# hdt auth — 乐鱼登录 & Token 管理

## 当前状态 (2026-07-04)

```
✅ 域名解析          — domain.py (leyu.com HTML urllib 提取)
✅ Token 缓存 L0/L1  — token_manager.py (四层降级)
✅ 打码平台抽象      — captcha_solver.py (CaptchaSolver ABC + JfbymSolver)
✅ w 参数生成        — geetest_signer.py (RSA-1024 + AES-CBC + lot_parser)
✅ SDK 链路捕获      — kaptchcate → verify → validateGeeCheckV2 → login (data/sdk_flow_captured.json)
✅ captcha_output 来源确认 — verify 响应 seccode.captcha_output
✅ RSA 密钥确认      — GeekedTest 1024-bit key 对 botion 有效
✅ lib key 确认      — EKAI: "y7R8" (非 ZAhG: "MwHu")
✅ lot_parser 确认   — 动态从 __BOTION__.ctStore 提取
✅ EKAI: "y7R8"     — 非标准 GeeTest 的 ZAhG: "MwHu"

❌ 纯 HTTP verify   — 链路完全通到 verify API，但始终 result=fail
                     原因待查：怀疑 userresponse 格式或 e_obj 字段有偏差
                     AB 测试捕获到一次人工点击 vs jfbym ~20px 偏移
❌ L3 浏览器登录    — CDP Input 点击方案可行，但当前 browser-act 实例 Input 域被禁用
❌ browser-act 生命周期 — BrowserActManager 已写，未端到端集成测试
```

## 亟待解决（接手的人优先看这个）

### 1. 纯 HTTP verify 为何 `result=fail`

```
fetch_captcha → jfbym 31111 → generate_w → verify API
                                          ↓
                                    result=fail (18+ 次尝试)
```

已验证正确：
- RSA key: GeekedTest n=0xC1E3934D... e=0x10001 ✅
- lot_parser: 从 ctStore 动态提取 ✅  
- EKAI: "y7R8" (botion 特有) ✅
- e_obj JSON 格式: compact separators ✅

未验证：
- `userresponse` 格式是否正确？当前是 `[[x1,y1],[x2,y2],[x3,y3]]`
- e_obj 是否有多余/缺少字段？
- jfbym 坐标精度是否足够？

**建议的调试路径：**
1. 重做 AB 测试（jfbym vs 人工点击），确认坐标偏差
2. 人工通过的 w 在 data/sdk_flow_captured.json — 对比我们的 w
3. 考虑用不同打码平台交叉验证 (capsolver, 2captcha)

### 2. browser-act Input 域

当前 browser-act 实例 CDP Input.dispatchMouseEvent 无响应。
解决：重启 browser-act 或降级内核版本。

### 3. CDP 点击缩放

headless_login.py 已修正为使用 botion_bg (272x181) 而非 botion_click (272x235)。

## 架构

```
TokenManager.get_token()
  ├─ L0: 缓存 game_token (>1h) → 0s                    ✅
  ├─ L1: session → venue/launch API → ~2s               ✅ (需有效签名)
  ├─ L2: browser profile → Playwright 自动跳转          ⚠️ 未充分测试
  └─ L3: raw CDP + jfbym 完整登录 → ~20s                ⚠️ Input 域问题

L3 展开:
  browser-act stealth Chrome ← raw CDP
    ├─ Runtime.evaluate → 填表 + 登录按钮
    ├─ Runtime.evaluate → 提取验证码图片 (DOM)
    ├─ JfbymSolver.solve() → jfbym 31111
    └─ Input.dispatchMouseEvent → 点击 (需 Input 域可用)
```

## 模块

| 文件 | 职责 |
|------|------|
| token_manager.py | TokenManager + 四层降级 + CLI + --diagnose |
| headless_login.py | raw CDP 登录引擎 |
| domain.py | leyu.com HTML urllib 域名解析 + 缓存 |
| captcha_solver.py | CaptchaSolver ABC + JfbymSolver |
| captcha.py | GeeTest v4 fetch + jfbym solve (旧版) |
| geetest_signer.py | w 参数 RSA/AES 签名 + lot_parser |
| browser_act.py | browser-act 进程生命周期管理 |
| selectors.py | CSS 选择器版本管理 |
| signature_recapture.py | CDP Network 签名重抓 |
| stealth_patches.py | Playwright artifact 清理 |
| browser_login.py | 旧版 Playwright 登录 CLI |
| http_login.py | 纯 HTTP 登录 (参考) |

## 环境变量

```bash
export JFBYM_TOKEN=xxx     # jfbym API token（必需）
export LEYU_USER=xxx       # 用户名（可选）
export LEYU_PWD=xxx        # 密码（可选）
```

## CLI

```bash
uv run python -m hdt.auth.token_manager --account X --user X --pwd X  # 获取 token
uv run python -m hdt.auth.token_manager --resolve-domain              # 解析域名
uv run python -m hdt.auth.token_manager --diagnose                    # 诊断
uv run python -m hdt.auth.token_manager --health                      # 健康检查
```

## Python

```python
from hdt.auth import TokenManager, resolve_domain
tm = TokenManager(account="x", user="u", pwd="p")
token = await tm.get_token()
```

## 关键文件

- `data/sdk_flow_captured.json` — 人工登录时 SDK 全链路捕获数据
- `docs/captcha-flow.md` — 验证码流程完整拆解
- `docs/gct4-analysis.md` — gct4.js 逆向分析
- `docs/robustness-analysis.md` — 健壮性分析
- `docs/interview-guide.md` — 招聘指南

## 更新

2026-07-04
