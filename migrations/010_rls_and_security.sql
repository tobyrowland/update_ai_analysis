-- Migration 010: Enable RLS, fix SECURITY DEFINER view, pin function search_path
--
-- Resolves the Supabase Security Advisor warnings:
--   1. "RLS Disabled in Public" on all data tables.
--   2. "Security Definer View" on agent_leaderboard.
--   3. "Function Search Path Mutable" on three pgsql functions.
--
-- Backwards compatible. The pipeline (db.py) and the web app (web/lib/supabase.ts)
-- both connect with the service-role key, which bypasses RLS — so enabling RLS
-- here is purely additive: nothing breaks, but the anon key is now safe to ship
-- to a browser without exposing the entire database.
--
-- Public SELECT policies are added everywhere because the leaderboard / equity
-- detail pages are intended to be viewable without auth. Writes have NO policy,
-- so only the service-role key can insert/update/delete.
--
-- Paste-and-run in the Supabase SQL editor. Idempotent.

-- ============================================================
-- 1. Enable RLS + public-read policies on every data table
-- ============================================================

-- Helper: re-running this migration must not error if the policy already
-- exists. We DROP IF EXISTS then CREATE, which is idempotent under any
-- supported Postgres version.

ALTER TABLE companies                ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_sales              ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_logs                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_accounts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_holdings           ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_trades             ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_portfolio_history  ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_heartbeats         ENABLE ROW LEVEL SECURITY;
ALTER TABLE benchmarks               ENABLE ROW LEVEL SECURITY;
ALTER TABLE benchmark_prices         ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read" ON companies;
CREATE POLICY "public read" ON companies               FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON price_sales;
CREATE POLICY "public read" ON price_sales             FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON run_logs;
CREATE POLICY "public read" ON run_logs                FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON agents;
CREATE POLICY "public read" ON agents                  FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON agent_accounts;
CREATE POLICY "public read" ON agent_accounts          FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON agent_holdings;
CREATE POLICY "public read" ON agent_holdings          FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON agent_trades;
CREATE POLICY "public read" ON agent_trades            FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON agent_portfolio_history;
CREATE POLICY "public read" ON agent_portfolio_history FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON agent_heartbeats;
CREATE POLICY "public read" ON agent_heartbeats        FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON benchmarks;
CREATE POLICY "public read" ON benchmarks              FOR SELECT USING (true);

DROP POLICY IF EXISTS "public read" ON benchmark_prices;
CREATE POLICY "public read" ON benchmark_prices        FOR SELECT USING (true);


-- ============================================================
-- 2. Recreate agent_leaderboard with security_invoker = true
-- ============================================================
-- A SECURITY DEFINER view runs with the *creator's* permissions and
-- bypasses the caller's RLS. security_invoker = true (Postgres 15+,
-- which Supabase runs) makes the view respect the caller's RLS instead.
-- The view body is unchanged from migration 009.

DROP VIEW IF EXISTS agent_leaderboard;

CREATE VIEW agent_leaderboard
WITH (security_invoker = true) AS
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
first_snapshot AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    ORDER BY agent_id, snapshot_date ASC
),
one_day_ago AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '1 day'
    ORDER BY agent_id, snapshot_date DESC
),
thirty_days_ago AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '30 days'
    ORDER BY agent_id, snapshot_date DESC
),
year_start AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date >= DATE_TRUNC('year', CURRENT_DATE)::DATE
    ORDER BY agent_id, snapshot_date ASC
),
one_year_ago AS (
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '1 year'
    ORDER BY agent_id, snapshot_date DESC
),
sharpe_returns AS (
    SELECT
        agent_id,
        (total_value_usd - LAG(total_value_usd) OVER w)
            / NULLIF(LAG(total_value_usd) OVER w, 0) AS daily_return
    FROM agent_portfolio_history
    WHERE EXTRACT(DOW FROM snapshot_date) BETWEEN 1 AND 5
    WINDOW w AS (PARTITION BY agent_id ORDER BY snapshot_date)
),
sharpe AS (
    SELECT
        agent_id,
        AVG(daily_return)         AS mean_return,
        STDDEV_SAMP(daily_return) AS stdev_return,
        COUNT(daily_return)       AS n_returns
    FROM sharpe_returns
    WHERE daily_return IS NOT NULL
    GROUP BY agent_id
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
        WHEN COALESCE(t1d.value_anchor, tfirst.value_anchor) IS NULL
          OR COALESCE(t1d.value_anchor, tfirst.value_anchor) = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - COALESCE(t1d.value_anchor, tfirst.value_anchor))
                    / COALESCE(t1d.value_anchor, tfirst.value_anchor)) * 100, 4)
    END AS pnl_pct_1d,
    CASE
        WHEN COALESCE(t30.value_anchor, tfirst.value_anchor) IS NULL
          OR COALESCE(t30.value_anchor, tfirst.value_anchor) = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - COALESCE(t30.value_anchor, tfirst.value_anchor))
                    / COALESCE(t30.value_anchor, tfirst.value_anchor)) * 100, 4)
    END AS pnl_pct_30d,
    CASE
        WHEN COALESCE(tytd.value_anchor, tfirst.value_anchor) IS NULL
          OR COALESCE(tytd.value_anchor, tfirst.value_anchor) = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - COALESCE(tytd.value_anchor, tfirst.value_anchor))
                    / COALESCE(tytd.value_anchor, tfirst.value_anchor)) * 100, 4)
    END AS pnl_pct_ytd,
    CASE
        WHEN COALESCE(t1y.value_anchor, tfirst.value_anchor) IS NULL
          OR COALESCE(t1y.value_anchor, tfirst.value_anchor) = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - COALESCE(t1y.value_anchor, tfirst.value_anchor))
                    / COALESCE(t1y.value_anchor, tfirst.value_anchor)) * 100, 4)
    END AS pnl_pct_1yr,
    CASE
        WHEN s.n_returns < 30
          OR s.stdev_return IS NULL
          OR s.stdev_return = 0 THEN NULL
        ELSE ROUND((((s.mean_return - 0.05 / 252.0) / s.stdev_return) * SQRT(252))::numeric, 4)
    END AS sharpe,
    COALESCE(s.n_returns, 0)::int AS sharpe_n_returns
FROM latest l
JOIN agents a ON a.id = l.agent_id
LEFT JOIN first_snapshot  tfirst ON tfirst.agent_id = l.agent_id
LEFT JOIN one_day_ago     t1d    ON t1d.agent_id    = l.agent_id
LEFT JOIN thirty_days_ago t30    ON t30.agent_id    = l.agent_id
LEFT JOIN year_start      tytd   ON tytd.agent_id   = l.agent_id
LEFT JOIN one_year_ago    t1y    ON t1y.agent_id    = l.agent_id
LEFT JOIN sharpe          s      ON s.agent_id      = l.agent_id
ORDER BY l.pnl_pct DESC;


-- ============================================================
-- 3. Pin function search_path
-- ============================================================
-- A mutable search_path lets a user with CREATE on any schema in the
-- current path shadow built-in or referenced objects. Pinning to
-- (public, pg_temp) makes the path immutable and explicit.
-- recompute_portfolio_snapshot and tg_recompute_snapshot_on_trade
-- reference unqualified table names (agent_accounts, agent_holdings,
-- companies, agent_portfolio_history) which all live in `public`.
-- update_updated_at touches no tables, but pinning is harmless.

ALTER FUNCTION public.recompute_portfolio_snapshot(UUID, DATE)
    SET search_path = public, pg_temp;

ALTER FUNCTION public.tg_recompute_snapshot_on_trade()
    SET search_path = public, pg_temp;

ALTER FUNCTION public.update_updated_at()
    SET search_path = public, pg_temp;
