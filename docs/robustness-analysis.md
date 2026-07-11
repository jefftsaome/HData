# get_token 健壮性分析报告

> 日期: 2026-06-29
> 状态: 核心链路已跑通，存在已知薄弱点，逐步加固中

## 当前架构

```
TokenManager.get_token()
  ├─ L0: 缓存 game_token (>1h) → 0s       ✅ 可控
  ├─ L1: session → venue/launch API → ~2s  ⚠️ 半可控
  ├─ L2: browser profile → 自动跳转 → ~5s  ❌ 当前不可用
  └─ L3: raw CDP + jfbym 完整登录 → ~20s   ⚠️ 脆弱点最多
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
| X-API-XXX 签名过期 | ⚠️ | --recapture-signatures | 需 browser-act 在线 + 已登录页面 |
| 域名变更 | ⚠️ | --diagnose 可发现 | 需人工介入获取新域名 |

### L3 完整登录 — ❌ 脆弱点集中

| 因素 | 可控? | 已做措施 | 残留风险 |
|------|-------|---------|---------|
| browser-act 在线 | ❌ | --diagnose 检测 + 提示 | 外部闭源商业产品 |
| browser-act 反爬绕过 | ❌ | 无 | 乐鱼升级反爬可能穿透 |
| CDP 端口 | ✅ | ps aux 自动发现 | — |
| jfbym 余额 | ⚠️ | --diagnose 显示余额 | jfbym 涨价/跑路/API 变更 |
| jfbym 识别率 | ❌ | 3 次重试 | 验证码类型变更不兼容 |
| CSS 选择器 | ⚠️ | selectors.py 快照 | 首次改版需人更新 |
| GeeTest captcha_id | ❌ | 无 | 硬编码 eaffad4f65a38a259ae369faf0c2f1a3 |
| CDP Input 方案 | ❌ | 无 | 黑盒方案，GeeTest SDK 升级可能失效 |
| 登录流程变更 | ❌ | 无 | 两步验证、手机验证码等 |
| 账号风控 | ❌ | 失败原因链 | 自动化登录可能触发封号 |

## 健壮性问题分级

### 🔴 高危（随时可能挂，无自愈）

1. **browser-act 依赖** — 外部闭源商业产品，版本升级可能改变行为，服务商跑路整个方案崩溃
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
- [x] browser-act 进程管理 `browser_act.py`
- [x] CaptchaSolver 抽象 + JfbymSolver
- [x] EKAI: "y7R8" 替换 ZAhG
- [x] 动态 lot_parser (从 ctStore 提取)
- [x] gct4.js 完整逆向文档
- [x] captcha_output 来源确认 (seccode.captcha_output)
- [x] 全链路 SDK 捕获 (kaptchcate → verify → validateGeeCheckV2 → login)
- [x] CDP 点击坐标缩放修正 (botion_bg 替代 botion_click)

## 剩余问题 (P0)

- [ ] **纯 HTTP verify 始终 result=fail** — 18+ 次尝试，坐标疑似不准确。需要对比人工通过的 w 参数 (data/sdk_flow_captured.json)
- [ ] **browser-act Input 域被禁用** — 当前实例 Input.dispatchMouseEvent 无响应，需重启或换内核
- [ ] **AB 测试 hook 可靠性** — addEventListener hook 仅成功一次，后续无法复现。CDP Network 抓包可替代

### 下一步 (P3 优先级)

- [ ] `--health` 定时巡检 (cron 每 30min，余额<100 或 token<1h 告警)
- [ ] 验证码类型检测 (弹窗出现时记录 captcha_type，类型变更即报错)
- [ ] browser-act 替代方案调研 (rebrowser-patches / undetected-chromedriver)
- [ ] 多打码平台备胎 (2captcha / capsolver)，jfbym 挂了自动切换
- [ ] 域名轮换感知 (venue/launch 连续失败 3 次 → 自动 CDP 重解析)
- [ ] Session 预热池 (预登录 2-3 账号，token 热备)

### 长期

- [ ] CDP Input 方案白盒化 (搞清楚 GeeTest SDK 检测原理)
- [ ] 真实鼠标轨迹模拟 (贝塞尔曲线 + 随机延迟，减少 raw CDP 依赖)
- [ ] browser-act 进程生命周期自治 (启动/停止/健康检查自动化)

## 相关文件

- `hdt/auth/README.md` — 架构文档 + 故障速查
- `hdt/docs/auth-research.md` — 认证研究原始笔记
- `hdt/docs/captcha-research.md` — 验证码逆向笔记
