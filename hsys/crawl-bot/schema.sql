-- ═══════════════════════════════════════════════════════════
-- HData 应用层 · 平台数据采集库表结构（SQLite）
-- 版本: v2（2026-07-19 多角度评审修订：+bet_points/boots/collect_runs，
--        rounds+ts_server，events_raw+source_account，user_version=2）
-- 用途: 牌局真相(rounds/round_bet_points/round_cards)
--       + 平台叙事(lobby_snapshots) + 原始留底(events_raw)
-- 分析目标: 分布检验 / 杀大赔小检验 / 好路信号回测 / 热度注水检验
-- ═══════════════════════════════════════════════════════════

PRAGMA journal_mode = WAL;        -- 读写不互锁：采集写的同时分析端可查
PRAGMA synchronous = NORMAL;      -- 性能与安全平衡
PRAGMA busy_timeout = 5000;       -- 避免偶发 database is locked
PRAGMA auto_vacuum = INCREMENTAL; -- events_raw 滚动删除后回收空间
PRAGMA user_version = 2;          -- schema 版本（应用启动校验/迁移依据）

-- ══ 表1：桌台元数据（10053 为准） ═══════════════════════
CREATE TABLE IF NOT EXISTS tables (
    table_id        INTEGER PRIMARY KEY,   -- 桌台ID（平台唯一）
    table_name      TEXT,                  -- 官方桌名，如 "经典百家乐J36"
    game_type_id    INTEGER,               -- 玩法ID（2001经典/2002极速…）
    game_type_name  TEXT,                  -- 官方玩法名（10053下发）
    casino_id       INTEGER,               -- 厅ID
    casino_name     TEXT,                  -- 厅名，如 "越南厅"
    physics_no      TEXT,                  -- 物理桌号 physicsTableNo
    first_seen      INTEGER,               -- 首次发现（本地毫秒）
    last_seen       INTEGER                -- 最近见到（本地毫秒）
);

-- ══ 表2：大厅采样（平台叙事的时间序列） ═════════════════
-- 每次采样每张桌一行；用于热度真实性、好路信号回测
CREATE TABLE IF NOT EXISTS lobby_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,      -- 采样时间（本地毫秒）
    table_id        INTEGER NOT NULL,
    online_number   INTEGER,               -- 展示在线人数（10052）
    total_amount    REAL,                  -- 展示桌总投注额（10052）
    game_status     INTEGER,               -- 2下注/3发牌/4结算
    boot_no         TEXT,                  -- 靴号（检测换靴清零）
    road_flat       TEXT,                  -- 当时全长珠盘 "BBPTB6…"
    good_roads      TEXT,                  -- 平台当时标记的好路 JSON，如 '["长庄"]'
    FOREIGN KEY (table_id) REFERENCES tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_lobby_ts    ON lobby_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_lobby_table ON lobby_snapshots(table_id, ts);

-- ══ 表3：牌局（核心，每局一行） ═════════════════════════
CREATE TABLE IF NOT EXISTS rounds (
    round_id        INTEGER PRIMARY KEY,   -- 平台局ID（全局唯一，天然去重）
    table_id        INTEGER NOT NULL,
    game_type_id    INTEGER,
    round_no        TEXT,                  -- 局号，如 "GJ3626719489"
    boot_no         TEXT,                  -- 靴号
    boot_index      INTEGER,               -- 靴内第几局（104）
    result          TEXT,                  -- B=庄赢 P=闲赢 T=和 B6=庄6点赢
    banker_points   INTEGER,               -- 庄点数（107 roundResult 前段）
    player_points   INTEGER,               -- 闲点数（107 roundResult 后段）
    road_flat_after TEXT,                  -- 结算后全长珠盘（含本局）
    good_roads      TEXT,                  -- 本局时刻平台标记的好路 JSON
    player_count    INTEGER,               -- 本局下注人数（110）
    total_amount    REAL,                  -- 本局总投注额（110）
    online_number   INTEGER,               -- 当时展示在线人数
    ts_bet_end      INTEGER,               -- 下注截止（104 countdownEndTime，服务端毫秒）
    ts_server       INTEGER,               -- 结算帧服务端时间（与 ts_settle 成对，验时钟异常）
    ts_settle       INTEGER,               -- 结算时间（本地收到107，毫秒）
    dealer_name     TEXT,                  -- 荷官（401快照）
    casino_id       INTEGER,               -- 厅ID
    FOREIGN KEY (table_id) REFERENCES tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_rounds_table  ON rounds(table_id, ts_settle);
CREATE INDEX IF NOT EXISTS idx_rounds_boot   ON rounds(boot_no);
CREATE INDEX IF NOT EXISTS idx_rounds_result ON rounds(result);
CREATE INDEX IF NOT EXISTS idx_rounds_dealer ON rounds(dealer_name);

-- ══ 表4：每局押注点（资金分布+派彩，杀大赔小检验核心） ═
-- 110 最后一帧的金额/人数 + 107 bootReport 的派彩，按押注点合并
CREATE TABLE IF NOT EXISTS round_bet_points (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id        INTEGER NOT NULL,
    bet_point_id    INTEGER NOT NULL,      -- 押注点ID（3001庄/3002闲/3013庄免佣…）
    bet_amount      REAL,                  -- 该点总投注额（110）
    bet_persons     INTEGER,               -- 该点投注人数（110）
    win_count       INTEGER,               -- 命中份数（107 bootReport）
    win_points      REAL,                  -- 实赔倍数（107 winPoints）
    FOREIGN KEY (round_id) REFERENCES rounds(round_id),
    UNIQUE (round_id, bet_point_id)
);
CREATE INDEX IF NOT EXISTS idx_rbp_point ON round_bet_points(bet_point_id, bet_amount);

-- ══ 表5：发牌明细（牌分布检验） ═════════════════════════
CREATE TABLE IF NOT EXISTS round_cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id        INTEGER NOT NULL,
    side            TEXT,                  -- B=庄 P=闲
    card_index      INTEGER,               -- 第几张（1/2/3）
    suit            TEXT,                  -- 花色（H/S/D/C）
    rank            TEXT,                  -- 牌面（A/2..10/J/Q/K）
    points          INTEGER,               -- 百家乐计点（10/J/Q/K=0）
    FOREIGN KEY (round_id) REFERENCES rounds(round_id)
);
CREATE INDEX IF NOT EXISTS idx_cards_round ON round_cards(round_id);
CREATE INDEX IF NOT EXISTS idx_cards_rank  ON round_cards(rank);

-- ══ 表6：原始事件留底（取证回溯，滚动保留30天） ══════════
CREATE TABLE IF NOT EXISTS events_raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,      -- 收到时间（本地毫秒）
    table_id        INTEGER,
    protocol_id     INTEGER NOT NULL,      -- 104/106/107/110/116/171…
    event_type      TEXT,                  -- round/card/bet/road/status…
    round_id        INTEGER,               -- 可关联则填，否则 NULL
    source_account  TEXT,                  -- 采集账号（多账号去重/审计）
    payload         TEXT NOT NULL          -- 原始 data JSON
);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events_raw(ts);
CREATE INDEX IF NOT EXISTS idx_events_round ON events_raw(round_id);
CREATE INDEX IF NOT EXISTS idx_events_table ON events_raw(table_id, ts);

-- ══ 表7：押注点字典（杀大赔小/赔率克扣检验的归一化基础） ═
-- 不同玩法的 bet_point_id 含义不同，分析 JOIN 此表，禁止硬编码
CREATE TABLE IF NOT EXISTS bet_points (
    bet_point_id    INTEGER NOT NULL,
    game_type_id    INTEGER NOT NULL,      -- 所属玩法（0=通用）
    side            TEXT,                  -- 归属：B=庄方 P=闲方 T=和 SIDE=侧注
    name            TEXT,                  -- 名称，如 "庄"/"闲"/"庄免佣"/"超级六"
    nominal_odds    REAL,                  -- 名义赔率（庄0.95/闲1/和8/超级六12…）
    PRIMARY KEY (bet_point_id, game_type_id)
);

-- ══ 表8：靴完整性汇总（分析时过滤残靴） ═════════════════
CREATE TABLE IF NOT EXISTS boots (
    boot_no         TEXT NOT NULL,
    table_id        INTEGER NOT NULL,
    first_ts        INTEGER,               -- 首局结算（本地毫秒）
    last_ts         INTEGER,               -- 末局结算
    round_count     INTEGER,               -- 已采局数
    complete        INTEGER DEFAULT 0,     -- 1=完整靴（boot_index 无断号）
    PRIMARY KEY (boot_no, table_id)
);

-- ══ 表9：采集运行记录（数据空洞审计/多账号 lineage） ════
CREATE TABLE IF NOT EXISTS collect_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account         TEXT,                  -- 采集账号
    layer           TEXT,                  -- L1大厅/L2进桌/L3作战
    tables_json     TEXT,                  -- 监控桌集合 JSON
    started_at      INTEGER,               -- 启动（本地毫秒）
    stopped_at      INTEGER,               -- 停止（NULL=运行中）
    note            TEXT
);
