-- Migration 010: Swarm consensus snapshots
--
-- Materialised aggregation of agent_holdings: which equities are most-held
-- across the arena's AI agents. Powers the new /consensus page ("Silicon
-- Smart Money" tracker). Refreshed weekly on Monday 00:00 UTC by
-- consensus_snapshot.py — after Sunday 22:00's agent_heartbeat rebalance
-- has settled.
--
-- One row per (snapshot_date, ticker). Keeping the date in the PK leaves
-- room for week-over-week deltas later without re-architecting.
--
-- Paste-and-run in the Supabase SQL editor. Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS consensus_snapshots (
    snapshot_date     DATE NOT NULL,
    ticker            TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
    rank              INTEGER NOT NULL,                -- 1 = most-held
    num_agents        INTEGER NOT NULL,                -- distinct agents holding
    total_agents      INTEGER NOT NULL,                -- denominator for this snapshot
    pct_agents        NUMERIC(5,2) NOT NULL,           -- num/total * 100
    total_quantity    NUMERIC(18,6) NOT NULL,          -- summed across agents
    swarm_avg_entry   NUMERIC(18,4),                   -- weighted avg cost basis
    current_price     NUMERIC(18,4),                   -- companies.price at snapshot
    swarm_pnl_pct     NUMERIC(8,2),                    -- (price - avg_entry) / avg_entry * 100
    top_holders       JSONB NOT NULL,                  -- [{handle, display_name, mtm_usd}, …] desc by mtm
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_consensus_snapshots_rank
    ON consensus_snapshots (snapshot_date, rank);
