-- ═══════════════════════════════════════════════════════════
-- HSys · 平台数据采集库表结构（PostgreSQL v1）
-- 移植自 SQLite docs/schema.sql v2 + streak v3（2026-07-23）
-- 约定：时间戳一律 BIGINT epoch 毫秒（UTC 基准，与采集端 time.time() 一致）；
--       金额 DOUBLE PRECISION；月分区边界按 Asia/Shanghai 自然月。
-- 差异说明：
--   ① events_raw 改为按月 RANGE 分区（ts），去掉代理主键 id
--      （追加型日志不需要；排序/定位用 ts+round_id）；
--   ② AUTOINCREMENT → GENERATED ALWAYS AS IDENTITY；
--   ③ 首次启动执行一次，后续改表走迁移（见 postgres/README.md）。
-- ═══════════════════════════════════════════════════════════

-- ══ 桌台元数据 ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS tables (
    table_id        BIGINT PRIMARY KEY,
    table_name      TEXT,
    game_type_id    INTEGER,
    game_type_name  TEXT,
    casino_id       INTEGER,
    casino_name     TEXT,
    physics_no      TEXT,
    first_seen      BIGINT,
    last_seen       BIGINT
);

-- ══ 大厅采样（平台叙事时间序列）═══════════════════════════
CREATE TABLE IF NOT EXISTS lobby_snapshots (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              BIGINT NOT NULL,
    table_id        BIGINT NOT NULL REFERENCES tables(table_id),
    online_number   INTEGER,
    total_amount    DOUBLE PRECISION,
    game_status     INTEGER,
    boot_no         TEXT,
    road_flat       TEXT,
    good_roads      TEXT
);
CREATE INDEX IF NOT EXISTS idx_lobby_ts    ON lobby_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_lobby_table ON lobby_snapshots(table_id, ts);

-- ══ 牌局（核心，每局一行）═════════════════════════════════
CREATE TABLE IF NOT EXISTS rounds (
    round_id        BIGINT PRIMARY KEY,
    table_id        BIGINT NOT NULL REFERENCES tables(table_id),
    game_type_id    INTEGER,
    round_no        TEXT,
    boot_no         TEXT,
    boot_index      INTEGER,
    result          TEXT,
    banker_points   INTEGER,
    player_points   INTEGER,
    road_flat_after TEXT,
    good_roads      TEXT,
    player_count    INTEGER,
    total_amount    DOUBLE PRECISION,
    online_number   INTEGER,
    ts_bet_end      BIGINT,
    ts_server       BIGINT,
    ts_settle       BIGINT,
    dealer_name     TEXT,
    casino_id       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rounds_table  ON rounds(table_id, ts_settle);
CREATE INDEX IF NOT EXISTS idx_rounds_boot   ON rounds(boot_no);
CREATE INDEX IF NOT EXISTS idx_rounds_result ON rounds(result);
CREATE INDEX IF NOT EXISTS idx_rounds_dealer ON rounds(dealer_name);

-- ══ 每局押注点（资金分布+派彩）════════════════════════════
CREATE TABLE IF NOT EXISTS round_bet_points (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id        BIGINT NOT NULL REFERENCES rounds(round_id),
    bet_point_id    INTEGER NOT NULL,
    bet_amount      DOUBLE PRECISION,
    bet_persons     INTEGER,
    win_count       INTEGER,
    win_points      DOUBLE PRECISION,
    UNIQUE (round_id, bet_point_id)
);
CREATE INDEX IF NOT EXISTS idx_rbp_point ON round_bet_points(bet_point_id, bet_amount);

-- ══ 发牌明细 ═════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS round_cards (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id        BIGINT NOT NULL REFERENCES rounds(round_id),
    side            TEXT,
    card_index      INTEGER,
    suit            TEXT,
    rank            TEXT,
    points          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cards_round ON round_cards(round_id);
CREATE INDEX IF NOT EXISTS idx_cards_rank  ON round_cards(rank);

-- ══ 原始事件留底（月分区：热窗在线，冷数据 detach 归档）════
CREATE TABLE IF NOT EXISTS events_raw (
    ts              BIGINT NOT NULL,
    table_id        BIGINT,
    protocol_id     INTEGER NOT NULL,
    event_type      TEXT,
    round_id        BIGINT,
    source_account  TEXT,
    payload         TEXT NOT NULL
) PARTITION BY RANGE (ts);

-- 首批月分区：2026-07 ~ 2027-06（之后由 hsys-archive 每月滚动创建）
DO $$
DECLARE
    m    date := DATE '2026-07-01';
    stop date := DATE '2027-07-01';
    lo   bigint;
    hi   bigint;
    pname text;
BEGIN
    WHILE m < stop LOOP
        lo := (EXTRACT(EPOCH FROM
              (m::timestamp AT TIME ZONE 'Asia/Shanghai')) * 1000)::bigint;
        hi := (EXTRACT(EPOCH FROM
              ((m + interval '1 month')::timestamp
               AT TIME ZONE 'Asia/Shanghai')) * 1000)::bigint;
        pname := format('events_raw_%s_%s', to_char(m, 'YYYY'),
                        to_char(m, 'MM'));
        EXECUTE format(
          'CREATE TABLE IF NOT EXISTS %I PARTITION OF events_raw
           FOR VALUES FROM (%s) TO (%s)', pname, lo, hi);
        m := (m + interval '1 month')::date;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_events_ts    ON events_raw(ts);
CREATE INDEX IF NOT EXISTS idx_events_round ON events_raw(round_id);
CREATE INDEX IF NOT EXISTS idx_events_table ON events_raw(table_id, ts);

-- ══ 押注点字典 ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS bet_points (
    bet_point_id    INTEGER NOT NULL,
    game_type_id    INTEGER NOT NULL,
    side            TEXT,
    name            TEXT,
    nominal_odds    DOUBLE PRECISION,
    PRIMARY KEY (bet_point_id, game_type_id)
);

-- ══ 靴完整性汇总 ═════════════════════════════════════════
CREATE TABLE IF NOT EXISTS boots (
    boot_no         TEXT NOT NULL,
    table_id        BIGINT NOT NULL,
    first_ts        BIGINT,
    last_ts         BIGINT,
    round_count     INTEGER,
    complete        INTEGER DEFAULT 0,
    PRIMARY KEY (boot_no, table_id)
);

-- ══ 采集运行记录 ═════════════════════════════════════════
CREATE TABLE IF NOT EXISTS collect_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account         TEXT,
    layer           TEXT,
    tables_json     TEXT,
    started_at      BIGINT,
    stopped_at      BIGINT,
    note            TEXT
);

-- ══ 长龙 episode（连胜事件）═══════════════════════════════
CREATE TABLE IF NOT EXISTS streak_episodes (
    episode_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_id        BIGINT NOT NULL,
    table_name      TEXT,
    game_type_id    INTEGER,
    side            TEXT NOT NULL,
    detected_via    TEXT,
    start_length    INTEGER,
    start_round_id  BIGINT,
    start_ts        BIGINT,
    end_round_id    BIGINT,
    end_ts          BIGINT,
    max_length      INTEGER,
    outcome         TEXT,
    account         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_table   ON streak_episodes(table_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_ep_outcome ON streak_episodes(outcome);

-- ══ 长龙局（连胜期间每局一行）═════════════════════════════
CREATE TABLE IF NOT EXISTS streak_rounds (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id      BIGINT NOT NULL REFERENCES streak_episodes(episode_id),
    round_id        BIGINT NOT NULL,
    ts_settle       BIGINT,
    streak_len_before INTEGER,
    result          TEXT,
    outcome         TEXT,
    banker_points   INTEGER,
    player_points   INTEGER,
    total_amount    DOUBLE PRECISION,
    player_count    INTEGER,
    online_number   INTEGER,
    bet_json        TEXT,
    payout_json     TEXT,
    UNIQUE (episode_id, round_id)
);
CREATE INDEX IF NOT EXISTS idx_sr_episode ON streak_rounds(episode_id);
