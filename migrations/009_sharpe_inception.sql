-- Migration 009: Since-inception Sharpe ratio (replaces 30-day rolling)
--
-- Rationale: 30-day Sharpes computed from ~22 daily returns produce
-- values of 5-9 in calm/rising regimes, which doesn't match what a
-- finance audience expects (typical fund Sharpes sit in 0-2). Since-
-- inception Sharpe folds in volatile months and mean-reverts to a
-- conventional range, while also rewarding sustained performance over
-- single-month luck.
--
-- Sharpe = (mean_daily_return - 0.05/252) / stdev_daily_return * sqrt(252)
-- with rf = 5% annual, computed over weekday-only daily returns from
-- the agent's entire snapshot history. NULL when fewer than 30 weekday
-- returns are available (frontend renders as "calculating") or stdev
-- is zero.
--
-- Column renamed: `sharpe_30d` → `sharpe`. `sharpe_n_returns` retained.
--
-- IMPORTANT: This rename is breaking for the frontend until the
-- corresponding deploy ships. Apply this migration and redeploy the
-- web bundle in the same window to avoid an empty leaderboard.
--
-- Paste-and-run in the Supabase SQL editor. Idempotent.
--
-- Note: DROP first, then CREATE — `CREATE OR REPLACE VIEW` cannot
-- rename a view column (Postgres 42P16), and we're renaming
-- `sharpe_30d` → `sharpe` here.

DROP VIEW IF EXISTS agent_leaderboard;

CREATE VIEW agent_leaderboard AS
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
    -- Weekday-only daily returns over the agent's entire history.
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
    -- Since-inception Sharpe with 5% annual rf. Min 30 weekday returns.
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
