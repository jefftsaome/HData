# HSys Web 服务镜像（FastAPI + uvicorn）
#
# 构建（必须在仓库根目录执行，构建上下文 = 仓库根）：
#   docker build -f hsys/server/Dockerfile -t hsys-server .
#
# 运行（config.json 含敏感信息，运行时挂载，绝不打进镜像）：
#   docker run -d --name hsys-server -p 7200:7200 ^
#     -v %cd%\hsys\crawl-bot\config.json:/app/hsys/crawl-bot/config.json:ro ^
#     -e HSYS_PG_DSN=postgresql://user:pass@host.docker.internal:5432/hdata ^
#     hsys-server
#   （Linux/macOS 把 %cd% 换成 $(pwd)，^ 换成 \）
#
# 健康检查：http://localhost:7200/healthz

# ── 构建层：安装依赖（锁文件复现）────────────────────────
FROM python:3.13.5-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# htools 依赖来自 git 仓库，uv 拉取需要 git
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷依赖清单：锁文件不变则此层命中缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group server --no-install-workspace

# 再拷源码，安装工作区包（hdata）
COPY hdata ./hdata
COPY hsys/server ./hsys/server
RUN uv sync --locked --no-dev --group server

# ── 运行层：不含 git/uv/构建缓存 ─────────────────────────
FROM python:3.13.5-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    HSYS_CONFIG=/app/hsys/crawl-bot/config.json

WORKDIR /app
RUN useradd --system --uid 10001 --create-home hsys \
 && mkdir -p /app/hsys/crawl-bot \
 && chown -R hsys:hsys /app
COPY --from=builder --chown=hsys:hsys /app /app

USER hsys
EXPOSE 7200

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:7200/healthz', timeout=4)"]

CMD ["uvicorn", "hsys.server.app.main:app", "--host", "0.0.0.0", "--port", "7200"]
