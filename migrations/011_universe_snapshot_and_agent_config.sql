-- Migration 011: agents.config + universe_snapshots
--
-- Two foundational changes for the LLM-pick weekly heartbeat work:
--
-- 1. agents.config (JSONB) — per-agent strategy parameters. Today
--    `dual_positive` and `momentum` use only the global defaults in
--    agent_strategies.py. The forthcoming `llm_pick` strategy needs
--    per-agent values: provider ("anthropic" | "openai" | "google" |
--    "deepseek"), model id, picker_mode ("two_stage" | "single_full"),
--    snapshot_tier ("compact" | "extended" | "full"). Existing strategies
--    can opt in later if they want overrides; until then the column is
--    inert (defaults to '{}').
--
-- 2. universe_snapshots — daily-immutable artefact of the screened
--    universe at three detail tiers. Built by build_universe_snapshot.py
--    after score_ai_analysis.py. Read by the llm_pick strategy at
--    heartbeat time and by the public /api/v1/universe endpoint.
--    (snapshot_date, detail) is PK so each day produces exactly three
--    rows: one per tier. The JSON itself is fully self-describing
--    (filter, ticker count, snapshot_time_utc) so consumers don't need
--    sidecars.

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS universe_snapshots (
  snapshot_date  DATE NOT NULL,
  detail         TEXT NOT NULL CHECK (detail IN ('compact', 'extended', 'full')),
  json           JSONB NOT NULL,
  sha256         TEXT NOT NULL,
  ticker_count   INTEGER NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (snapshot_date, detail)
);

-- Lookup pattern is "give me the latest snapshot at tier X" — covered by
-- the PK already, but the index below makes "list latest dates per tier"
-- (used by the date-picker UI on /universe) cheap.
CREATE INDEX IF NOT EXISTS idx_universe_snapshots_date_desc
  ON universe_snapshots (snapshot_date DESC, detail);
