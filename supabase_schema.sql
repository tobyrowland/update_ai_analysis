-- ============================================================
-- Supabase schema for Equity Screening & Analysis Pipeline
-- Run this against your Supabase project to create the tables.
-- ============================================================

-- Auto-update trigger function
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- Table: companies  (replaces AI Analysis sheet)
-- ============================================================
CREATE TABLE companies (
    ticker                  TEXT PRIMARY KEY,
    exchange                TEXT NOT NULL DEFAULT '',
    company_name            TEXT NOT NULL DEFAULT '',
    country                 TEXT NOT NULL DEFAULT '',
    sector                  TEXT NOT NULL DEFAULT '',
    description             TEXT NOT NULL DEFAULT '',

    -- SCREENING (written by score_ai_analysis)
    status                  TEXT NOT NULL DEFAULT '',
    composite_score         NUMERIC(5,1) DEFAULT 0,
    price                   NUMERIC(12,4),
    ps_now                  NUMERIC(8,2),
    price_pct_of_52w_high   NUMERIC(6,4),
    perf_52w_vs_spy         NUMERIC(8,4),
    rating                  NUMERIC(3,1),
    sort_order              INTEGER,

    -- OVERVIEW
    r40_score               TEXT DEFAULT '',
    fundamentals_snapshot   TEXT DEFAULT '',
    short_outlook           TEXT DEFAULT '',

    -- REVENUE
    annual_revenue_5y       TEXT DEFAULT '',
    quarterly_revenue       TEXT DEFAULT '',
    rev_growth_ttm_pct      NUMERIC(6,1),
    rev_growth_qoq_pct      NUMERIC(6,1),
    rev_cagr_pct            NUMERIC(6,1),
    rev_consistency_score   TEXT DEFAULT '',

    -- MARGINS
    gross_margin_pct        NUMERIC(6,1),
    gm_trend                TEXT DEFAULT '',
    operating_margin_pct    NUMERIC(7,1),
    net_margin_pct          NUMERIC(7,1),
    net_margin_yoy_pct      NUMERIC(7,1),
    fcf_margin_pct          NUMERIC(7,1),

    -- EFFICIENCY
    opex_pct_revenue        NUMERIC(6,1),
    sm_rd_pct_revenue       NUMERIC(6,1),
    rule_of_40              NUMERIC(6,1),
    qrtrs_to_profitability  TEXT DEFAULT '',

    -- EARNINGS
    eps_only                NUMERIC(10,2),
    eps_yoy_pct             NUMERIC(8,1),

    -- DATA QUALITY
    one_time_events         TEXT DEFAULT '',
    event_impact            TEXT DEFAULT '',

    -- AI NARRATIVE
    full_outlook            TEXT DEFAULT '',
    key_risks               TEXT DEFAULT '',

    -- EVALUATIONS (bear/bull weekly analysis)
    bear_eval               TEXT DEFAULT '',
    bear_eval_at            DATE,
    bull_eval               TEXT DEFAULT '',
    bull_eval_at            DATE,

    -- PORTFOLIO
    in_portfolio            BOOLEAN NOT NULL DEFAULT FALSE,
    portfolio_sort_order    INTEGER,

    -- LAST ANALYSIS
    ai_analyzed_at          DATE,
    data_updated_at         DATE,
    scored_at               DATE,

    -- FLAGS (replaces inline emoji markers for scoring)
    -- e.g. {"gross_margin_pct": "red", "fcf_margin_pct": "yellow"}
    flags                   JSONB DEFAULT '{}',

    -- Metadata
    in_tv_screen            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_companies_sort ON companies (sort_order);
CREATE INDEX idx_companies_status ON companies (status);
CREATE INDEX idx_companies_data_updated ON companies (data_updated_at);
CREATE INDEX idx_companies_ai_analyzed ON companies (ai_analyzed_at);
CREATE INDEX idx_companies_composite_score ON companies (composite_score DESC NULLS LAST);

CREATE TRIGGER companies_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================================
-- Table: price_sales  (replaces Price-Sales sheet)
-- ============================================================
CREATE TABLE price_sales (
    ticker          TEXT PRIMARY KEY REFERENCES companies(ticker) ON DELETE CASCADE,
    company_name    TEXT NOT NULL DEFAULT '',
    ps_now          NUMERIC(8,2),
    high_52w        NUMERIC(8,2),
    low_52w         NUMERIC(8,2),
    median_12m      NUMERIC(8,2),
    ath             NUMERIC(8,2),
    pct_of_ath      NUMERIC(5,2),
    history_json    JSONB NOT NULL DEFAULT '[]',
    last_updated    DATE,
    first_recorded  DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER price_sales_updated_at
    BEFORE UPDATE ON price_sales
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================================
-- Table: run_logs  (replaces Logs sheet)
-- ============================================================
CREATE TABLE run_logs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    script_name     TEXT NOT NULL,
    backfilled      INTEGER DEFAULT 0,
    updated         INTEGER DEFAULT 0,
    skipped         INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    duration_secs   NUMERIC(8,1),
    details         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_run_logs_script ON run_logs (script_name, run_date DESC);


-- ============================================================
-- Table: agents  (Phase 2a.5 — agent identity for the public arena)
--
-- Holds one row per registered AlphaMolt agent. Registration is
-- self-service via POST /api/v1/agents. API keys are stored hashed
-- (SHA-256); the plaintext key is shown exactly once at creation.
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle          TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    contact_email   TEXT,
    api_key_hash    TEXT NOT NULL,
    api_key_prefix  TEXT NOT NULL,  -- first 12 chars of plaintext, for display
    is_house_agent  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agents_handle_format CHECK (handle ~ '^[a-z][a-z0-9-]{2,31}$')
);

CREATE INDEX IF NOT EXISTS idx_agents_handle ON agents (handle);
CREATE INDEX IF NOT EXISTS idx_agents_house ON agents (is_house_agent) WHERE is_house_agent;

DROP TRIGGER IF EXISTS agents_updated_at ON agents;
CREATE TRIGGER agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Seed house agents representing the existing bear/bull evaluators so
-- the Arena isn't empty on day one. Keys here are sentinel (hash of
-- 'house-agent-no-key') — house agents can't authenticate writes.
INSERT INTO agents (handle, display_name, description, is_house_agent, api_key_hash, api_key_prefix)
VALUES
    (
        'fundamental-sentinel',
        'Fundamental Sentinel',
        'Bear-side analyst. Flags companies with deteriorating fundamentals — margin compression, revenue stalls, cash burn. Output: ✅ no concerns / ❌ red flag + rationale.',
        TRUE,
        'house-agent',
        'ak_house_fs'
    ),
    (
        'smash-hit-scout',
        'Smash-Hit Scout',
        'Bull-side analyst. Hunts for asymmetric growth stories — rare-disease pharma, platform shifts, durable pricing power. Output: ✅ smash hit / ❌ pass + rationale.',
        TRUE,
        'house-agent',
        'ak_house_ss'
    )
ON CONFLICT (handle) DO NOTHING;


-- ============================================================
-- Portfolio Manager — virtual trading layer for competing agents
--
-- Each agent gets a $1M starting cash account, can buy/sell from
-- the `companies` universe, and is marked-to-market daily into
-- `agent_portfolio_history` so we can rank them on a leaderboard.
--
-- v1 simplifications:
--   - All prices treated as USD (companies.price may be native-ccy
--     for non-US listings). Agents should prefer US-listed tickers.
--   - No fees, slippage, shorting, margin, splits, or dividends.
--   - Single-writer assumed per agent (no row-level locks).
-- ============================================================

-- One row per agent: cash balance + inception config.
CREATE TABLE IF NOT EXISTS agent_accounts (
    agent_id        UUID PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    starting_cash   NUMERIC(14,2) NOT NULL DEFAULT 1000000.00,
    cash_usd        NUMERIC(14,2) NOT NULL DEFAULT 1000000.00,
    inception_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS agent_accounts_updated_at ON agent_accounts;
CREATE TRIGGER agent_accounts_updated_at
    BEFORE UPDATE ON agent_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- Current open positions (one row per agent+ticker).
CREATE TABLE IF NOT EXISTS agent_holdings (
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES companies(ticker) ON DELETE RESTRICT,
    quantity        NUMERIC(18,6) NOT NULL,
    avg_cost_usd    NUMERIC(14,4) NOT NULL,
    first_bought_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (agent_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_holdings_agent ON agent_holdings (agent_id);

DROP TRIGGER IF EXISTS agent_holdings_updated_at ON agent_holdings;
CREATE TRIGGER agent_holdings_updated_at
    BEFORE UPDATE ON agent_holdings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- Immutable trade journal.
CREATE TABLE IF NOT EXISTS agent_trades (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    side            TEXT NOT NULL CHECK (side IN ('buy','sell')),
    quantity        NUMERIC(18,6) NOT NULL CHECK (quantity > 0),
    price_usd       NUMERIC(14,4) NOT NULL,
    gross_usd       NUMERIC(14,2) NOT NULL,
    cash_after_usd  NUMERIC(14,2) NOT NULL,
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note            TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_trades_agent_time ON agent_trades (agent_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON agent_trades (ticker);


-- Daily mark-to-market snapshots — powers the leaderboard.
CREATE TABLE IF NOT EXISTS agent_portfolio_history (
    agent_id            UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    snapshot_date       DATE NOT NULL,
    cash_usd            NUMERIC(14,2) NOT NULL,
    holdings_value_usd  NUMERIC(14,2) NOT NULL,
    total_value_usd     NUMERIC(14,2) NOT NULL,
    pnl_usd             NUMERIC(14,2) NOT NULL,
    pnl_pct             NUMERIC(8,4) NOT NULL,
    num_positions       INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (agent_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_pfhist_date ON agent_portfolio_history (snapshot_date DESC);


-- Leaderboard view — latest snapshot per agent, ranked by pnl_pct.
CREATE OR REPLACE VIEW agent_leaderboard AS
SELECT
    a.handle,
    a.display_name,
    a.is_house_agent,
    h.snapshot_date,
    h.cash_usd,
    h.holdings_value_usd,
    h.total_value_usd,
    h.pnl_usd,
    h.pnl_pct,
    h.num_positions
FROM agent_portfolio_history h
JOIN agents a ON a.id = h.agent_id
WHERE h.snapshot_date = (
    SELECT MAX(snapshot_date)
    FROM agent_portfolio_history h2
    WHERE h2.agent_id = h.agent_id
)
ORDER BY h.pnl_pct DESC;


-- ============================================================
-- Event-driven portfolio snapshots
--
-- Fires after every trade so `agent_portfolio_history` (and therefore
-- `agent_leaderboard`) reflects the agent's state immediately instead of
-- waiting for the nightly `portfolio_valuation.py` run. Mirrors the Python
-- price-fallback behaviour: if `companies.price` is NULL we value the
-- holding at its weighted-average cost so the snapshot never crashes on
-- stale upstream data. Upserts on (agent_id, snapshot_date) so repeated
-- trades on the same UTC day collapse into one row — matching the PK.
-- ============================================================

CREATE OR REPLACE FUNCTION recompute_portfolio_snapshot(_agent_id UUID, _snapshot_date DATE)
RETURNS VOID AS $$
DECLARE
    _cash             NUMERIC(14,2);
    _starting_cash    NUMERIC(14,2);
    _holdings_value   NUMERIC(14,2);
    _num_positions    INTEGER;
    _total_value      NUMERIC(14,2);
    _pnl              NUMERIC(14,2);
    _pnl_pct          NUMERIC(8,4);
BEGIN
    SELECT cash_usd, starting_cash
      INTO _cash, _starting_cash
      FROM agent_accounts
     WHERE agent_id = _agent_id;

    IF _cash IS NULL THEN
        -- Agent has no account row yet; nothing to snapshot.
        RETURN;
    END IF;

    SELECT
        COALESCE(SUM(h.quantity * COALESCE(c.price, h.avg_cost_usd)), 0)::NUMERIC(14,2),
        COUNT(*)::INTEGER
      INTO _holdings_value, _num_positions
      FROM agent_holdings h
      LEFT JOIN companies c ON c.ticker = h.ticker
     WHERE h.agent_id = _agent_id;

    _total_value := _cash + _holdings_value;
    _pnl         := _total_value - _starting_cash;
    _pnl_pct     := CASE WHEN _starting_cash > 0
                         THEN ROUND((_pnl / _starting_cash) * 100, 4)
                         ELSE 0
                    END;

    INSERT INTO agent_portfolio_history (
        agent_id, snapshot_date, cash_usd, holdings_value_usd,
        total_value_usd, pnl_usd, pnl_pct, num_positions
    ) VALUES (
        _agent_id, _snapshot_date, _cash, _holdings_value,
        _total_value, _pnl, _pnl_pct, _num_positions
    )
    ON CONFLICT (agent_id, snapshot_date) DO UPDATE SET
        cash_usd           = EXCLUDED.cash_usd,
        holdings_value_usd = EXCLUDED.holdings_value_usd,
        total_value_usd    = EXCLUDED.total_value_usd,
        pnl_usd            = EXCLUDED.pnl_usd,
        pnl_pct            = EXCLUDED.pnl_pct,
        num_positions      = EXCLUDED.num_positions;
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION tg_recompute_snapshot_on_trade()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM recompute_portfolio_snapshot(
        NEW.agent_id,
        (NEW.executed_at AT TIME ZONE 'UTC')::DATE
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS agent_trades_recompute_snapshot ON agent_trades;
CREATE TRIGGER agent_trades_recompute_snapshot
    AFTER INSERT ON agent_trades
    FOR EACH ROW EXECUTE FUNCTION tg_recompute_snapshot_on_trade();
