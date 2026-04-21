-- Migration 004: 30d-return fallback to since-inception
--
-- Migration 003 added pnl_pct_30d to agent_leaderboard with NULL when an
-- agent has <30 days of history. When the leaderboard is young (e.g. 7
-- days in), that means every row shows `—`. Rework the view so it falls
-- back to the earliest available snapshot: the column now reads "return
-- over the last 30 days, or since inception if the agent is younger
-- than 30 days". Honest-enough label; always renders a number.
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
thirty_days_ago AS (
    -- Preferred anchor: most recent snapshot on/before 30 days ago.
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '30 days'
    ORDER BY agent_id, snapshot_date DESC
),
first_snapshot AS (
    -- Fallback anchor: earliest snapshot for this agent (since inception).
    SELECT DISTINCT ON (agent_id)
        agent_id,
        total_value_usd AS value_anchor
    FROM agent_portfolio_history
    ORDER BY agent_id, snapshot_date ASC
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
        WHEN COALESCE(t30.value_anchor, tfirst.value_anchor) IS NULL
          OR COALESCE(t30.value_anchor, tfirst.value_anchor) = 0
            THEN NULL
        ELSE ROUND(
            ((l.total_value_usd - COALESCE(t30.value_anchor, tfirst.value_anchor))
             / COALESCE(t30.value_anchor, tfirst.value_anchor)) * 100,
            4
        )
    END AS pnl_pct_30d
FROM latest l
JOIN agents a ON a.id = l.agent_id
LEFT JOIN thirty_days_ago t30 ON t30.agent_id = l.agent_id
LEFT JOIN first_snapshot tfirst ON tfirst.agent_id = l.agent_id
ORDER BY l.pnl_pct DESC;
