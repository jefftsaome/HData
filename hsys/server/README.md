# HSys Web 服务层（规划中）

FastAPI + uvicorn，局域网 3-5 用户。本目录目前只有本规划文档。

## 已定决策

- **框架**：FastAPI + uvicorn，Python 3.13，与仓库共用 .venv
  （新增依赖：fastapi、uvicorn、itsdangerous 或 PyJWT——提交时再加入
  pyproject）。
- **数据库**：直连 `../crawl-bot/data/streak.db`（WAL 只读模式），
  与采集进程同机共享，无需额外同步。
- **实时推送**：crawl-bot 把事件 HTTP POST 到 `/ingest`，server 内部
  广播给所有 WebSocket 连接（见 ../README.md "为什么不用 Redis"）。
- **页面**：viewer/ 下四个页面（长龙列表 /analysis /heat /strategy-od）
  逐步迁移为 FastAPI 模板或静态页，API 从 viewer/server.py 平移。

## 登录：双密码设计

每个用户配两个密码，哈希存储（不同盐）：

| 输入 | 行为 |
|---|---|
| 正常密码 | 正常登录，进入系统 |
| 胁迫密码 | 界面表现与正常登录**完全一致**（同页面、同数据），后台静默：<br>① 终止采集进程（hunter.py）<br>② 删除 `crawl-bot/data/`（含归档）、`crawl-bot/logs/`、`crawl-bot/config.json`（范围见 ../README.md）<br>③ 删除动作异步执行，登录响应延迟与正常登录无差别<br>④ 服务器日志中胁迫登录与正常登录**无差别记录**（不留下"触发了销毁"的痕迹） |

实现要点：校验先比对正常密码哈希，不命中再比对胁迫密码哈希，两条
路径的时间差要抹平（统一固定耗时，如统一 sleep 到 300ms 再响应）。

## 规划路由

```
POST /login            双密码登录（签发 session cookie）
POST /logout
GET  /                 长龙实时列表（WebSocket 推送更新）
GET  /analysis         断龙分析
GET  /heat             平台热度
GET  /strategy-od      反方金额下降七条件策略页
POST /ingest           采集事件接收（仅本机/内网，带共享密钥）
WS   /ws               前端实时推送
GET  /api/...          viewer 现有 API 平移
```

## 部署（局域网）

```bash
uvicorn hsys.server.main:app --host 0.0.0.0 --port 7200
```

Windows 防火墙放行 7200；登录必须开启。上互联网前必须补：HTTPS、
登录限流、/ingest 密钥轮换。

## 里程碑

1. 骨架：FastAPI + 登录（双密码）+ 一个空仪表盘页
2. /ingest + /ws 推送链路打通，crawl-bot `push.enabled=true`
3. 迁移 viewer 四页
4. 加固：限流、HTTPS、审计
