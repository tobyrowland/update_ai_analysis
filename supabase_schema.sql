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
