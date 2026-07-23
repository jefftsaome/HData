#!/bin/bash
# HSys events_raw 冷数据归档（在 PG 容器内执行）。
#
# 用法：docker exec hsys-pg hsys-archive [保留月数，默认 12]
#
# ① 滚动创建未来 3 个月的分区（每月跑一次永不断档）；
# ② 超出保留月数的分区：DETACH → pg_dump 自定义格式压缩归档到
#    /archive/events_raw-YYYY-MM.dump → DROP TABLE。
# 归档文件可用 pg_restore 回灌（见 postgres/README.md）。
set -euo pipefail

RETAIN="${1:-12}"
DB="${POSTGRES_DB:-hdata}"
DBUSER="${POSTGRES_USER:-hsys}"
OUT=/archive
mkdir -p "$OUT"
PSQL="psql -U $DBUSER -d $DB -v ON_ERROR_STOP=1 -tAc"
PSQLX="psql -U $DBUSER -d $DB -v ON_ERROR_STOP=1 -c"

# ── ① 滚动创建分区（当月 + 未来 3 个月）────────────────
for off in 0 1 2 3; do
    m=$(date -d "+$off month" +%Y-%m-01)
    pname=events_raw_$(date -d "$m" +%Y_%m)
    lo=$($PSQL "SELECT (EXTRACT(EPOCH FROM ('$m'::timestamp
                AT TIME ZONE 'Asia/Shanghai'))*1000)::bigint")
    hi=$($PSQL "SELECT (EXTRACT(EPOCH FROM (('$m'::date + interval '1 month')
                ::timestamp AT TIME ZONE 'Asia/Shanghai'))*1000)::bigint")
    $PSQLX "CREATE TABLE IF NOT EXISTS $pname PARTITION OF events_raw
            FOR VALUES FROM ($lo) TO ($hi)" >/dev/null
done
echo "[archive] 分区已确保（当月+未来3个月）"

# ── ② 超窗分区归档 ──────────────────────────────────────
cutoff=$(date -d "$RETAIN months ago" +%Y%m)   # 分区 YYYYMM 小于此值即超窗
parts=$($PSQL "SELECT inhrelid::regclass::text
               FROM pg_inherits
               WHERE inhparent = 'events_raw'::regclass ORDER BY 1")
archived=0
for p in $parts; do
    ym=$(echo "${p#events_raw_}" | tr -d '_')          # 2026_07 → 202607
    [ "$ym" -lt "$cutoff" ] || continue
    nice=$(echo "${p#events_raw_}" | tr '_' '-')       # 2026_07 → 2026-07
    rows=$($PSQL "SELECT COUNT(*) FROM $p")
    echo "[archive] $p ($rows 行) → $OUT/events_raw-$nice.dump"
    $PSQLX "ALTER TABLE events_raw DETACH PARTITION $p" >/dev/null
    pg_dump -U "$DBUSER" -d "$DB" -Fc -Z 9 -t "$p" \
            -f "$OUT/events_raw-$nice.dump"
    $PSQLX "DROP TABLE $p" >/dev/null
    archived=$((archived + 1))
done
[ "$archived" -eq 0 ] && echo "[archive] 无超窗分区（保留 ${RETAIN} 个月）" \
                      || echo "[archive] 完成，归档 $archived 个分区"
