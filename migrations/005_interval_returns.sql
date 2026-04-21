-- Migration 005: Leaderboard return intervals (1d / 30d / YTD / 1Yr)
--
-- Replaces the single "30d return with since-inception fallback"
-- column on agent_leaderboard with four rolling windows. Each column
-- follows the same pattern: prefer the snapshot on/before the window
-- boundary, fall back to the agent's earliest snapshot when history
-- is shorter than the window. `pnl_pct` (all-time) stays on the view
-- so the homepage rankings card keeps reading it.
--
-- Paste-and-run in the Supabase SQL editor. Idempotent.

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
first_snapshot AS (
    -- Fallback anchor: earliest snapshot for this agent.
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
    -- First snapshot on/after Jan 1 of the current year. For agents
    -- with inception in this year, this equals their first_snapshot.
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
    END AS pnl_pct_1yr
FROM latest l
JOIN agents a ON a.id = l.agent_id
LEFT JOIN first_snapshot tfirst ON tfirst.agent_id = l.agent_id
LEFT JOIN one_day_ago     t1d   ON t1d.agent_id   = l.agent_id
LEFT JOIN thirty_days_ago t30   ON t30.agent_id   = l.agent_id
LEFT JOIN year_start      tytd  ON tytd.agent_id  = l.agent_id
LEFT JOIN one_year_ago    t1y   ON t1y.agent_id   = l.agent_id
ORDER BY l.pnl_pct DESC;
