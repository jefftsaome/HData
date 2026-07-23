# HSys 分析平台

独立部署的数据采集 + 分析 + 实时展示项目。唯一外部依赖是 `hdata` 包
（登录/协议/WS 客户端），不依赖 HData 仓库的 `scripts/` 目录。

```
hsys/
├── README.md               ← 本文档（架构总览 + 运行手册）
├── crawl-bot/              ← 采集层（单入口 + 策略制）
│   ├── main.py             ← 唯一入口：--strategy 选择采集内容
│   ├── strategies/
│   │   └── streak.py       ← 长龙采集策略（连胜桌监控到断龙/删失）
│   ├── store.py            ← 落库层（当前 SQLite；PG 后端 P0 接入）
│   ├── schema.sql          ← SQLite 全量 DDL（v2 + streak v3）
│   ├── archive.py          ← SQLite 时代的 events_raw 归档；
│   │                         PG 时代由分区 detach + pg_dump 接替
│   ├── config.example.json ← 配置模板（提交）
│   ├── config.json         ← 真实配置（.gitignore，严禁提交）
│   ├── data/               ← SQLite（PG 切换前）+ 归档文件（.gitignore）
│   └── logs/               ← 采集日志（.gitignore）
└── server/                 ← 服务层
    ├── README.md           ← 技术选型与建设规划（2026-07-22 定版）
    ├── Dockerfile          ← hsys-server 镜像（FastAPI，已验证）
    ├── app/                ← FastAPI 应用（P1 骨架：/healthz）
    ├── web/                ←（待建）Vue 页面与共享组件
    └── postgres/           ← PostgreSQL 镜像（采集数据存储）
        ├── Dockerfile      ← postgres:16 + 调优 + initdb + 归档脚本
        ├── postgresql.conf
        ├── initdb/01_schema.sql
        ├── archive.sh      ← 月分区滚动创建 + 超窗压缩归档
        └── README.md       ← 存储规划详档（压缩/迁移/扩容）
```

## 架构（2026-07-23 定版）

```
              ┌──────────── crawl-bot ────────────┐
  平台网站 ──WS──► main.py --strategy streak       │
              │    （发现层+监控层，hdata 包驱动）   │
              │    └─► 落库（现 SQLite → P0 切 PG） │
              └────────┬───────────────────────────┘
                       │                            │
              HTTP POST /ingest              SQL（asyncpg）
               （P1 启用）                            │
                       ▼                            ▼
              ┌────── server（FastAPI）────┐   ┌─ postgres（Docker）───┐
              │  /ingest → 事件总线 → /ws   │   │ 派生分析表（永久）      │
              │  登录（双密码）→ Vue 页面   │←─►│ events_raw（月分区，    │
              └──────────────┬─────────────┘   │  >12月压缩归档到 /archive）│
                             ▼ WebSocket       └───────────────────────┘
                        浏览器（局域网 3-5 人）
```

## 数据存储：PostgreSQL（Docker，2026-07-23 落地镜像）

- 镜像：`hsys/server/postgres/`（postgres:16 + 写多读多调优 +
  首启自动建表 + events_raw 月分区 + 归档脚本）；
- 部署：本机 Windows Docker，命名卷 `hsys-pgdata` 持久化，端口只绑
  127.0.0.1；详细操作见 `server/postgres/README.md`；
- **压缩策略**：PG 核心无整表压缩；本项目采用——
  ① TOAST(lz4) 行内大字段压缩（默认开）；
  ② **超 12 个月的 events_raw 分区 DETACH + pg_dump 压缩归档**
  （`docker exec hsys-pg hsys-archive 12`，JSON 约 5-10 倍压缩率），
  归档文件永久保存、可回灌；
  ③ TimescaleDB 在线压缩暂缓（扩展依赖 + 许可考虑，TB 级再评估）；
- **扩容路径**：未来购独立存储服务器 → 同款容器 + 流复制平滑迁移
  （或 pg_dump 全库迁移），归档文件直接拷贝，应用侧只改 DSN；
- **SQLite 历史迁移**：`scripts/migrate_sqlite_to_pg.py`（P0 待写），
  派生表全量 + events_raw 近 30 天；旧 streak.db 留作只读备份。

## 实时推送：WebSocket（2026-07-22 决策）

- 链路：crawl-bot → `POST /ingest`（200ms 批量，带共享密钥）
  → server 内存事件总线 → `/ws` 广播；
- 浏览器按桌台/事件类型订阅；选 WS 不选 SSE（可上行订阅指令）；
- PG LISTEN/NOTIFY 暂不使用，留给"非采集写入者"事件通知。

## Redis：允许引入，当前暂缓

- 3-5 用户单机：内存事件总线够用；PG LISTEN/NOTIFY 覆盖持久化
  队列场景；Windows 无官方 Redis（Memurai/WSL 多份运维）；
- **引入触发条件**：server 多进程部署、热点缓存、跨机任务队列；
  事件总线做成接口（bus.py），届时换实现业务不动。

## 胁迫密码删除范围（设计约定）

Web 端输入胁迫密码时：先杀采集进程 → ① DROP DATABASE hdata；
② 删 `crawl-bot/data/`（归档 dump、SQLite 备份）；③ 删
`crawl-bot/logs/`；④ 删 `crawl-bot/config.json`。
server 继续运行、界面表现如常（数据已空）。代码与
config.example.json 保留。

## 局域网部署注意

- PG 只绑 127.0.0.1（容器间走 docker network）；server 绑
  `0.0.0.0:7200`，防火墙放行 7200；
- 登录必开（双密码），口令 bcrypt 哈希；
- 上互联网前补：HTTPS、登录限流、/ingest 密钥轮换、密钥移出
  配置文件（环境变量/凭据管理器）。

## 运行手册

```bash
# ── PG（首次）──
docker build -t hsys-pg hsys/server/postgres
docker volume create hsys-pgdata
docker run -d --name hsys-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=hsys -e POSTGRES_PASSWORD=<强密码> -e POSTGRES_DB=hdata \
  -v hsys-pgdata:/var/lib/postgresql/data \
  -v %cd%\hsys\crawl-bot\data\archive:/archive \
  --restart unless-stopped hsys-pg

# ── 采集（crawl-bot 单入口）──
uv run python hsys/crawl-bot/main.py --check          # 校验配置
uv run python hsys/crawl-bot/main.py                  # 默认 streak 策略
uv run python hsys/crawl-bot/main.py --strategy streak --min 5
uv run python hsys/crawl-bot/main.py --list           # 列出可用策略

# ── PG 冷数据归档（每月一次，可做成 Automation）──
docker exec hsys-pg hsys-archive 12

# ── Web 服务（P1 骨架）──
docker build -f hsys/server/Dockerfile -t hsys-server .
docker run -d --name hsys-server -p 7200:7200 \
  -v %cd%\hsys\crawl-bot\config.json:/app/hsys/crawl-bot/config.json:ro \
  hsys-server
```

## 路线图

- [x] crawl-bot 配置化启动（2026-07-22）
- [x] crawl-bot 单入口 + 策略制，与 scripts/ 解耦（2026-07-23）
- [x] PG 镜像（schema/分区/归档脚本/调优）（2026-07-23）
- [x] hsys-server 镜像（FastAPI 骨架 /healthz）（2026-07-23）
- [ ] **P0**：store_pg（asyncpg 后端）+ SQLite→PG 迁移脚本 + 采集切 PG
- [ ] **P1**：登录双密码 + /ingest + /ws + 实时首页（Vue）
- [ ] **P2**：四页迁移 + /api/query + ECharts 局内图
- [ ] **P3**：可配置看板 + 分析参数 UI 化
- [ ] **P4**：加固上互联网；viewer（stdlib）下线
