# get_token 健壮性分析报告

> 日期: 2026-06-29
> 状态: 核心链路已跑通，存在已知薄弱点，逐步加固中

## 当前架构

```
TokenManager.get_token()
  ├─ L0: 缓存 game_token (>1h) → 0s       ✅ 可控
  ├─ L1: session → venue/launch API → ~2s  ⚠️ 半可控
  ├─ L2: browser profile → Playwright 自动跳转 → ~5s  ✅ 可用
  ├─ L3a: 纯 HTTP + jfbym → ~10s           ⚠️ verify 精度敏感
  └─ L3b: manual-capture 人工辅助登录 → ~30-120s ✅ 可用
```

## 不可控因素 & 可控措施

### L0 缓存 — ✅ 完全可控

| 因素 | 可控? | 已做措施 |
|------|-------|---------|
| 缓存文件损坏 | ✅ | 自动检测 + 删除重建 |
| 并发写入冲突 | ✅ | asyncio.Lock |
| Token 过期 | ✅ | JWT exp 检查 >1h 阈值 |

### L1 API 刷新 — ⚠️ 半可控

| 因素 | 可控? | 已做措施 | 残留风险 |
|------|-------|---------|---------|
| venue/launch 接口挂了 | ❌ | 失败原因链记录 | 乐鱼服务器不可控 |
| X-API-XXX 签名过期 | ⚠️ | --recapture-signatures | 需本地浏览器已登录并开放 CDP |
| 域名变更 | ⚠️ | --diagnose 可发现 | 需人工介入获取新域名 |

### L3 登录阶段 — ⚠️ 仍有脆弱点

| 因素 | 可控? | 已做措施 | 残留风险 |
|------|-------|---------|---------|
| 本地浏览器可用性 | ⚠️ | manual-capture 兜底 | 浏览器版本升级可能影响行为 |
| CDP 端口 | ✅ | 9222/9223 自动探测 + 环境变量 | 端口被占用需人工处理 |
| jfbym 余额 | ⚠️ | --diagnose 显示余额 | jfbym 涨价/跑路/API 变更 |
| jfbym 识别率 | ❌ | 3 次重试 | 验证码类型变更不兼容 |
| CSS 选择器 | ⚠️ | selectors.py 快照 | 首次改版需人更新 |
| GeeTest captcha_id | ❌ | 无 | 硬编码 eaffad4f65a38a259ae369faf0c2f1a3 |
| CDP Input 方案 | ❌ | 无 | 黑盒方案，GeeTest SDK 升级可能失效 |
| 登录流程变更 | ❌ | 无 | 两步验证、手机验证码等 |
| 账号风控 | ❌ | 失败原因链 | 自动化登录可能触发封号 |

## 健壮性问题分级

### 🔴 高危（随时可能挂，无自愈）

1. **验证码链路不稳定** — 纯 HTTP verify 对坐标/参数极敏感，失败率仍高
2. **GeeTest/botion 升级** — captcha_id、验证码类型、SDK 检测算法均可单方面改变
3. **CDP Input 黑盒** — 不知道为何工作、何时失效，无文档或社区支持

### 🟡 中危（有诊断但需人介入）

4. **域名+签名循环依赖** — 换域名需运行 --recapture-signatures，但首次登录本身需要域名
5. **CSS 选择器过期** — 网站改版后需人工更新
6. **签名表全空是正常行为** — 无法区分"本就为空"和"解密失败"

### 🟢 低危（已有防护）

7. **jfbym 余额** — --diagnose 可见
8. **缓存损坏** — 自动修复
9. **端口变化** — 自动发现

## 改进路线图

### 已实施 (截至 2026-07-04)

- [x] `--diagnose` 自诊断命令
- [x] 域名自动缓存到 `.cache/domain.json`
- [x] 失败原因链 `TokenUnavailableError.chain`
- [x] 知识文档 `hdt/auth/README.md`
- [x] 签名自动捕获 `--recapture-signatures`
- [x] CSS 选择器快照 `selectors.py`
- [x] jfbym 余额查询集成到 diagnose
- [x] 域名解析模块 `domain.py` (leyu.com HTML urllib 提取)
- [x] Playwright `manual-capture` 人工辅助登录
- [x] CaptchaSolver 抽象 + JfbymSolver
- [x] EKAI: "y7R8" 替换 ZAhG
- [x] 动态 lot_parser (从 ctStore 提取)
- [x] gct4.js 完整逆向文档
- [x] captcha_output 来源确认 (seccode.captcha_output)
- [x] 全链路 SDK 捕获 (kaptchcate → verify → validateGeeCheckV2 → login)
- [x] CDP 点击坐标缩放修正 (botion_bg 替代 botion_click)

## 剩余问题 (P0)

- [ ] **纯 HTTP verify 始终 result=fail** — 18+ 次尝试，坐标疑似不准确。需要对比人工通过的 w 参数 (data/sdk_flow_captured.json)
- [ ] **AB 测试 hook 可靠性** — addEventListener hook 仅成功一次，后续无法复现。CDP Network 抓包可替代

### 下一步 (P3 优先级)

- [ ] `--health` 定时巡检 (cron 每 30min，余额<100 或 token<1h 告警)
- [ ] 验证码类型检测 (弹窗出现时记录 captcha_type，类型变更即报错)
- [ ] 多打码平台备胎 (2captcha / capsolver)，jfbym 挂了自动切换
- [ ] 域名轮换感知 (venue/launch 连续失败 3 次 → 自动 CDP 重解析)
- [ ] Session 预热池 (预登录 2-3 账号，token 热备)

### 长期

- [ ] CDP Input 方案白盒化 (搞清楚 GeeTest SDK 检测原理)
- [ ] 真实鼠标轨迹模拟 (贝塞尔曲线 + 随机延迟，减少 raw CDP 依赖)

## 相关文件

- `hdt/auth/README.md` — 架构文档 + 故障速查
- `hdt/docs/auth-research.md` — 认证研究原始笔记
- `hdt/docs/captcha-research.md` — 验证码逆向笔记
