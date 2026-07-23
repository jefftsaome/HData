# HSys PostgreSQL 镜像（采集数据存储）
#
# 构建（上下文 = 本目录）：
#   docker build -t hsys-pg hsys/server/postgres
#
# 首次运行（命名卷持久化 + 归档目录 bind mount + 只对本机开端口）：
#   docker volume create hsys-pgdata
#   docker run -d --name hsys-pg -p 127.0.0.1:5432:5432 \
#     -e POSTGRES_USER=hsys -e POSTGRES_PASSWORD=<强密码> -e POSTGRES_DB=hdata \
#     -v hsys-pgdata:/var/lib/postgresql/data \
#     -v %cd%\hsys\crawl-bot\data\archive:/archive \
#     --restart unless-stopped \
#     hsys-pg
#
# 注意：POSTGRES_* 环境变量与 initdb 脚本只在数据卷为空的首次启动生效；
# 改密码用 ALTER USER，改 schema 用 psql 手动执行或后续迁移工具。

FROM postgres:16-bookworm

ENV TZ=Asia/Shanghai

# 调优配置（写多读多、单表大体量 events_raw）
COPY postgresql.conf /etc/postgresql/postgresql.conf

# 首次启动自动建库建表（含 events_raw 月分区）
COPY initdb/ /docker-entrypoint-initdb.d/

# 冷数据归档：超窗月分区 detach → pg_dump 压缩 → DROP
COPY archive.sh /usr/local/bin/hsys-archive
RUN chmod +x /usr/local/bin/hsys-archive

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
