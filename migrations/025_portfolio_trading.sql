-- Migration 025: Trading backend for human-owned portfolios.
--
-- Human-owned portfolios (migration 024) are configured drafts with no
-- capital. This migration gives them a portfolio-level shared-pot cash
-- balance + holdings so their member agents can trade, plus the "go live"
-- mechanism that grants the $1M.
--
-- Decisions baked in:
--   - Shared pot: one cash balance per portfolio (portfolio_accounts).
--   - Explicit launch: portfolios.launched_at NULL = draft; launch_portfolio()
--     sets it and seeds the $1M account atomically.
--   - Legacy 1:1 agent portfolios are untouched — they keep using
--     agent_accounts / agent_holdings. The new tables are purely additive.
--
-- Additive & idempotent. Paste-and-run in the Supabase SQL editor.
--
-- NOTE: this migration alters the agent_portfolio_history primary key from
-- (agent_id, snapshot_date) to (portfolio_id, snapshot_date). It is a logical
-- no-op for existing rows (portfolio_id == agent_id during the shim) but it
-- is load-bearing — take a database snapshot before applying.

-- ============================================================
-- 1. portfolios: launch + heartbeat columns
-- ============================================================

ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS launched_at      TIMESTAMPTZ;
ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

-- ============================================================
-- 2. portfolio_accounts — the shared-pot cash row (one per portfolio)
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_accounts (
    portfolio_id   UUID PRIMARY KEY REFERENCES portfolios(id) ON DELETE CASCADE,
    starting_cash  NUMERIC(14,2) NOT NULL DEFAULT 1000000.00,
    cash_usd       NUMERIC(14,2) NOT NULL DEFAULT 1000000.00,
    inception_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS portfolio_accounts_updated_at ON portfolio_accounts;
CREATE TRIGGER portfolio_accounts_updated_at
    BEFORE UPDATE ON portfolio_accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- 3. portfolio_holdings — open positions, keyed by portfolio (not agent)
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    portfolio_id    UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    quantity        NUMERIC(18,6) NOT NULL,
    avg_cost_usd    NUMERIC(14,4) NOT NULL,
    first_bought_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (portfolio_id, ticker)
);

-- ============================================================
-- 4. agent_portfolio_history — re-key on portfolio_id
-- ============================================================
-- The valuation history table powers the agent_leaderboard view (already
-- partitioned on portfolio_id). Its PK is agent-keyed, which blocks
-- agent_id-less human-portfolio snapshots. Re-key to (portfolio_id,
-- snapshot_date) and make agent_id nullable. Idempotent: only acts when the
-- PK isn't already the portfolio-keyed one.

DO $$
DECLARE
    pk_name TEXT;
    pk_cols TEXT;
BEGIN
    SELECT c.conname,
           string_agg(a.attname, ',' ORDER BY array_position(c.conkey, a.attnum))
      INTO pk_name, pk_cols
      FROM pg_constraint c
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
     WHERE c.conrelid = 'agent_portfolio_history'::regclass
       AND c.contype = 'p'
     GROUP BY c.conname;

    IF pk_cols IS DISTINCT FROM 'portfolio_id,snapshot_date' THEN
        IF pk_name IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE agent_portfolio_history DROP CONSTRAINT %I',
                pk_name
            );
        END IF;
        ALTER TABLE agent_portfolio_history ALTER COLUMN agent_id DROP NOT NULL;
        ALTER TABLE agent_portfolio_history
            ADD CONSTRAINT agent_portfolio_history_pkey
            PRIMARY KEY (portfolio_id, snapshot_date);
    END IF;
END $$;

-- ============================================================
-- 5. RLS — defense-in-depth (the website reads via service-role anyway)
-- ============================================================

ALTER TABLE portfolio_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_holdings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "portfolio account read" ON portfolio_accounts;
CREATE POLICY "portfolio account read" ON portfolio_accounts FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM portfolios p
         WHERE p.id = portfolio_accounts.portfolio_id
           AND (p.is_public OR p.owner_user_id = auth.uid())
    ));

DROP POLICY IF EXISTS "portfolio holdings read" ON portfolio_holdings;
CREATE POLICY "portfolio holdings read" ON portfolio_holdings FOR SELECT
    USING (EXISTS (
        SELECT 1 FROM portfolios p
         WHERE p.id = portfolio_holdings.portfolio_id
           AND (p.is_public OR p.owner_user_id = auth.uid())
    ));

-- ============================================================
-- 6. launch_portfolio — atomic "go live"
-- ============================================================
-- Sets launched_at and seeds the $1M portfolio_accounts row in one
-- transaction. Idempotent: a second call on a launched portfolio is a no-op.

CREATE OR REPLACE FUNCTION launch_portfolio(p_portfolio_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_launched     TIMESTAMPTZ;
    v_member_count INT;
BEGIN
    SELECT launched_at INTO v_launched
        FROM portfolios
        WHERE id = p_portfolio_id
        FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no portfolio %', p_portfolio_id;
    END IF;
    IF v_launched IS NOT NULL THEN
        RETURN jsonb_build_object('status', 'already_launched',
                                  'launched_at', v_launched);
    END IF;

    SELECT count(*) INTO v_member_count
        FROM portfolio_agents
        WHERE portfolio_id = p_portfolio_id;
    IF v_member_count = 0 THEN
        RETURN jsonb_build_object('status', 'no_members');
    END IF;

    UPDATE portfolios SET launched_at = NOW() WHERE id = p_portfolio_id;

    INSERT INTO portfolio_accounts (portfolio_id, cash_usd, starting_cash)
        VALUES (p_portfolio_id, 1000000.00, 1000000.00)
        ON CONFLICT (portfolio_id) DO NOTHING;

    RETURN jsonb_build_object('status', 'ok');
END;
$$;

-- ============================================================
-- 7. execute_portfolio_buy / execute_portfolio_sell
-- ============================================================
-- Portfolio-keyed twins of execute_atomic_buy/sell (migration 022). Lock the
-- portfolio_accounts row, upsert portfolio_holdings, journal to agent_trades
-- with both portfolio_id and the executing member agent_id.

CREATE OR REPLACE FUNCTION execute_portfolio_buy(
    p_portfolio_id UUID,
    p_agent_id     UUID,
    p_ticker       TEXT,
    p_quantity     NUMERIC,
    p_price_usd    NUMERIC,
    p_note         TEXT DEFAULT ''
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_gross_usd     NUMERIC;
    v_current_cash  NUMERIC;
    v_new_cash      NUMERIC;
    v_existing_qty  NUMERIC;
    v_existing_cost NUMERIC;
    v_new_qty       NUMERIC;
    v_new_avg_cost  NUMERIC;
    v_trade_id      BIGINT;
BEGIN
    IF p_quantity <= 0 THEN
        RAISE EXCEPTION 'quantity must be > 0 (got %)', p_quantity;
    END IF;
    IF p_price_usd <= 0 THEN
        RAISE EXCEPTION 'price_usd must be > 0 (got %)', p_price_usd;
    END IF;

    v_gross_usd := p_quantity * p_price_usd;

    SELECT cash_usd INTO v_current_cash
        FROM portfolio_accounts
        WHERE portfolio_id = p_portfolio_id
        FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no account for portfolio %', p_portfolio_id;
    END IF;

    IF v_current_cash < v_gross_usd THEN
        RETURN jsonb_build_object(
            'status', 'insufficient_cash',
            'cash_usd', v_current_cash,
            'needed_usd', v_gross_usd
        );
    END IF;

    v_new_cash := v_current_cash - v_gross_usd;

    SELECT quantity, avg_cost_usd
        INTO v_existing_qty, v_existing_cost
        FROM portfolio_holdings
        WHERE portfolio_id = p_portfolio_id AND ticker = p_ticker
        FOR UPDATE;

    IF NOT FOUND THEN
        v_new_qty := p_quantity;
        v_new_avg_cost := p_price_usd;
        INSERT INTO portfolio_holdings
            (portfolio_id, ticker, quantity, avg_cost_usd, first_bought_at, updated_at)
        VALUES
            (p_portfolio_id, p_ticker, v_new_qty, v_new_avg_cost, NOW(), NOW());
    ELSE
        v_new_qty := v_existing_qty + p_quantity;
        v_new_avg_cost := (v_existing_qty * v_existing_cost + v_gross_usd) / v_new_qty;
        UPDATE portfolio_holdings
           SET quantity = v_new_qty,
               avg_cost_usd = v_new_avg_cost,
               updated_at = NOW()
         WHERE portfolio_id = p_portfolio_id AND ticker = p_ticker;
    END IF;

    UPDATE portfolio_accounts
       SET cash_usd = v_new_cash
     WHERE portfolio_id = p_portfolio_id;

    INSERT INTO agent_trades
        (agent_id, portfolio_id, ticker, side, quantity, price_usd,
         gross_usd, cash_after_usd, executed_at, note)
    VALUES
        (p_agent_id, p_portfolio_id, p_ticker, 'buy', p_quantity, p_price_usd,
         v_gross_usd, v_new_cash, NOW(), COALESCE(p_note, ''))
    RETURNING id INTO v_trade_id;

    RETURN jsonb_build_object(
        'status', 'ok',
        'trade_id', v_trade_id,
        'portfolio_id', p_portfolio_id,
        'gross_usd', v_gross_usd,
        'new_cash_usd', v_new_cash,
        'new_quantity', v_new_qty,
        'new_avg_cost_usd', v_new_avg_cost
    );
END;
$$;

CREATE OR REPLACE FUNCTION execute_portfolio_sell(
    p_portfolio_id UUID,
    p_agent_id     UUID,
    p_ticker       TEXT,
    p_quantity     NUMERIC,
    p_price_usd    NUMERIC,
    p_note         TEXT DEFAULT ''
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_gross_usd    NUMERIC;
    v_current_cash NUMERIC;
    v_new_cash     NUMERIC;
    v_existing_qty NUMERIC;
    v_new_qty      NUMERIC;
    v_trade_id     BIGINT;
BEGIN
    IF p_quantity <= 0 THEN
        RAISE EXCEPTION 'quantity must be > 0 (got %)', p_quantity;
    END IF;
    IF p_price_usd <= 0 THEN
        RAISE EXCEPTION 'price_usd must be > 0 (got %)', p_price_usd;
    END IF;

    v_gross_usd := p_quantity * p_price_usd;

    SELECT cash_usd INTO v_current_cash
        FROM portfolio_accounts
        WHERE portfolio_id = p_portfolio_id
        FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no account for portfolio %', p_portfolio_id;
    END IF;

    SELECT quantity INTO v_existing_qty
        FROM portfolio_holdings
        WHERE portfolio_id = p_portfolio_id AND ticker = p_ticker
        FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'no_position', 'ticker', p_ticker);
    END IF;

    IF v_existing_qty < p_quantity THEN
        RETURN jsonb_build_object(
            'status', 'insufficient_quantity',
            'held_quantity', v_existing_qty,
            'requested_quantity', p_quantity
        );
    END IF;

    v_new_qty := v_existing_qty - p_quantity;
    v_new_cash := v_current_cash + v_gross_usd;

    IF v_new_qty = 0 THEN
        DELETE FROM portfolio_holdings
            WHERE portfolio_id = p_portfolio_id AND ticker = p_ticker;
    ELSE
        UPDATE portfolio_holdings
           SET quantity = v_new_qty,
               updated_at = NOW()
         WHERE portfolio_id = p_portfolio_id AND ticker = p_ticker;
    END IF;

    UPDATE portfolio_accounts
       SET cash_usd = v_new_cash
     WHERE portfolio_id = p_portfolio_id;

    INSERT INTO agent_trades
        (agent_id, portfolio_id, ticker, side, quantity, price_usd,
         gross_usd, cash_after_usd, executed_at, note)
    VALUES
        (p_agent_id, p_portfolio_id, p_ticker, 'sell', p_quantity, p_price_usd,
         v_gross_usd, v_new_cash, NOW(), COALESCE(p_note, ''))
    RETURNING id INTO v_trade_id;

    RETURN jsonb_build_object(
        'status', 'ok',
        'trade_id', v_trade_id,
        'portfolio_id', p_portfolio_id,
        'gross_usd', v_gross_usd,
        'new_cash_usd', v_new_cash,
        'remaining_quantity', v_new_qty
    );
END;
$$;

REVOKE ALL ON FUNCTION launch_portfolio          FROM PUBLIC;
REVOKE ALL ON FUNCTION execute_portfolio_buy     FROM PUBLIC;
REVOKE ALL ON FUNCTION execute_portfolio_sell    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION launch_portfolio       TO service_role;
GRANT EXECUTE ON FUNCTION execute_portfolio_buy  TO service_role;
GRANT EXECUTE ON FUNCTION execute_portfolio_sell TO service_role;

-- ============================================================
-- 8. Rebuild agent_leaderboard — surface human portfolios
-- ============================================================
-- Same shape as migration 021, with two changes:
--   * JOIN agents owner -> LEFT JOIN (owner_agent_id is NULL for human
--     portfolios; the inner join silently dropped them).
--   * COALESCE(owner.is_house_agent, false).
-- Plus is_public + launched_at columns for query-layer visibility filtering.

DROP VIEW IF EXISTS agent_leaderboard;

CREATE VIEW agent_leaderboard
    WITH (security_invoker = true)
AS
WITH latest AS (
    SELECT DISTINCT ON (portfolio_id)
        portfolio_id, snapshot_date, cash_usd, holdings_value_usd,
        total_value_usd, pnl_usd, pnl_pct, num_positions
    FROM agent_portfolio_history
    ORDER BY portfolio_id, snapshot_date DESC
),
one_day_ago AS (
    SELECT DISTINCT ON (portfolio_id)
        portfolio_id, total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '1 day'
    ORDER BY portfolio_id, snapshot_date DESC
),
one_week_ago AS (
    SELECT DISTINCT ON (portfolio_id)
        portfolio_id, total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '7 days'
    ORDER BY portfolio_id, snapshot_date DESC
),
thirty_days_ago AS (
    SELECT DISTINCT ON (portfolio_id)
        portfolio_id, total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '30 days'
    ORDER BY portfolio_id, snapshot_date DESC
),
year_start AS (
    SELECT DISTINCT ON (portfolio_id)
        portfolio_id, total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date < DATE_TRUNC('year', CURRENT_DATE)::DATE
    ORDER BY portfolio_id, snapshot_date DESC
),
one_year_ago AS (
    SELECT DISTINCT ON (portfolio_id)
        portfolio_id, total_value_usd AS value_anchor
    FROM agent_portfolio_history
    WHERE snapshot_date <= CURRENT_DATE - INTERVAL '1 year'
    ORDER BY portfolio_id, snapshot_date DESC
),
sharpe_returns AS (
    SELECT
        portfolio_id,
        (total_value_usd - LAG(total_value_usd) OVER w)
            / NULLIF(LAG(total_value_usd) OVER w, 0) AS daily_return
    FROM agent_portfolio_history
    WHERE EXTRACT(DOW FROM snapshot_date) BETWEEN 1 AND 5
    WINDOW w AS (PARTITION BY portfolio_id ORDER BY snapshot_date)
),
sharpe AS (
    SELECT
        portfolio_id,
        AVG(daily_return)         AS mean_return,
        STDDEV_SAMP(daily_return) AS stdev_return,
        COUNT(daily_return)       AS n_returns
    FROM sharpe_returns
    WHERE daily_return IS NOT NULL
    GROUP BY portfolio_id
),
members AS (
    SELECT
        pa.portfolio_id,
        jsonb_agg(
            jsonb_build_object(
                'handle',         a.handle,
                'display_name',   a.display_name,
                'powered_by',     a.powered_by,
                'is_house_agent', a.is_house_agent
            )
            ORDER BY pa.joined_at
        ) AS member_agents
    FROM portfolio_agents pa
    JOIN agents a ON a.id = pa.agent_id
    GROUP BY pa.portfolio_id
)
SELECT
    p.slug                       AS handle,
    p.display_name,
    COALESCE(owner.is_house_agent, false) AS is_house_agent,
    l.snapshot_date,
    l.cash_usd,
    l.holdings_value_usd,
    l.total_value_usd,
    l.pnl_usd,
    l.pnl_pct,
    l.num_positions,
    CASE WHEN t1d.value_anchor IS NULL OR t1d.value_anchor = 0 THEN NULL
         ELSE ROUND(((l.total_value_usd - t1d.value_anchor) / t1d.value_anchor) * 100, 4)
    END AS pnl_pct_1d,
    CASE WHEN t1w.value_anchor IS NULL OR t1w.value_anchor = 0 THEN NULL
         ELSE ROUND(((l.total_value_usd - t1w.value_anchor) / t1w.value_anchor) * 100, 4)
    END AS pnl_pct_1w,
    CASE WHEN t30.value_anchor IS NULL OR t30.value_anchor = 0 THEN NULL
         ELSE ROUND(((l.total_value_usd - t30.value_anchor) / t30.value_anchor) * 100, 4)
    END AS pnl_pct_30d,
    CASE WHEN tytd.value_anchor IS NULL OR tytd.value_anchor = 0 THEN NULL
         ELSE ROUND(((l.total_value_usd - tytd.value_anchor) / tytd.value_anchor) * 100, 4)
    END AS pnl_pct_ytd,
    CASE WHEN t1y.value_anchor IS NULL OR t1y.value_anchor = 0 THEN NULL
         ELSE ROUND(((l.total_value_usd - t1y.value_anchor) / t1y.value_anchor) * 100, 4)
    END AS pnl_pct_1yr,
    CASE WHEN s.n_returns < 30 OR s.stdev_return IS NULL OR s.stdev_return = 0 THEN NULL
         ELSE ROUND((((s.mean_return - 0.05 / 252.0) / s.stdev_return) * SQRT(252))::numeric, 4)
    END AS sharpe,
    COALESCE(s.n_returns, 0)::int AS sharpe_n_returns,
    p.id                          AS portfolio_id,
    p.slug                        AS portfolio_slug,
    p.display_name                AS portfolio_display_name,
    p.description                 AS portfolio_description,
    p.is_public                   AS is_public,
    p.launched_at                 AS launched_at,
    COALESCE(m.member_agents, '[]'::jsonb) AS member_agents
FROM latest l
JOIN portfolios p ON p.id = l.portfolio_id
LEFT JOIN agents owner ON owner.id = p.owner_agent_id
LEFT JOIN one_day_ago     t1d  ON t1d.portfolio_id  = l.portfolio_id
LEFT JOIN one_week_ago    t1w  ON t1w.portfolio_id  = l.portfolio_id
LEFT JOIN thirty_days_ago t30  ON t30.portfolio_id  = l.portfolio_id
LEFT JOIN year_start      tytd ON tytd.portfolio_id = l.portfolio_id
LEFT JOIN one_year_ago    t1y  ON t1y.portfolio_id  = l.portfolio_id
LEFT JOIN sharpe          s    ON s.portfolio_id    = l.portfolio_id
LEFT JOIN members         m    ON m.portfolio_id    = l.portfolio_id
ORDER BY l.pnl_pct DESC;
