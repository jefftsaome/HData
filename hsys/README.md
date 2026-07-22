# HSys 分析平台

独立的数据采集 + 分析 + 实时展示平台。采集层复用 HData 包（`hdata/` 与
`scripts/streak_hunter.py`），本目录只放平台层的东西：配置、数据、
归档、Web 服务。

```
hsys/
├── README.md               ← 本文档（架构总览 + 运行手册）
├── crawl-bot/              ← 采集层
│   ├── hunter.py           ← 配置驱动启动器（复用 scripts/streak_hunter）
│   ├── archive.py          ← events_raw 月度归档（gzip NDJSON）
│   ├── config.example.json ← 配置模板（提交）
│   ├── config.json         ← 真实配置（.gitignore，严禁提交）
│   ├── data/               ← SQLite + 归档文件（.gitignore）
│   │   ├── streak.db
│   │   └── archive/events_raw-YYYY-MM.jsonl.gz
│   └── logs/               ← 采集日志（.gitignore）
└── server/                 ← Web 服务层（FastAPI，规划见 server/README.md）
```

## 架构

```
              ┌──────────── crawl-bot ────────────┐
  平台网站 ──WS──► hunter.py（登录/订阅/落库）      │
              │    ├─► data/streak.db（热窗）      │
              │    └─► archive.py 定期归档 ──► data/archive/*.jsonl.gz（永久）
              └────────┐
                       │ HTTP POST /ingest（push.enabled=true 后启用）
                       ▼
              ┌────── server（FastAPI，待开发）──────┐
              │  登录（双密码）→ 页面 / WebSocket 推送 │
              └──────────────┬───────────────────────┘
                             ▼
                        浏览器（局域网 3-5 人）
```

## 为什么不用 Redis

采集进程和 Web 服务跑在同一台机器上。推送链路就是"采集进程直接把事件
POST 给 Web 服务"——像记者直接打电话进广播站，广播站（Web 服务）再转述
给听众（浏览器 WebSocket）。3-5 个听众完全不需要中转仓库。

Redis 解决的是"生产者和消费者解耦 + 缓冲 + 多方订阅"。出现以下情况再加：

- 消费者变多（多个服务都要订阅同一事件流）；
- 需要事件缓冲/重放（Web 服务重启期间的事件不能丢）；
- 采集和 Web 分离部署到不同机器且需要可靠队列。

到时候把 crawl-bot 的 POST 目标从 Web 换成 Redis Stream 即可，采集侧
改动只有一处。

## 为什么继续用 SQLite（不换 PostgreSQL）

- 单机部署、单写多读：采集进程写，viewer/分析只读，WAL 模式互不阻塞；
- 3-5 个局域网用户，QPS 个位数，SQLite 绰绰有余；
- 零运维：没有服务进程要管，备份就是拷文件；
- 300-500 MB/天的大头是 events_raw，已用"热窗 + gzip 归档"解决，
  与数据库选型无关。

换 PostgreSQL 的触发条件：多机部署、多人同时在线 > 20、需要远程直连
SQL、或者要做跨机热备。届时分析表结构可直接平移。

## 数据保留策略（数据永久保存）

| 数据 | 位置 | 策略 |
|---|---|---|
| 派生分析表（streak_rounds / streak_episodes / lobby_snapshots 等） | streak.db | **永不清** |
| events_raw 热窗（≤7 天） | streak.db | 采集启动时清超窗部分（`purge_raw_days`） |
| events_raw 冷数据（>7 天） | data/archive/*.jsonl.gz | **永久保存**，gzip 约压到 1/6，每月 ~2-3 GB |
| 采集日志 | logs/hunter.log* | loguru 50 MB 滚动 |

⚠ 运行 `archive.py --export --delete` 必须在 `purge_raw_days` 到期之前，
否则超窗数据会被启动清理直接删掉、来不及归档。建议每 2-3 天跑一次，
或把 `purge_raw_days` 调大（磁盘换安全）。今后会把"先归档后清理"合并
成一条命令/定时任务。

## 胁迫密码删除范围（设计约定）

用户在 Web 端输入胁迫密码时，依次：先杀采集进程 → 删除以下内容：

1. `crawl-bot/data/` 整个目录（streak.db 及 wal/shm、archive/ 全部归档）；
2. `crawl-bot/logs/` 全部日志；
3. `crawl-bot/config.json`（含平台账号与打码 token）；
4. `config.example.json` 保留；代码与文档保留。

这是"停止采集 + 删所有日志 + 删数据库"的唯一自洽解释：留在机器上的
账号凭据与归档数据同样会暴露采集行为。若日后要保留归档另存他机，
在 server 实现时改成"先上传后删除"的一个开关即可。

## 局域网部署注意

- Web 服务绑定 `0.0.0.0`，Windows 防火墙放行端口（计划 7200）；
- 登录认证必须开启（双密码：正常 / 胁迫），口令哈希存储，不写明文；
- 日后上互联网前必须加：HTTPS、登录限流（防爆破）、胁迫接口单独
  的速率限制与审计。

## 运行手册

```bash
# 1. 校验配置（不启动）
uv run python hsys/crawl-bot/hunter.py --check

# 2. 启动采集（先停掉旧的 scripts/streak_hunter.py 进程！同一批账号
#    不能两边同时登录，会互踢）
uv run python hsys/crawl-bot/hunter.py

# 3. 归档（采集运行中可直接跑；默认干跑）
uv run python hsys/crawl-bot/archive.py
uv run python hsys/crawl-bot/archive.py --export --delete

# 4. 收缩库文件（须先停采集）
uv run python hsys/crawl-bot/archive.py --export --delete --vacuum
```

### 从旧路径迁移（一次性）

1. `Ctrl+C` 停掉正在跑的 `scripts/streak_hunter.py`（会优雅收尾）；
2. 移动库文件：
   `mv data/streak.db* hsys/crawl-bot/data/`（含 .db-wal/.db-shm）；
3. `uv run python hsys/crawl-bot/hunter.py --check` 确认配置；
4. `uv run python hsys/crawl-bot/hunter.py` 启动；
5. viewer 如需看新库：`python viewer/server.py --port 7100
   --db hsys/crawl-bot/data/streak.db`。

## 路线图

- [x] crawl-bot 配置化启动 + 归档
- [ ] server：FastAPI + 登录（双密码）+ 页面迁移 + WebSocket 推送
- [ ] crawl-bot → server /ingest 实时推送（config.push 预留）
- [ ] 归档合并进采集进程（先归档后清理一体化）
- [ ] 定时归档任务（Automation）
