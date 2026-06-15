-- Migration 051: per-portfolio rebalance cadence toggle (daily | weekly).
--
-- The agent heartbeat gates a whole portfolio on portfolios.last_heartbeat_at
-- via a hardcoded interval (agent_heartbeat._portfolio_is_due,
-- PORTFOLIO_HEARTBEAT_INTERVAL_HOURS = 168). That fixed weekly cadence is now
-- owner-configurable: a portfolio can opt into a DAILY rebalance instead.
--
--   weekly (default) → the portfolio is re-evaluated at most every 168h
--   daily            → the portfolio is re-evaluated at most every 24h
--
-- The heartbeat workflow itself runs daily (.github/workflows/agent-heartbeat.yml,
-- "0 7 * * *"); this column decides how often each portfolio actually acts on a
-- tick. Default is 'weekly' so existing portfolios keep their current behaviour.
--
-- Additive & idempotent. Paste-and-run in the Supabase SQL editor.

ALTER TABLE portfolios
    ADD COLUMN IF NOT EXISTS rebalance_cadence TEXT NOT NULL DEFAULT 'weekly';

-- Constrain to the two supported values. Guarded so re-running is a no-op.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'portfolios_rebalance_cadence_check'
    ) THEN
        ALTER TABLE portfolios
            ADD CONSTRAINT portfolios_rebalance_cadence_check
            CHECK (rebalance_cadence IN ('daily', 'weekly'));
    END IF;
END $$;

COMMENT ON COLUMN portfolios.rebalance_cadence IS
    'How often the heartbeat re-evaluates this portfolio: daily (24h) or weekly (168h, default). See agent_heartbeat._portfolio_is_due.';
