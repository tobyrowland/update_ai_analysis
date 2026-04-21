-- Migration 003: Benchmark portfolios (S&P 500, MSCI World)
--
-- Adds two tables for tracking passive-index benchmarks that appear on
-- the /leaderboard alongside agent portfolios. Benchmarks are neither
-- equities (no screener pollution) nor agents (no trade journal / no
-- $1M cash account) — they're a third kind of "portfolio" valued via
-- the ratio of today's close to the inception close.
--
-- Also upgrades the agent_leaderboard view to expose pnl_pct_30d so
-- the leaderboard can rank on a rolling 30-day window (fairer than
-- since-inception given varying agent inception dates).
--
-- Paste-and-run in the Supabase SQL editor. Idempotent — safe to
-- re-run.

-- ---------------------------------------------------------------------------
-- 1. Benchmarks metadata + daily closes
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS benchmarks (
    ticker                  TEXT PRIMARY KEY,               -- e.g. 'SPY.US', 'URTH.US'
    display_name            TEXT NOT NULL,                  -- e.g. 'S&P 500 (SPY)'
    inception_date          DATE NOT NULL,                  -- matches the earliest agent inception
    inception_price         NUMERIC(14,4) NOT NULL,         -- adjusted close on inception_date
    latest_price            NUMERIC(14,4),                  -- adjusted close at last update
    latest_price_date       DATE,
    notional_starting_cash  NUMERIC(14,2) NOT NULL DEFAULT 1000000,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS benchmark_prices (
    ticker       TEXT NOT NULL REFERENCES benchmarks(ticker) ON DELETE CASCADE,
    price_date   DATE NOT NULL,
    close        NUMERIC(14,4) NOT NULL,                    -- adjusted close from EODHD
    PRIMARY KEY (ticker, price_date)
);

CREATE INDEX IF NOT EXISTS idx_bench_prices_date ON benchmark_prices (price_date DESC);


-- ---------------------------------------------------------------------------
-- 2. agent_leaderboard view — add pnl_pct_30d column
--
-- Computed by joining the latest snapshot against the most recent snapshot
-- on or before (CURRENT_DATE - 30). Agents with less than 30 days of
-- history get NULL, which sorts last on the leaderboard.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW agent_leaderboard AS
WITH latest AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        snapshot_date,
        cash_usd,
        holdings_value_usd,
        total_value_usd,
        pnl_usd,
        pnl_pct,
        num_positions
    FROM agent_portfolio_history
    ORDER BY agent_id, snapshot_date DESC
),
thirty_days_ago AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_30d_ago
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '30 days'
    ORDER BY agent_id, snapshot_date DESC
)
SELECT
    a.handle,
    a.display_name,
    a.is_house_agent,
    l.snapshot_date,
    l.cash_usd,
    l.holdings_value_usd,
    l.total_value_usd,
    l.pnl_usd,
    l.pnl_pct,
    l.num_positions,
    CASE
        WHEN t.value_30d_ago IS NULL OR t.value_30d_ago = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - t.value_30d_ago) / t.value_30d_ago) * 100, 4)
    END AS pnl_pct_30d
FROM latest l
JOIN agents a ON a.id = l.agent_id
LEFT JOIN thirty_days_ago t ON t.agent_id = l.agent_id
ORDER BY l.pnl_pct DESC;
