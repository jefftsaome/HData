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
# 打码平台 token（geepass 优先，jfbym 备选）
export GEEPASS_TOKEN=<geepass token>
export JFBYM_TOKEN=<jfbym token>
export CAPTCHA_TOKEN=<legacy jfbym token; deprecated>

# 账号（可选）
export LEYU_USER=<username>
export LEYU_PWD=<password>
```

## CLI

### Python API

```python
from hdata.auth.api import get_login
import os

# 最简单用法（缓存有效时完全纯HTTP）
session = await get_login("username", "password")

# 使用打码平台（尝试纯HTTP登录）
session = await get_login(
    "username",
    "password",
    geepass_token=os.getenv("GEEPASS_TOKEN", ""),
    jfbym_token=os.getenv("JFBYM_TOKEN", ""),
)

# 返回结构:
{
    "account": "username",
    "token": "X-API-TOKEN...",       # 主站 API token
    "uuid": "...",                    # 用户 UUID
    "domain": "https://...",          # 真实域名
    "game_token": "[redacted]",           # 游戏 JWT
    "game_player_id": 123456,         # 玩家 ID
    "game_backend": "host:port",      # 游戏后端
    "signatures": {...},              # API 签名表
}
```

### CLI

```bash
# 首次登录（打开浏览器，手动完成验证码）
uv run python -m hdata.auth.token_manager --manual-capture

# 获取 game_token（优先缓存/刷新）
uv run python -m hdata.auth.token_manager --account lidongsen1

# 诊断
uv run python -m hdata.auth.token_manager --diagnose

# 注入外部 token
uv run python -m hdata.auth.token_manager --inject-game-token <token>
```

## 纯HTTP verify 研究结论

Separate platform tokens fix credential routing and improve diagnostics. They do
not prove that pure HTTP `verify` succeeds or resolve the unknown 76-byte
`e_obj` difference.

经过 50+ 次测试和 20+ 次人工协作的结论：

**w 参数已完全匹配：**
- 长度：始终 1216 hex (608 bytes)
- AES 段：480 bytes (960 hex)
- RSA 段：128 bytes (256 hex)
- AES IV、加密算法、PKCS7 填充均正确

**未解决：e_obj JSON 字段差异**
- 真实 SDK 的 e_obj 原文约 476 bytes
- 我们生成的 e_obj 约 400 bytes
- 差异 76 bytes，即缺少/不同的字段值
- botion SDK (704KB 混淆，字符串 XOR 编码) 无法通过静态分析提取具体字段
- 无 sourcemap 可用
- hook JSON.stringify / crypto.subtle / XHR / fetch 均无效（SDK 使用自实现 AES + JSONP script 请求）

**验证码打码平台对比：**
| 平台 | 速度 | 坐标精度 | verify 通过 |
|------|------|----------|------------|
| geepass (30104) | ~0.2s | 较好 | ❌ |
| jfbym (31111) | ~0.5s | 较好 | ❌ |

打码平台坐标精度不是 verify 失败的原因——e_obj 字段差异才是。

## 关键文件

- `data/sdk_flow_captured.json` — 人工登录时 SDK 全链路捕获数据
- `docs/captcha-flow.md` — 验证码流程完整拆解
- `docs/gct4-analysis.md` — gct4.js 逆向分析
- `docs/robustness-analysis.md` — 健壮性分析
- `docs/interview-guide.md` — 招聘指南

## 更新

2026-07-04
