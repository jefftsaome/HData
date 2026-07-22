# HSys 分析平台

独立的数据采集 + 分析 + 实时展示平台。采集层复用 HData 包（`hdata/` 与
`scripts/streak_hunter.py`），本目录只放平台层的东西：配置、数据、
归档、Web 服务。

```
hsys/
├── README.md               ← 本文档（架构总览 + 数据层决策 + 运行手册）
├── crawl-bot/              ← 采集层
│   ├── hunter.py           ← 配置驱动启动器（复用 scripts/streak_hunter）
│   ├── archive.py          ← SQLite 时代的 events_raw 归档；
│   │                         PG 时代由"分区 detach + pg_dump"接替
│   ├── config.example.json ← 配置模板（提交）
│   ├── config.json         ← 真实配置（.gitignore，严禁提交）
│   ├── data/               ← SQLite（PG 切换前）+ 归档文件（.gitignore）
│   └── logs/               ← 采集日志（.gitignore）
└── server/                 ← Web 服务层
    ├── README.md           ← 技术选型与建设规划（2026-07-22 定版）
    ├── app/                ←（待建）FastAPI 应用
    └── web/                ←（待建）Vue 页面与共享组件
```

## 架构（2026-07-22 定版）

```
              ┌──────────── crawl-bot ────────────┐
  平台网站 ──WS──► hunter.py（登录/订阅/落库）      │
              │    └─► PostgreSQL（本机）          │
              │         ├─ 派生分析表（永久）       │
              │         └─ events_raw（月分区，     │
              │            冷数据 detach+dump 归档）│
              └────────┐
                       │ HTTP POST /ingest（批量 200ms）
                       ▼
              ┌────── server（FastAPI）────────────┐
              │  /ingest → 内存事件总线 → /ws 广播   │←──SQL──► PostgreSQL
              │  登录（双密码）→ Vue 页面            │
              └──────────────┬───────────────────────┘
                             ▼ WebSocket
                        浏览器（局域网 3-5 人）
```

## 数据库：PostgreSQL（2026-07-22 决策，取代原 SQLite 方案）

原定"SQLite 保留"决策随多用户 Web 平台需求作废，理由：

- 3-5 人同时在线读 + 采集进程持续写，SQLite WAL 单写锁在长查询下会
  堆积；PG 多版本并发无此问题；
- PG 原生**声明式分区**：events_raw 按月分区，冷数据 `DETACH PARTITION`
  + `pg_dump` 即完成归档，比手写 archive.py 干净；
- PG `LISTEN/NOTIFY` 自带消息通道，覆盖"数据库事件通知"场景，
  使 Redis 失去最大存在理由（见下节）；
- 互联网化的权限、备份、运维生态成熟。

形态约定：

- PG 装在采集机本机（Windows 服务，仅监听 localhost:5432），
  库名 `hdata`；crawl-bot 与 server 都走本机连接；
- 时间戳一律 `BIGINT` epoch 毫秒（与现有代码一致，避开时区坑）；
- `events_raw` 按月 `PARTITION BY RANGE (ts)`；热窗默认保留最近
  2 个分区，更老分区 detach 后 `pg_dump` 成 `events_raw-YYYY-MM.sql.gz`
  永久保存（存 `crawl-bot/data/archive/`），需要时可回灌；
- 派生分析表（streak_rounds / streak_episodes / lobby_snapshots /
  rounds / round_bet_points 等）不分区、永不清。

迁移路径（P0 里程碑，见 server/README.md）：

1. 安装 PG（`scoop install postgresql` 或 EDB 安装包），`createdb hdata`；
2. `hsys/crawl-bot/schema_pg.sql` 建库（含 events_raw 分区）；
3. `streak_store.py` 抽象 Store 接口，`store_pg.py`（asyncpg）与
   现有 sqlite 实现并存，`config.json` 加 `db.backend` 切换；
4. `scripts/migrate_sqlite_to_pg.py` 一次性搬迁：派生分析表全量 +
   events_raw 最近 30 天；旧 `data/streak.db` 保留为只读备份，
   已有 NDJSON.gz 归档仍可按需回灌；
5. 切换当天下线 stdlib viewer（其页面由 server 接替，见 P2）。

## 实时推送：WebSocket（2026-07-22 决策）

- 链路：crawl-bot → `POST /ingest`（200ms 批量打包，带共享密钥）
  → server 内存事件总线 → `/ws` 广播给已订阅的浏览器；
- 浏览器端按桌台 / 事件类型订阅，页面只收自己关心的流；
- 选 WS 不选 SSE：WS 还能上行订阅指令，换桌台不用重建连接；
- 暂不用 PG LISTEN/NOTIFY 做推送主链路（采集进程直连 server 更直接），
  它留给"非采集写入者"（如分析任务写完结果表后通知前端刷新）。

## Redis：允许引入，但当前暂缓

- 3-5 用户单机部署，server 内存事件总线够用；
- PG LISTEN/NOTIFY 覆盖持久化队列场景，Redis 剩余价值（缓存）在
  本规模无意义；
- Windows 无官方 Redis（需 Memurai 或 WSL），多一份运维负担；
- **引入触发条件**：server 多进程/多机部署、出现热点查询需要缓存、
  需要跨机任务队列。架构上事件总线做成接口（`bus.py`），届时换
  Redis 实现，业务代码不动。

## 数据保留策略（PG 时代）

| 数据 | 位置 | 策略 |
|---|---|---|
| 派生分析表 | PG `hdata` | **永不清** |
| events_raw 热窗 | PG 月分区（默认留最近 2 个分区） | 更老分区 detach |
| events_raw 冷数据 | `data/archive/events_raw-YYYY-MM.sql.gz` | **永久保存** |
| 采集日志 | `logs/hunter.log*` | loguru 50 MB 滚动 |

（SQLite 时代的 archive.py 与 purge_raw_days 机制保留到 PG 切换完成，
切换后 purge 逻辑废弃，由分区 detach 接替。）

## 胁迫密码删除范围（设计约定，PG 时代更新）

用户在 Web 端输入胁迫密码时，依次：先杀采集进程 → 删除以下内容：

1. **DROP DATABASE hdata**（含全部分区与历史）；
2. `crawl-bot/data/` 整个目录（归档 dump、旧 SQLite 备份）；
3. `crawl-bot/logs/` 全部日志；
4. `crawl-bot/config.json`（含平台账号与打码 token、PG 口令）；
5. `config.example.json` 保留；代码与文档保留。

server 进程本身继续运行、界面表现与正常登录一致（数据已空）。
这是"停止采集 + 删所有日志 + 删数据库"的唯一自洽解释。

## 局域网部署注意

- PG 只监听 localhost，不对外开放；server 绑 `0.0.0.0:7200`，
  Windows 防火墙放行 7200；
- 登录认证必须开启（双密码：正常 / 胁迫），口令 bcrypt 哈希存储；
- 日后上互联网前必须加：HTTPS、登录限流、/ingest 密钥轮换、
  PG 口令与共享密钥移入 Windows 凭据管理器或环境变量。

## 运行手册（PG 切换后）

```bash
# 0. 一次性：安装并初始化 PG
scoop install postgresql
initdb -D <pgdata> -U postgres -W   # 或按 scoop 提示初始化
pg_ctl -D <pgdata> start
createdb -U postgres hdata
psql -U postgres -d hdata -f hsys/crawl-bot/schema_pg.sql

# 1. 一次性：迁移历史数据
uv run python scripts/migrate_sqlite_to_pg.py --from data/streak.db

# 2. 日常采集（config.json 里 db.backend = "pg"）
uv run python hsys/crawl-bot/hunter.py --check
uv run python hsys/crawl-bot/hunter.py

# 3. 冷数据归档（每月一次，或做成 Automation）
#    detach 上月分区 + pg_dump，脚本化在 P0 内完成

# 4. Web 服务
uvicorn hsys.server.app.main:app --host 0.0.0.0 --port 7200
```

（PG 切换前的 SQLite 手册见 git 历史版本。）

## 路线图

- [x] crawl-bot 配置化启动 + SQLite 归档（2026-07-22）
- [ ] **P0**：PG 落地（schema_pg.sql、store_pg.py、迁移脚本、分区归档脚本）
- [ ] **P1**：server 骨架（FastAPI + 登录双密码 + /ingest + /ws + 实时首页）
- [ ] **P2**：四页迁移（Vue 化 + /api/query + ECharts）
- [ ] **P3**：可配置看板（布局 JSON + 拖拽）+ 分析参数 UI 化
- [ ] **P4**：加固上互联网（HTTPS、限流、密钥管理）
- [ ] viewer（stdlib）下线
