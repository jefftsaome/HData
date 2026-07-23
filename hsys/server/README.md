# HSys Web 服务层 — 技术选型与建设规划（2026-07-22 定版）

FastAPI + PostgreSQL + Vue 3，局域网 3-5 用户起步，可扩到互联网。

## 一、技术选型

| 层 | 选择 | 理由 / 备选否决 |
|---|---|---|
| 后端 | **Python + FastAPI + uvicorn** | 直接复用 `hdata` 包与全部分析脚本逻辑；asyncio 与采集侧同构。否 Golang：性能在 5 用户规模过剩，丢掉代码复用，平白多一门语言；否 Dart 后端：数据生态太弱 |
| 数据库 | **PostgreSQL 16 + asyncpg** | 见 ../README.md（并发、月分区、LISTEN/NOTIFY） |
| 前端 | **Vue 3（CDN 免构建）+ 原生 ES Module** | 响应式数据天然适配 WS 实时推送；免构建 = 一页一个 HTML 文件，改完刷新即生效。否 React+Vite SPA：构建链重，分析页高频调整时不划算；否 Flutter/Dart：等真需要桌面/移动 APP 再评估 |
| 图表 | **ECharts（CDN）** | K 线级金融图表能力，局内资金曲线升级靠它 |
| 看板布局 | **gridstack.js（P3 引入）** | 拖拽网格，布局 JSON 存库 |
| 实时推送 | **原生 WebSocket** | 可上行订阅指令；SSE 只能下行 |
| Redis | **暂缓** | 触发条件与接口预留见 ../README.md |

**为什么 Vue 免构建是当前最优解**：分析页面跟随研究高频调整（用户核心
需求 ⑤），免构建模式下新增/修改一个分析页 = 复制一个 HTML 文件改查询
参数，agent 和用户都能直接改、刷新即看；共享逻辑（导航、WS 客户端、
图表封装、API 客户端）抽成 `/static/js/` 模块。哪天页面复杂到免构建
hold 不住（状态管理混乱、组件复用困难），再整体迁 Vite 构建，页面
结构与组件接口平移，不是重写。

## 二、页面灵活调整设计（核心需求 ⑤ 的落地方案）

1. **一页一文件**：`web/pages/*.html`，每页一个独立 Vue 小应用，
   共享 `/static/js/{vue,echarts}.js` 与 `/static/js/app/`（api.js /
   ws.js / components/）。新分析页 = 复制模板页改配置块。
2. **通用查询 API**：`POST /api/query {table, select, where, group_by,
   having, order_by, limit}`——白名单校验表名/列名/运算符，覆盖八成
   "换个口径看数据"的需求，**不动后端**。
3. **分析参数 UI 化**：阈值、时段、桌型、龙长全部做成页面上控件 +
   URL query 持久化（链接可直接分享给别人看同一视图）。
4. **可配置看板（P3）**：看板页读用户布局 JSON（存 PG），gridstack
   拖拽；面板类型：指标卡 / 图表 / 表格 / 热力图 / 实时桌台。

## 三、目录结构（待建）

```
server/
├── app/
│   ├── main.py            ← FastAPI 入口，挂路由与静态目录
│   ├── config.py          ← 读 ../crawl-bot/config.json + server 段
│   ├── db.py              ← asyncpg 连接池
│   ├── auth.py            ← 双密码登录、session、恒时响应
│   ├── ingest.py          ← POST /ingest（共享密钥，采集事件入口）
│   ├── bus.py             ← 内存事件总线（接口抽象，Redis 预留）
│   ├── ws.py              ← /ws 订阅管理与广播
│   └── api/
│       ├── query.py       ← 通用查询（白名单）
│       ├── rounds.py      ← 局详情 / 局内帧
│       ├── episodes.py    ← 长龙列表与筛选
│       └── strategy.py    ← 策略页专用聚合（七条件/链式等）
└── web/
    ├── static/js/         ← vue.global.js / echarts.js / app/
    └── pages/             ← index / analysis / heat / strategy-od /
                               board（P3）/ login
```

## 四、API 与协议草案

- `POST /login`：双密码（正常/胁迫），签发 HttpOnly session cookie，
  两条路径恒时响应（统一补齐到 ~300ms）；
- `POST /api/query`：通用查询，白名单防注入；
- `GET /api/rounds/{round_id}/frames`：局内帧曲线；
- `POST /ingest`：采集事件入口，头带共享密钥，只收批量事件数组；
- `WS /ws`：上行 `{action:"subscribe", tables:[...], events:[...]}`，
  下行 `{type:"round"|"bet"|"road"|"status", table_id, data}`；
  未登录 session 连接即拒。

## 五、登录：双密码设计（不变，PG 时代范围更新）

每个用户配两个密码，bcrypt 哈希（不同盐）：

| 输入 | 行为 |
|---|---|
| 正常密码 | 正常登录，进入系统 |
| 胁迫密码 | 界面表现与正常登录**完全一致**，后台静默异步：<br>① 终止采集进程（hunter.py）<br>② **DROP DATABASE hdata** + 删 `crawl-bot/data/`、<br>`crawl-bot/logs/`、`crawl-bot/config.json`<br>③ 日志记录与正常登录无差别 |

## 六、部署（局域网）

```bash
uvicorn hsys.server.app.main:app --host 0.0.0.0 --port 7200
```

Windows 防火墙放行 7200；PG 只听 localhost；登录必开。上互联网前补：
HTTPS、登录限流（如 5 次/分钟/IP）、/ingest 密钥轮换。

## 七、里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0** | ~~PG 安装初始化、schema_pg.sql（含 events_raw 月分区）~~（2026-07-23 已交付 postgres/ 镜像）+ store_pg.py、SQLite→PG 迁移脚本、分区归档脚本 | 采集写 PG 跑 24h 无异常；迁移后派生表行数与 SQLite 一致 |
| **P1** | FastAPI 骨架：登录双密码、/ingest、/ws、实时首页（Vue） | 登录/胁迫两条路径行为正确；浏览器秒级看到新局 |
| **P2** | 四页迁移（长龙/断龙分析/热度/策略页）+ /api/query + ECharts 局内图升级 | 覆盖 viewer 全部功能后 viewer 下线 |
| **P3** | 可配置看板（布局 JSON + gridstack 拖拽）+ 分析参数 UI 化 | 不改代码拼出新分析视图 |
| **P4** | 加固：HTTPS、限流、密钥管理 | 可暴露到互联网 |
