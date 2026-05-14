-- Migration 020: Investment-thesis framework.
--
-- When an agent buys an equity, the system captures a durable record:
--
-- (1) Snapshot (mandatory, automatic, every BUY) — a frozen JSONB
--     capture of the equity's screener / fundamentals / valuation /
--     momentum / narrative state at the moment of purchase. Lives in
--     the `snapshot` column. Hard NOT NULL — every row has it.
--
-- (2) Buy thesis text + signals (optional, agent-supplied) — when the
--     buy call passes a `thesis={...}` argument, the same row also
--     stores the agent's narrative + machine-checkable break/extend
--     signals. NULL when the agent didn't author one.
--
-- The framework is dormant until callers wire it up — this migration
-- adds the table only. PortfolioManager.buy / buy_atomic gain a
-- `thesis` kwarg in the accompanying Python change so every BUY
-- starts producing a snapshot row automatically. Strategies don't
-- need updating to get snapshots; writing thesis text is opt-in.

CREATE TABLE IF NOT EXISTS investment_theses (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id           UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    ticker             TEXT NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
    trade_id           BIGINT REFERENCES agent_trades(id) ON DELETE SET NULL,

    -- Frozen extended-tier equity state at buy time. Always populated.
    -- Shape mirrors build_universe_snapshot.py extended tier
    -- (lines 72-86): fundamentals, valuation, momentum, narrative.
    snapshot           JSONB NOT NULL,

    -- Agent-authored fields. NULL when the buy call passed no thesis.
    thesis_text        TEXT,
    extend_signals     JSONB,
    break_signals      JSONB,

    -- 'auto'  — snapshot only, no thesis_text/signals
    -- 'agent' — agent authored thesis_text + signals
    source             TEXT NOT NULL DEFAULT 'auto'
                          CHECK (source IN ('auto', 'agent')),

    -- 'active'     — currently held, thesis intact
    -- 'broken'     — maintenance check flagged a break_signal
    -- 'improved'   — maintenance check confirmed an extend_signal
    -- 'superseded' — a newer thesis exists for the same (agent, ticker)
    -- 'closed'     — position fully exited
    status             TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN (
                            'active','broken','improved','superseded','closed'
                          )),

    opened_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status_changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_theses_agent_ticker_status
    ON investment_theses (agent_id, ticker, status);
CREATE INDEX IF NOT EXISTS idx_theses_ticker
    ON investment_theses (ticker);
CREATE INDEX IF NOT EXISTS idx_theses_opened_at
    ON investment_theses (opened_at DESC);


-- ============================================================
-- RLS — public read, service-role-only writes. Matches the pattern
-- at migrations/010_rls_and_security.sql:39-70.
-- ============================================================
ALTER TABLE investment_theses ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read" ON investment_theses;
CREATE POLICY "public read" ON investment_theses
    FOR SELECT USING (true);
