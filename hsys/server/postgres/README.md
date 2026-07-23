# HSys PostgreSQL（采集数据存储）

PG 16 容器，存 crawl-bot 采集的全部数据。当前部署在本机 Windows Docker；
后续扩大规模可整体迁到独立存储服务器（见末节）。

## 快速开始

```bash
# 构建（上下文 = 本目录）
docker build -t hsys-pg hsys/server/postgres

# 首次运行（命名卷持久化；端口只绑本机回环）
docker volume create hsys-pgdata
docker run -d --name hsys-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=hsys -e POSTGRES_PASSWORD=<强密码> -e POSTGRES_DB=hdata \
  -v hsys-pgdata:/var/lib/postgresql/data \
  -v %cd%\hsys\crawl-bot\data\archive:/archive \
  --restart unless-stopped \
  hsys-pg
```

- `POSTGRES_*` 与 `initdb/` 脚本**只在数据卷为空的首次启动执行**；
  之后改密码用 `ALTER USER`，改表结构用 psql 手动执行（或后续引入
  迁移工具）。
- 采集/分析从宿主机连：`postgresql://hsys:<密码>@127.0.0.1:5432/hdata`；
  hsys-server 容器连：两容器加同一 network（`docker network create
  hsys-net`，运行时 `--network hsys-net`），DSN 主机名写 `hsys-pg`。
- `/archive` bind mount 到 `crawl-bot/data/archive/`，归档文件落地即入
  现有数据目录（gitignore 已覆盖）。

## 数据压缩问题（明确回答）

**PostgreSQL 核心没有 MySQL 那样的整表压缩**，但有三层可用手段，
本项目的压缩策略如下：

| 层 | 手段 | 本项目采用 |
|---|---|---|
| 行内大字段 | TOAST 压缩（默认 pglz，本镜像改 `lz4` 更快） | ✅ 已开启，events_raw.payload 自动受益 |
| 超 1 年冷数据 | **月分区 DETACH + `pg_dump -Fc -Z9` 压缩归档**（JSON 文本实测约 5-10 倍压缩率） | ✅ 本方案，`hsys-archive` 实现 |
| 压缩后仍要在线查 | TimescaleDB 扩展的 `compress_chunk`（列式，~90%+） | ❌ 暂缓：引入扩展依赖 + Timescale License（免费可用但非 OSI 开源），数据量到 TB 级再评估 |

即：**热数据（近 12 个月）留 PG 在线可查；超 1 年的数据压缩成 dump
文件永久保存**，需要时回灌。这与"数据永久保存"的要求一致——压缩的
是存储形态，不是删除。

WAL 也已开压缩（`wal_compression=on`），日常 IO 进一步降低。

## 归档操作（每月一次，可做成 Automation 定时任务）

```bash
docker exec hsys-pg hsys-archive 12    # 保留 12 个月热数据
```

脚本自动：① 滚动创建未来 3 个月分区（建议每月上旬运行一次）；
② 超窗分区 DETACH → 压缩 dump 到 `/archive/events_raw-YYYY-MM.dump`
→ DROP。

**回灌某月**（表已 DROP，恢复为新表再挂回或直接查）：

```bash
# 恢复为普通表 events_raw_2026_07 直接查询
docker exec -i hsys-pg pg_restore -U hsys -d hdata \
  /archive/events_raw-2026-07.dump
# 想重新挂回分区（可选，需先确认分区键范围无冲突）：
docker exec -i hsys-pg psql -U hsys -d hdata -c \
  "ALTER TABLE events_raw ATTACH PARTITION events_raw_2026_07
   FOR VALUES FROM (<lo>) TO (<hi>)"
```

## SQLite 历史数据迁移（P0 待做）

`scripts/migrate_sqlite_to_pg.py`（下一步实现）：派生分析表
（rounds / streak_* / lobby_snapshots / tables 等）全量搬迁 +
events_raw 最近 30 天；更早的 events_raw 已在
`data/archive/*.jsonl.gz`，按需回灌。旧 `data/streak.db` 迁移后保留
为只读备份。

## 扩大到独立存储服务器的路径

现在：本机 Docker 命名卷 `hsys-pgdata`。未来买独立存储服务器后：

1. **平滑方式（推荐）**：新服务器跑同款 hsys-pg 容器，搭流复制
   （streaming replication）做从库，追平后提升为主库，切换窗口
   分钟级；crawl-bot / server 只改 DSN 主机地址。
2. **简单方式**：`pg_dump -Fc` 全库导出 → 新服务器 `pg_restore`，
   停机窗口约等于导出+导入时间（当前数据量小时级）。
3. 归档 dump 文件（/archive）直接 rsync/拷贝到新机器，与库无关。
4. 存储服务器只需对采集机/分析机开放 5432（防火墙白名单），
   不暴露公网。

## 目录

```
postgres/
├── Dockerfile           ← postgres:16 + 配置 + initdb + 归档脚本
├── postgresql.conf      ← 写多读多调优（WAL压缩/lz4/慢查询日志）
├── initdb/
│   └── 01_schema.sql    ← 全量建表 + events_raw 月分区（首启执行）
├── archive.sh           ← hsys-archive：分区滚动创建+超窗压缩归档
└── README.md            ← 本文档
```
