-- ═══════════════════════════════════════════════════════════
-- HSys · 平台数据采集库表结构（PostgreSQL v1.2）
-- 移植自 SQLite docs/schema.sql v2 + streak v3
-- 约定：时间戳一律 BIGINT epoch 毫秒（UTC 基准，与采集端 time.time() 一致）；
--       金额 DOUBLE PRECISION；月分区边界按 Asia/Shanghai 自然月。
-- 差异说明（相对 SQLite 版）：
--   ① events_raw 改为按月 RANGE 分区（ts），去掉代理主键 id
--      （追加型日志不需要；排序/定位用 ts+round_id）；
--   ② AUTOINCREMENT → GENERATED ALWAYS AS IDENTITY；
--   ③ 字段说明用 COMMENT ON 注册进数据库元数据（\d+ 表名 可见）；
--   ④ 本脚本只在数据卷为空的首次启动执行一次，后续改表走迁移；
--   ⑤ v1.2 字段类型按 SQLite 实际数据画像收紧（2026-07-23，
--      样本：rounds 4.25万 / lobby 122万 / events 85.6万 / bet_points 47.6万行）：
--      table_id BIGINT→INTEGER（实际 2064~2832）；枚举/小整数→SMALLINT
--      （实际范围见各列注释）；boots.complete → BOOLEAN；
--      金额保持 DOUBLE PRECISION（max 6025708.2 为8位有效数字，REAL 精度不够）。
-- 分析目标：分布检验 / 杀大赔小检验 / 好路信号回测 / 热度注水检验
-- ═══════════════════════════════════════════════════════════

-- ══ 桌台元数据（10053 为准） ═════════════════════════════
CREATE TABLE IF NOT EXISTS tables (
    table_id        INTEGER PRIMARY KEY,
    table_name      TEXT,
    game_type_id    SMALLINT,
    game_type_name  TEXT,
    casino_id       SMALLINT,
    casino_name     TEXT,
    physics_no      TEXT,
    first_seen      BIGINT,
    last_seen       BIGINT
);
COMMENT ON TABLE  tables IS '桌台元数据（以 10053 下发为准）';
COMMENT ON COLUMN tables.table_id       IS '桌台ID（平台唯一；实际样本 2064~2832）';
COMMENT ON COLUMN tables.table_name     IS '官方桌名，如 "经典百家乐J36"（样本 max 8 字符）';
COMMENT ON COLUMN tables.game_type_id   IS '玩法ID（2001经典/2002极速…；样本 2001~2038）';
COMMENT ON COLUMN tables.game_type_name IS '官方玩法名（10053下发）';
COMMENT ON COLUMN tables.casino_id      IS '厅ID（样本 3~13）';
COMMENT ON COLUMN tables.casino_name    IS '厅名，如 "越南厅"';
COMMENT ON COLUMN tables.physics_no     IS '物理桌号 physicsTableNo';
COMMENT ON COLUMN tables.first_seen     IS '首次发现（本地毫秒）';
COMMENT ON COLUMN tables.last_seen      IS '最近见到（本地毫秒）';

-- ══ 大厅采样（平台叙事的时间序列） ═══════════════════════
-- 每次采样每张桌一行；用于热度真实性、好路信号回测
CREATE TABLE IF NOT EXISTS lobby_snapshots (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              BIGINT NOT NULL,
    table_id        INTEGER NOT NULL REFERENCES tables(table_id),
    online_number   INTEGER,
    total_amount    DOUBLE PRECISION,
    game_status     SMALLINT,
    boot_no         TEXT,
    road_flat       TEXT,
    good_roads      TEXT
);
CREATE INDEX IF NOT EXISTS idx_lobby_ts    ON lobby_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_lobby_table ON lobby_snapshots(table_id, ts);
COMMENT ON TABLE  lobby_snapshots IS '大厅采样（平台叙事时间序列）：每次采样每桌一行，用于热度真实性/好路信号回测';
COMMENT ON COLUMN lobby_snapshots.id            IS '自增主键';
COMMENT ON COLUMN lobby_snapshots.ts            IS '采样时间（本地毫秒）';
COMMENT ON COLUMN lobby_snapshots.table_id      IS '桌台ID → tables';
COMMENT ON COLUMN lobby_snapshots.online_number IS '展示在线人数（10052；样本 max 2728，NULL 率 41.9%）';
COMMENT ON COLUMN lobby_snapshots.total_amount  IS '展示桌总投注额（10052；注意：当前样本全为 0，协议未下发有效值，列保留待观察）';
COMMENT ON COLUMN lobby_snapshots.game_status   IS '牌局状态：2下注/3发牌/4结算（样本 0~4，99.9% 为 4）';
COMMENT ON COLUMN lobby_snapshots.boot_no       IS '靴号（检测换靴清零；样本 max 23 字符）';
COMMENT ON COLUMN lobby_snapshots.road_flat     IS '当时全长珠盘，如 "BBPTB6…"（样本 max 113 字符）';
COMMENT ON COLUMN lobby_snapshots.good_roads    IS '平台当时标记的好路 JSON，如 ''["长庄"]''';

-- ══ 牌局（核心，每局一行） ═══════════════════════════════
CREATE TABLE IF NOT EXISTS rounds (
    round_id        BIGINT PRIMARY KEY,
    table_id        INTEGER NOT NULL REFERENCES tables(table_id),
    game_type_id    SMALLINT,
    round_no        TEXT,
    boot_no         TEXT,
    boot_index      SMALLINT,
    result          TEXT,
    banker_points   SMALLINT,
    player_points   SMALLINT,
    road_flat_after TEXT,
    good_roads      TEXT,
    player_count    INTEGER,
    total_amount    DOUBLE PRECISION,
    online_number   INTEGER,
    ts_bet_end      BIGINT,
    ts_server       BIGINT,
    ts_settle       BIGINT,
    dealer_name     TEXT,
    casino_id       SMALLINT
);
CREATE INDEX IF NOT EXISTS idx_rounds_table  ON rounds(table_id, ts_settle);
CREATE INDEX IF NOT EXISTS idx_rounds_boot   ON rounds(boot_no);
CREATE INDEX IF NOT EXISTS idx_rounds_result ON rounds(result);
CREATE INDEX IF NOT EXISTS idx_rounds_dealer ON rounds(dealer_name);
COMMENT ON TABLE  rounds IS '牌局真相（核心表，每局一行）';
COMMENT ON COLUMN rounds.round_id        IS '平台局ID（全局唯一，天然去重；样本 4.764e8~4.776e8 持续递增，保持 BIGINT）';
COMMENT ON COLUMN rounds.table_id        IS '桌台ID → tables';
COMMENT ON COLUMN rounds.game_type_id    IS '玩法ID（2001经典/2002极速…；样本 NULL 率 19.7%）';
COMMENT ON COLUMN rounds.round_no        IS '局号，如 "GJ3626719489"（样本 max 17 字符）';
COMMENT ON COLUMN rounds.boot_no         IS '靴号（样本 max 23 字符）';
COMMENT ON COLUMN rounds.boot_index      IS '靴内第几局（104；样本 1~104，NULL 率 1.9%）';
COMMENT ON COLUMN rounds.result          IS '结果：B=庄赢 P=闲赢 T=和 B6=庄6点赢（样本 B6 占 5.37%）';
COMMENT ON COLUMN rounds.banker_points   IS '庄点数（107 roundResult 前段；0~9）';
COMMENT ON COLUMN rounds.player_points   IS '闲点数（107 roundResult 后段；0~9）';
COMMENT ON COLUMN rounds.road_flat_after IS '结算后全长珠盘（含本局；样本 max 107 字符）';
COMMENT ON COLUMN rounds.good_roads      IS '本局时刻平台标记的好路 JSON';
COMMENT ON COLUMN rounds.player_count    IS '本局下注人数（110；样本 max 1581）';
COMMENT ON COLUMN rounds.total_amount    IS '本局总投注额（110；样本 max 6025708.2，必须 DOUBLE，REAL 精度不够）';
COMMENT ON COLUMN rounds.online_number   IS '当时展示在线人数（样本 max 2089，NULL 率 24.7%）';
COMMENT ON COLUMN rounds.ts_bet_end      IS '下注截止（104 countdownEndTime，服务端毫秒；NULL 率 1.9%）';
COMMENT ON COLUMN rounds.ts_server       IS '结算帧服务端时间（注意：当前采集端未填充，全 NULL，列保留）';
COMMENT ON COLUMN rounds.ts_settle       IS '结算时间（本地收到107，毫秒）';
COMMENT ON COLUMN rounds.dealer_name     IS '荷官（401快照；NULL 率 21.5%）';
COMMENT ON COLUMN rounds.casino_id       IS '厅ID（样本 3~13）';

-- ══ 每局押注点（资金分布+派彩，杀大赔小检验核心） ════════
-- 110 最后一帧的金额/人数 + 107 bootReport 的派彩，按押注点合并
CREATE TABLE IF NOT EXISTS round_bet_points (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id        BIGINT NOT NULL REFERENCES rounds(round_id),
    bet_point_id    SMALLINT NOT NULL,
    bet_amount      DOUBLE PRECISION,
    bet_persons     INTEGER,
    win_count       INTEGER,
    win_points      DOUBLE PRECISION,
    UNIQUE (round_id, bet_point_id)
);
CREATE INDEX IF NOT EXISTS idx_rbp_point ON round_bet_points(bet_point_id, bet_amount);
COMMENT ON TABLE  round_bet_points IS '每局押注点（资金分布+派彩，杀大赔小检验核心）：110 末帧金额/人数 + 107 bootReport 派彩';
COMMENT ON COLUMN round_bet_points.id           IS '自增主键';
COMMENT ON COLUMN round_bet_points.round_id     IS '平台局ID → rounds';
COMMENT ON COLUMN round_bet_points.bet_point_id IS '押注点ID（3001庄/3002闲/3013庄免佣…；样本 3001~5821 共 47 种）';
COMMENT ON COLUMN round_bet_points.bet_amount   IS '该点总投注额（110；样本 max 4188162.6）';
COMMENT ON COLUMN round_bet_points.bet_persons  IS '该点投注人数（110；样本 max 921）';
COMMENT ON COLUMN round_bet_points.win_count    IS '命中份数（107 bootReport；注意：当前采集端未填充，全 NULL，列保留）';
COMMENT ON COLUMN round_bet_points.win_points   IS '实赔倍数（107 winPoints；样本 0.5~191.0，NULL 率 53.6%=未命中）';

-- ══ 发牌明细（牌分布检验） ═══════════════════════════════
CREATE TABLE IF NOT EXISTS round_cards (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    round_id        BIGINT NOT NULL REFERENCES rounds(round_id),
    side            TEXT,
    card_index      SMALLINT,
    suit            TEXT,
    rank            TEXT,
    points          SMALLINT
);
CREATE INDEX IF NOT EXISTS idx_cards_round ON round_cards(round_id);
CREATE INDEX IF NOT EXISTS idx_cards_rank  ON round_cards(rank);
COMMENT ON TABLE  round_cards IS '发牌明细（牌分布检验）';
COMMENT ON COLUMN round_cards.id         IS '自增主键';
COMMENT ON COLUMN round_cards.round_id   IS '平台局ID → rounds';
COMMENT ON COLUMN round_cards.side       IS '归属方：B=庄 P=闲';
COMMENT ON COLUMN round_cards.card_index IS '第几张（1/2/3）';
COMMENT ON COLUMN round_cards.suit       IS '花色（H/S/D/C）';
COMMENT ON COLUMN round_cards.rank       IS '牌面（A/2..10/J/Q/K）';
COMMENT ON COLUMN round_cards.points     IS '百家乐计点（10/J/Q/K=0；0~9）';

-- ══ 原始事件留底（月分区：热窗在线，冷数据 detach 压缩归档） ═
CREATE TABLE IF NOT EXISTS events_raw (
    ts              BIGINT NOT NULL,
    table_id        INTEGER,
    protocol_id     SMALLINT NOT NULL,
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
COMMENT ON TABLE  events_raw IS '原始事件留底（取证回溯）：按月 RANGE 分区；热窗在线，超 12 个月分区 detach 压缩归档（hsys-archive）';
COMMENT ON COLUMN events_raw.ts             IS '收到时间（本地毫秒，分区键）';
COMMENT ON COLUMN events_raw.table_id       IS '桌台ID（样本 0~2832；0=非桌台事件）';
COMMENT ON COLUMN events_raw.protocol_id    IS '协议号（样本 102/104/106/107/110/116/123/171；文档另有 10052/10053 大厅协议）';
COMMENT ON COLUMN events_raw.event_type     IS '事件分类（样本 bet/card/round/status/road/notice/leave/kick）';
COMMENT ON COLUMN events_raw.round_id       IS '平台局ID（可关联则填，否则 NULL；NULL 率 3.0%）';
COMMENT ON COLUMN events_raw.source_account IS '采集账号（多账号去重/审计）';
COMMENT ON COLUMN events_raw.payload        IS '原始 data JSON（样本 max 7120 字符，平均 719）';

-- ══ 押注点字典（杀大赔小/赔率克扣检验的归一化基础） ══════
-- 不同玩法的 bet_point_id 含义不同，分析 JOIN 此表，禁止硬编码
CREATE TABLE IF NOT EXISTS bet_points (
    bet_point_id    SMALLINT NOT NULL,
    game_type_id    SMALLINT NOT NULL,
    side            TEXT,
    name            TEXT,
    nominal_odds    DOUBLE PRECISION,
    PRIMARY KEY (bet_point_id, game_type_id)
);
COMMENT ON TABLE  bet_points IS '押注点字典：不同玩法 bet_point_id 含义不同，分析 JOIN 此表，禁止硬编码（注意：SQLite 侧尚未填充，PG 初始化后需灌入）';
COMMENT ON COLUMN bet_points.bet_point_id IS '押注点ID';
COMMENT ON COLUMN bet_points.game_type_id IS '所属玩法（0=通用）';
COMMENT ON COLUMN bet_points.side         IS '归属：B=庄方 P=闲方 T=和 SIDE=侧注';
COMMENT ON COLUMN bet_points.name         IS '名称，如 "庄"/"闲"/"庄免佣"/"超级六"';
COMMENT ON COLUMN bet_points.nominal_odds IS '名义赔率（庄0.95/闲1/和8/超级六12…）';

-- ══ 靴完整性汇总（分析时过滤残靴） ═══════════════════════
CREATE TABLE IF NOT EXISTS boots (
    boot_no         TEXT NOT NULL,
    table_id        INTEGER NOT NULL,
    first_ts        BIGINT,
    last_ts         BIGINT,
    round_count     SMALLINT,
    complete        BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (boot_no, table_id)
);
COMMENT ON TABLE  boots IS '靴完整性汇总（分析时过滤残靴；注意：SQLite 侧尚未填充）';
COMMENT ON COLUMN boots.boot_no     IS '靴号';
COMMENT ON COLUMN boots.table_id    IS '桌台ID';
COMMENT ON COLUMN boots.first_ts    IS '首局结算（本地毫秒）';
COMMENT ON COLUMN boots.last_ts     IS '末局结算（本地毫秒）';
COMMENT ON COLUMN boots.round_count IS '已采局数（单靴约 60~80 局，SMALLINT 足够）';
COMMENT ON COLUMN boots.complete    IS 'TRUE=完整靴（boot_index 无断号）；v1.2 由 INTEGER 0/1 改为 BOOLEAN';

-- ══ 采集运行记录（数据空洞审计/多账号 lineage） ══════════
CREATE TABLE IF NOT EXISTS collect_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account         TEXT,
    layer           TEXT,
    tables_json     TEXT,
    started_at      BIGINT,
    stopped_at      BIGINT,
    note            TEXT
);
COMMENT ON TABLE  collect_runs IS '采集运行记录（数据空洞审计/多账号 lineage）';
COMMENT ON COLUMN collect_runs.run_id      IS '自增主键';
COMMENT ON COLUMN collect_runs.account     IS '采集账号（多账号为 "a+b+c" 拼接；样本 max 75 字符）';
COMMENT ON COLUMN collect_runs.layer       IS '采集层：L1大厅/L2进桌/L3作战';
COMMENT ON COLUMN collect_runs.tables_json IS '监控桌集合 JSON';
COMMENT ON COLUMN collect_runs.started_at  IS '启动时间（本地毫秒）';
COMMENT ON COLUMN collect_runs.stopped_at  IS '停止时间（本地毫秒；NULL=运行中）';
COMMENT ON COLUMN collect_runs.note        IS '备注';

-- ══ 长龙 episode（一条连胜事件一行：入场→反/删失） ═══════
CREATE TABLE IF NOT EXISTS streak_episodes (
    episode_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_id        INTEGER NOT NULL,
    table_name      TEXT,
    game_type_id    SMALLINT,
    side            TEXT NOT NULL,
    detected_via    TEXT,
    start_length    SMALLINT,
    start_round_id  BIGINT,
    start_ts        BIGINT,
    end_round_id    BIGINT,
    end_ts          BIGINT,
    max_length      SMALLINT,
    outcome         TEXT,
    account         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_table   ON streak_episodes(table_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_ep_outcome ON streak_episodes(outcome);
COMMENT ON TABLE  streak_episodes IS '长龙 episode：一条连胜事件一行（入场→反/删失）';
COMMENT ON COLUMN streak_episodes.episode_id     IS '自增主键';
COMMENT ON COLUMN streak_episodes.table_id       IS '桌台ID';
COMMENT ON COLUMN streak_episodes.table_name     IS '入场时桌名快照';
COMMENT ON COLUMN streak_episodes.game_type_id   IS '玩法ID';
COMMENT ON COLUMN streak_episodes.side           IS '连胜方向：B=庄 P=闲';
COMMENT ON COLUMN streak_episodes.detected_via   IS '发现来源：local_streak / good_roads（样本全为 good_roads）';
COMMENT ON COLUMN streak_episodes.start_length   IS '入场时已达连胜数（样本 5~14）';
COMMENT ON COLUMN streak_episodes.start_round_id IS '入场后第一局 round_id';
COMMENT ON COLUMN streak_episodes.start_ts       IS '入场时间（本地毫秒）';
COMMENT ON COLUMN streak_episodes.end_round_id   IS '结局局 round_id';
COMMENT ON COLUMN streak_episodes.end_ts         IS '结局时间（本地毫秒）';
COMMENT ON COLUMN streak_episodes.max_length     IS '最终达到的最大连胜数（样本 5~17）';
COMMENT ON COLUMN streak_episodes.outcome        IS '结局：broke=反了 / censored_boot=换靴 / censored_disconnect=掉线兜底或强杀遗留 / censored_network=我方网络 / censored_kick=疑似被踢且封锁 / censored_manual=人为退出 / NULL=进行中';
COMMENT ON COLUMN streak_episodes.account        IS '监控账号（多账号为 "a+b+c" 拼接；样本 max 75 字符）';

-- ══ 长龙局（连胜期间每局一行：协变量+结局标签） ══════════
CREATE TABLE IF NOT EXISTS streak_rounds (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id      BIGINT NOT NULL REFERENCES streak_episodes(episode_id),
    round_id        BIGINT NOT NULL,
    ts_settle       BIGINT,
    streak_len_before SMALLINT,
    result          TEXT,
    outcome         TEXT,
    banker_points   SMALLINT,
    player_points   SMALLINT,
    total_amount    DOUBLE PRECISION,
    player_count    INTEGER,
    online_number   INTEGER,
    bet_json        TEXT,
    payout_json     TEXT,
    UNIQUE (episode_id, round_id)
);
CREATE INDEX IF NOT EXISTS idx_sr_episode ON streak_rounds(episode_id);
COMMENT ON TABLE  streak_rounds IS '长龙局：连胜期间每局一行（协变量+结局标签）';
COMMENT ON COLUMN streak_rounds.id                IS '自增主键';
COMMENT ON COLUMN streak_rounds.episode_id        IS '所属 episode → streak_episodes';
COMMENT ON COLUMN streak_rounds.round_id          IS '平台局ID';
COMMENT ON COLUMN streak_rounds.ts_settle         IS '结算时间（本地毫秒）';
COMMENT ON COLUMN streak_rounds.streak_len_before IS '本局结果前的连胜长度（样本 5~16）';
COMMENT ON COLUMN streak_rounds.result            IS '结果：B/P/T/B6（T=和，不断也不算）';
COMMENT ON COLUMN streak_rounds.outcome           IS '标签：continue=同向或T / broke=反';
COMMENT ON COLUMN streak_rounds.banker_points     IS '庄点数（0~9）';
COMMENT ON COLUMN streak_rounds.player_points     IS '闲点数（0~9）';
COMMENT ON COLUMN streak_rounds.total_amount      IS '本局总投注（110；样本 max 6025708.2）';
COMMENT ON COLUMN streak_rounds.player_count      IS '本局下注人数（110）';
COMMENT ON COLUMN streak_rounds.online_number     IS '展示在线人数';
COMMENT ON COLUMN streak_rounds.bet_json          IS '110 jackpotPoolInfos 原文（样本 max 2070 字符）';
COMMENT ON COLUMN streak_rounds.payout_json       IS '107 bootReport 原文（样本 max 1594 字符）';
