-- Migration 006: Agent heartbeats
--
-- Adds a scheduled rebalance loop ("heartbeat") to agents so portfolios
-- can evolve over time instead of being frozen after an initial build.
--
-- Each run of agent_heartbeat.py reads agents whose last_heartbeat_at is
-- older than heartbeat_interval_hours, dispatches to the named `strategy`,
-- and journals what happened in agent_heartbeats. Agents with NULL
-- `strategy` are skipped (they're manually managed).
--
-- Paste-and-run in the Supabase SQL editor. Idempotent.

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS strategy                 TEXT,
    ADD COLUMN IF NOT EXISTS heartbeat_interval_hours INTEGER DEFAULT 168,
    ADD COLUMN IF NOT EXISTS last_heartbeat_at        TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agents_strategy
    ON agents (strategy)
    WHERE strategy IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_heartbeats (
    id               BIGSERIAL PRIMARY KEY,
    agent_id         UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    strategy         TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    status           TEXT NOT NULL,  -- 'ok' | 'error' | 'skipped' | 'dry-run'
    trades_executed  INTEGER NOT NULL DEFAULT 0,
    buys             INTEGER NOT NULL DEFAULT 0,
    sells            INTEGER NOT NULL DEFAULT 0,
    notes            JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message    TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_agent_time
    ON agent_heartbeats (agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_started_at
    ON agent_heartbeats (started_at DESC);
