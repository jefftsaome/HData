# PROJECT.md — 项目总览（Kimi 记忆锚点）

> 更新时间：2026-07-28。本文件是跨会话记忆摘要，细节以各仓库内 docs 为准。
> 本 workspace（D:\my-code-repo\myown\）下有三个仓库，依赖关系：**HSys → HData → htools**。

## 〇、仓库拓扑

| 仓库 | 路径 | 角色 | 说明 |
|---|---|---|---|
| **htools** | `D:\my-code-repo\myown\htools` | 底层公共库 | 通用工具封装（日志 loguru 封装等），HData 和 HSys 都依赖它；hdata 不直接用 loguru，统一走 htools |
| **HData** | `D:\my-code-repo\myown\HData` | 采集工具包 | 见下；git 仓库 jefftsaome/HData，main 分支 |
| **HSys** | `D:\my-code-repo\myown\HSys` | 分析平台 | 见下；非 git 仓库，已补 .gitignore |

## 一、项目在做什么

对某亚洲真人视讯百家乐平台（当前对接的子平台代号 leyu，入口 leyu.com / leyu.me，域名频繁更换）做**数据采集 + 数据分析**：

- **HData**：Python 工具包（pip 包），封装平台对接的全部机制——域名发现、打码登录（geepass/jfbym）、会话/token 管理、WS 协议（lefun 格式）、多桌监控、事件分发。对外不暴露 leyu 字眼，最终打包成 pyd/so 供其他项目调用。
- **HSys**：独立部署的分析平台（依赖 hdata 包），含三块：
  - `HSys/crawl-bot/`：采集程序，单入口 `uv run python main.py --strategy fullscan|streak`（裸跑缺省 streak）
  - `HSys/server/`：FastAPI + 免构建 Vue 的 Web 平台（端口 7200，登录 zhourunfa / 正常密码 / 胁迫密码删库），页面：首页热度榜、长龙浏览器、断龙分析、桌台视图、观战页（实时+牌局复现）
  - `HSys/server/postgres/`：PostgreSQL（docker 容器 hsys-pg），全量数据落这里

## 二、当前运行配置（2026-07-28）

- 采集策略：fullscan 全站采集；账号池机制：**status 0=新号 / 1=启用 / 2=弃用**，写回 config.json，60s 热加载；3 活跃 + N 备用，单号上限 120 桌
- 账号：4 个新号试用中（liyu686、tc686、zhouzh1998、wydyy9999）；30 个老号含 linbing1 全部标 2（linbing1 是唯一确认有数据通道的号，留作战略储备）
- 出口：3 条无忧 VPN（proxies.json，含敏感信息，已 .gitignore，绝不进 git）
- 健康自检：按真实数据帧到达判定静默，单桌→分片重建，>50% 桌中断→平台级掐流降速错峰
- 每局 5 局不下注踢出规则：观察号每 ~2.5 分钟被踢，靠换号轮换维持

## 三、核心研究发现（平台边界）

1. **平台按账号信任等级发放数据**：29 个批量新号从出生起零数据帧（控制帧照通）；linbing1 单号曾同时收 174 桌。所有已采数据全部来自 linbing1
2. **可信号额度会被持续评估收紧**（linbing1：174 桌 → 26 → 9），掐掉后不恢复
3. 假设待验证：**真实下注行为解锁数据通道**（"下注解锁"实验一直没做）
4. 统计层面平台"干净得过分"：断龙/续龙比例均衡、人数金额无异常抖动、最后一秒无操控痕迹——越是严丝合缝越像有意为之
5. 长龙分析口径：连胜≥5 的局与下一局配对，"反额↓反人↑顺额↑顺人↑"模型反率 ≈47.7%（n=1349），无显著偏离

## 四、数据规模

- PG 中 rounds 24,000+ 局、events_raw 190 万+ 帧；224 张桌有数据
- 时间戳是毫秒 epoch；查询注意时区（用 timestamptz，别用裸 timestamp，会错一年）

## 五、仓库与协作约定

- 工作目录 `D:\my-code-repo\myown\`（含 htools / HSys / HData 三仓库）；只有 HData 是 git 仓库（jefftsaome/HData，main 分支），HData 改动要 commit+push；HSys/htools 非 git 仓库（HSys 已补 .gitignore）
- 采集进程由用户自己重启，助手只改源码并记 `HSys/crawl-bot/待生效改动.md`
- 测试基线：hdata 226、crawl-bot 75、server 122
- 文档在 HData/docs/（中文命名，含《数据样本》《平台边界试探》等）
- 用户风格：中文大白话、结论带实测数字、不用看不懂的术语

## 六、待办/悬而未决

- [ ] 4 个新号成色验证（有数据+不被踢=可信号）
- [ ] "下注解锁数据通道"实验（用户拍板账号后执行）
- [ ] 养号路线：少量可信号，设备 UUID 独立（目前所有号共用 9f790aa1…，待修）
- [ ] streak 策略搬健康自检（分析完成，未实施）
- [ ] 断龙策略回测持续迭代；24 小时体检报告欠账
