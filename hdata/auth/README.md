# hdt auth — 游戏登录 & Token 管理

## 当前状态 (2026-07-16)

```
✅ 域名解析          — domain.py (leyu.com HTML urllib 提取)
✅ Token 刷新 L0/L1  — 缓存 game_token → venue/launch API 纯HTTP刷新
✅ 打码平台          — captcha_solver.py (GeepassSolver + JfbymSolver)
✅ w 参数生成        — geetest_signer.py (RSA-1024 + AES-CBC + lot_parser)
✅ 浏览器登录        — Playwright + CDP Input 自动点击验证码
✅ 手工辅助登录      — manual-capture 可见浏览器人工验证码
✅ get_login 接口    — api.py 统一对外接口（缓存优先→纯HTTP刷新→浏览器降级）

⚠️ 纯 HTTP verify   — w 长度/结构完全匹配，但 e_obj 字段值偏差 76 bytes
                      根因：botion SDK (704KB 混淆) 内部 e_obj 字段无法确定
```

## 架构

```
get_login(username, password)
  ├─ L0: 缓存 game_token (>1h) → 0s                    ✅ 无需浏览器
  ├─ L1: session → venue/launch API → ~2s               ✅ 纯HTTP刷新
  ├─ L2: HTTP 验证码登录                                 ⚠️ verify 未通过
  └─ L3: 浏览器辅助登录 → ~30s                           ✅ 需Playwright
```

## 模块

| 文件 | 职责 |
|------|------|
| `api.py` | 统一对外接口 `get_login(username, password)` |
| `session.py` | 会话管理 + game_token 刷新 + 缓存 |
| `browser_login.py` | Playwright 登录（可见浏览器 + CDP Input 点击验证码） |
| `geetest_signer.py` | w 参数 RSA/AES 签名 + lot_parser |
| `captcha_solver.py` | CaptchaSolver ABC + GeepassSolver + JfbymSolver |
| `captcha.py` | GeeTest v4 captcha fetch (数据获取) |
| `domain.py` | 域名解析 + 缓存 |
| `token_manager.py` | TokenManager CLI (旧，已迁移到 session.py) |
| `http_login.py` | 纯 HTTP 登录 V1 (参考) |
| `http_login_v2.py` | 纯 HTTP 登录 V2 (geepass优先) |
| `selectors.py` | CSS 选择器版本管理 |
| `signature_recapture.py` | CDP Network 签名重抓 |
| `stealth_patches.py` | Playwright artifact 清理 |

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

## 用法

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

- `data/sdk_flow_captured.json` — 人工登录时 SDK 全链路捕获数据（参考值）
- `docs/captcha-flow.md` — 验证码流程完整拆解
- `docs/captcha-research.md` — 验证码逆向研究记录
- `docs/gct4-analysis.md` — gct4.js (GeeTest v4 核心) 逆向分析
- `docs/robustness-analysis.md` — 健壮性分析
