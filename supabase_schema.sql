-- =============================================================
-- TradingBot V4 — Schéma Supabase
-- =============================================================

CREATE TABLE IF NOT EXISTS sessions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    started_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ,
    runtime_minutes INT DEFAULT 0,
    mode TEXT DEFAULT 'DEMO',
    channels TEXT[] DEFAULT '{}',
    lot_size REAL DEFAULT 0.01,
    total_trades INT DEFAULT 0,
    total_pnl REAL DEFAULT 0,
    status TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS trades (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    opened_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ,
    canal TEXT DEFAULT 'Inconnu',
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    zone_low REAL,
    zone_high REAL,
    sl REAL,
    tps REAL[] DEFAULT '{}',
    tp_count INT DEFAULT 0,
    tp_final REAL,
    entry_price REAL,
    lot_size REAL DEFAULT 0.01,
    result TEXT DEFAULT 'OPEN',
    pnl REAL DEFAULT 0,
    duree_min REAL DEFAULT 0,
    tickets BIGINT[] DEFAULT '{}',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    trade_id UUID REFERENCES trades(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    event_type TEXT NOT NULL,
    details JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_trades_canal ON trades(canal);
CREATE INDEX IF NOT EXISTS idx_trades_result ON trades(result);
CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_trade ON events(trade_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

CREATE OR REPLACE VIEW canal_stats AS
SELECT
    canal,
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE result = 'WIN') as wins,
    COUNT(*) FILTER (WHERE result = 'LOSS') as losses,
    COUNT(*) FILTER (WHERE result = 'BE') as breakevens,
    COUNT(*) FILTER (WHERE result = 'OPEN') as still_open,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl,
    ROUND(AVG(pnl) FILTER (WHERE result != 'OPEN')::numeric, 2) as avg_pnl,
    ROUND(
        CASE
            WHEN COUNT(*) FILTER (WHERE result IN ('WIN','LOSS','BE')) > 0
            THEN (COUNT(*) FILTER (WHERE result = 'WIN')::float /
                  COUNT(*) FILTER (WHERE result IN ('WIN','LOSS','BE')) * 100)
            ELSE 0
        END::numeric, 1
    ) as win_rate,
    ROUND(
        CASE
            WHEN SUM(pnl) FILTER (WHERE pnl < 0) < 0
            THEN ABS(SUM(pnl) FILTER (WHERE pnl > 0)) /
                 ABS(SUM(pnl) FILTER (WHERE pnl < 0))
            ELSE 0
        END::numeric, 2
    ) as profit_factor,
    ROUND(MAX(pnl)::numeric, 2) as best_trade,
    ROUND(MIN(pnl)::numeric, 2) as worst_trade
FROM trades
WHERE result != 'OPEN'
GROUP BY canal
ORDER BY total_pnl DESC;

CREATE OR REPLACE VIEW session_stats AS
SELECT
    s.id,
    s.started_at,
    s.ended_at,
    s.mode,
    s.lot_size,
    s.status,
    COUNT(t.id) as total_trades,
    COUNT(t.id) FILTER (WHERE t.result = 'WIN') as wins,
    COUNT(t.id) FILTER (WHERE t.result = 'LOSS') as losses,
    ROUND(SUM(t.pnl)::numeric, 2) as total_pnl,
    ROUND(
        CASE
            WHEN COUNT(t.id) FILTER (WHERE t.result IN ('WIN','LOSS')) > 0
            THEN (COUNT(t.id) FILTER (WHERE t.result = 'WIN')::float /
                  COUNT(t.id) FILTER (WHERE t.result IN ('WIN','LOSS')) * 100)
            ELSE 0
        END::numeric, 1
    ) as win_rate
FROM sessions s
LEFT JOIN trades t ON t.session_id = s.id
GROUP BY s.id
ORDER BY s.started_at DESC;

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all on sessions" ON sessions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all on trades" ON trades FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all on events" ON events FOR ALL USING (true) WITH CHECK (true);
