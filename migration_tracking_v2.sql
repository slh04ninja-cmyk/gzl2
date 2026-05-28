-- =============================================================
-- Migration Tracking V2 — Ajout colonnes enrichies
-- Exécuter dans Supabase SQL Editor
-- =============================================================

-- Ajouter les nouvelles colonnes à la table trades
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_type TEXT DEFAULT 'UNKNOWN';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS risk_reward REAL DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS r_multiple REAL DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS max_drawdown REAL DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp_hit TEXT;

-- Index pour les nouvelles colonnes
CREATE INDEX IF NOT EXISTS idx_trades_signal_type ON trades(signal_type);

-- =============================================================
-- Vue enrichie par canal
-- =============================================================
CREATE OR REPLACE VIEW canal_stats_enriched AS
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
    ROUND(AVG(risk_reward) FILTER (WHERE risk_reward > 0)::numeric, 2) as avg_rr,
    ROUND(AVG(r_multiple) FILTER (WHERE r_multiple != 0)::numeric, 2) as avg_r_multiple,
    ROUND(MAX(max_drawdown)::numeric, 2) as max_drawdown,
    ROUND(MAX(pnl)::numeric, 2) as best_trade,
    ROUND(MIN(pnl)::numeric, 2) as worst_trade,
    -- Répartition par type de signal
    COUNT(*) FILTER (WHERE signal_type = 'CAS1') as cas1_count,
    COUNT(*) FILTER (WHERE signal_type = 'CAS2') as cas2_count,
    COUNT(*) FILTER (WHERE signal_type = 'PU') as pu_count,
    -- Win rate par type
    ROUND(
        CASE
            WHEN COUNT(*) FILTER (WHERE signal_type = 'CAS1' AND result IN ('WIN','LOSS','BE')) > 0
            THEN (COUNT(*) FILTER (WHERE signal_type = 'CAS1' AND result = 'WIN')::float /
                  COUNT(*) FILTER (WHERE signal_type = 'CAS1' AND result IN ('WIN','LOSS','BE')) * 100)
            ELSE 0
        END::numeric, 1
    ) as cas1_win_rate,
    ROUND(
        CASE
            WHEN COUNT(*) FILTER (WHERE signal_type = 'CAS2' AND result IN ('WIN','LOSS','BE')) > 0
            THEN (COUNT(*) FILTER (WHERE signal_type = 'CAS2' AND result = 'WIN')::float /
                  COUNT(*) FILTER (WHERE signal_type = 'CAS2' AND result IN ('WIN','LOSS','BE')) * 100)
            ELSE 0
        END::numeric, 1
    ) as cas2_win_rate,
    ROUND(
        CASE
            WHEN COUNT(*) FILTER (WHERE signal_type = 'PU' AND result IN ('WIN','LOSS','BE')) > 0
            THEN (COUNT(*) FILTER (WHERE signal_type = 'PU' AND result = 'WIN')::float /
                  COUNT(*) FILTER (WHERE signal_type = 'PU' AND result IN ('WIN','LOSS','BE')) * 100)
            ELSE 0
        END::numeric, 1
    ) as pu_win_rate
FROM trades
WHERE result != 'OPEN'
GROUP BY canal
ORDER BY total_pnl DESC;

-- =============================================================
-- Vue par heure de la journée
-- =============================================================
CREATE OR REPLACE VIEW hourly_stats AS
SELECT
    EXTRACT(HOUR FROM opened_at AT TIME ZONE 'UTC') as hour_utc,
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE result = 'WIN') as wins,
    COUNT(*) FILTER (WHERE result = 'LOSS') as losses,
    ROUND(SUM(pnl)::numeric, 2) as total_pnl,
    ROUND(
        CASE
            WHEN COUNT(*) FILTER (WHERE result IN ('WIN','LOSS','BE')) > 0
            THEN (COUNT(*) FILTER (WHERE result = 'WIN')::float /
                  COUNT(*) FILTER (WHERE result IN ('WIN','LOSS','BE')) * 100)
            ELSE 0
        END::numeric, 1
    ) as win_rate
FROM trades
WHERE result != 'OPEN'
GROUP BY EXTRACT(HOUR FROM opened_at AT TIME ZONE 'UTC')
ORDER BY hour_utc;
