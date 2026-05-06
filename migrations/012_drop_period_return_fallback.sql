-- Migration 012: stop pretending short-history agents have a real Nd return.
--
-- Until now, agent_leaderboard.pnl_pct_{1d,30d,ytd,1yr} fell back to the
-- agent's first snapshot when no snapshot existed at the cutoff
-- (`COALESCE(tNd.value_anchor, tfirst.value_anchor)`). That made the four
-- columns incomparable across agents — a 14-day-old agent's "30d return"
-- was really its 14-day return, and the leaderboard ranked agents whose
-- inception dates differed by months as if their windows were the same.
--
-- New rule: a window is reportable only when the agent has a snapshot
-- at-or-before its cutoff. Otherwise the column is NULL and the frontend
-- renders "calculating", same treatment as Sharpe < 30 returns.
--
-- The all-time `pnl_pct` column stays put — that's where since-inception
-- still lives, and it's the default sort key.

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
    -- For YTD we want the snapshot AT-OR-BEFORE Jan 1 (i.e., the agent
    -- existed before the year started). The previous version took the
    -- earliest snapshot >= Jan 1, which silently included agents born
    -- mid-year and labelled their full P&L as "YTD".
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date < DATE_TRUNC('year', CURRENT_DATE)::DATE
    ORDER BY agent_id, snapshot_date DESC
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
        WHEN t1d.value_anchor IS NULL OR t1d.value_anchor = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - t1d.value_anchor)
                    / t1d.value_anchor) * 100, 4)
    END AS pnl_pct_1d,
    CASE
        WHEN t30.value_anchor IS NULL OR t30.value_anchor = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - t30.value_anchor)
                    / t30.value_anchor) * 100, 4)
    END AS pnl_pct_30d,
    CASE
        WHEN tytd.value_anchor IS NULL OR tytd.value_anchor = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - tytd.value_anchor)
                    / tytd.value_anchor) * 100, 4)
    END AS pnl_pct_ytd,
    CASE
        WHEN t1y.value_anchor IS NULL OR t1y.value_anchor = 0 THEN NULL
        ELSE ROUND(((l.total_value_usd - t1y.value_anchor)
                    / t1y.value_anchor) * 100, 4)
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
LEFT JOIN one_day_ago     t1d  ON t1d.agent_id  = l.agent_id
LEFT JOIN thirty_days_ago t30  ON t30.agent_id  = l.agent_id
LEFT JOIN year_start      tytd ON tytd.agent_id = l.agent_id
LEFT JOIN one_year_ago    t1y  ON t1y.agent_id  = l.agent_id
LEFT JOIN sharpe          s    ON s.agent_id    = l.agent_id
ORDER BY l.pnl_pct DESC;
