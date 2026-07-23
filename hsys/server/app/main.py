"""HSys FastAPI 应用入口（P1 骨架，当前仅健康检查 + 静态托管）。

完整功能（登录双密码 / /ingest / /ws / Vue 页面）按
hsys/server/README.md 的里程碑推进。

环境变量：
  HSYS_CONFIG   配置文件路径（默认 ../crawl-bot/config.json，容器内
                通过 volume 挂载到 /app/hsys/crawl-bot/config.json）
  HSYS_PG_DSN   PostgreSQL DSN，如 postgresql://user:pass@host:5432/hdata
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent            # hsys/server/app
SERVER_DIR = APP_DIR.parent                          # hsys/server
HSYS_DIR = SERVER_DIR.parent                         # hsys

CONFIG_PATH = os.environ.get(
    "HSYS_CONFIG", str(HSYS_DIR / "crawl-bot" / "config.json"))

app = FastAPI(title="HSys", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    """容器/进程健康检查：配置与 PG DSN 只报有无，不泄露内容。"""
    return {
        "ok": True,
        "ts": int(time.time() * 1000),
        "config_mounted": Path(CONFIG_PATH).exists(),
        "pg_dsn_set": bool(os.environ.get("HSYS_PG_DSN")),
    }


# Vue 静态页（web/ 目录存在时挂载到根路径）
_web = SERVER_DIR / "web"
if _web.exists():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
