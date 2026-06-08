-- Migration 045: agent team builder (portfolio & agents brief v2).
--
-- The portfolio page becomes the user's home base: they assemble a TEAM of
-- agents by dragging ready-made strategies out of a library, and the moment
-- each is saved it goes live and trades the portfolio. There is no mandate to
-- write and no batch "deploy" step — the strategy lives inside the agents the
-- user picks. This migration adds the agent-library taxonomy the new page
-- reads, plus a per-instance enable switch (Run/Stop), and seeds an example
-- roster so the library isn't empty. The real roster is curated separately;
-- this is the SYSTEM, the seeds are illustrative.
--
-- Two axes (brief §3), kept deliberately separate:
--   * ACTION (structure) — buy | sell | manage. The only grouping. Mechanically
--     true, never inferred: buy adds exposure, sell reduces it, manage does
--     neither cleanly (rebalancers / sizers).
--   * TRIGGERS (advice) — declared intent tags on sells (caps-losses,
--     banks-gains). A small fixed vocabulary, additive, author-declared; the
--     system never detects them. Drives the readiness strip's reasoning.
--
-- Each library agent also ships a plain-language SENTENCE template that
-- interpolates its PARAMS (1-2 typed, bounded controls with sensible
-- defaults), so the configured agent says what it will do in English.
--
-- Additive & idempotent. Paste-and-run in the Supabase SQL editor.

-- ============================================================
-- 1. agents — library taxonomy columns
-- ============================================================
-- action: 'buy' | 'sell' | 'manage' — NULL for non-library agents (the legacy
--   bear/bull sentinels, pipeline house agents, community agents).
-- triggers: declared intent tags (sells only), e.g. {caps-losses,banks-gains}.
-- param_schema: ordered list of typed, bounded controls the quick-config
--   renders. Each item: {key,label,type,min,max,step,unit,default,options}.
-- sentence_template: plain-language description with {key} placeholders the UI
--   fills live from the param values.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS action            TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS triggers          TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS param_schema      JSONB  NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS sentence_template TEXT;

ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_action_check;
ALTER TABLE agents ADD  CONSTRAINT agents_action_check
    CHECK (action IS NULL OR action IN ('buy', 'sell', 'manage'));

-- The library is exactly the hireable agents that declare an action.
CREATE INDEX IF NOT EXISTS idx_agents_library
    ON agents (action) WHERE action IS NOT NULL;

-- ============================================================
-- 2. portfolio_agents — per-instance enable switch (Run/Stop)
-- ============================================================
-- A saved (deployed) team agent is a portfolio_agents row. Its per-instance
-- params live in `config` (flat keys, merged into the strategy's params by the
-- heartbeat, exactly like agents.config). `enabled` is the Run/Stop toggle:
-- a stopped agent stays on the team but is skipped by the heartbeat.
ALTER TABLE portfolio_agents ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- A manage-action member maps to role 'manager' (alongside 041's buyer/reviewer).
-- role stays free-text TEXT, so no constraint change is needed.

-- ============================================================
-- 3. Seed an example library (brief screenshots — illustrative)
-- ============================================================
-- Function-first identity: the NAME is the strategy, the LLM is a secondary
-- "powered by" line. Each is a house agent, available_for_hire, with a real
-- strategy binding so the team actually trades:
--   buy    -> watchlist_buyer   (mechanical, draft from the screen candidates)
--   sell   -> portfolio_reviewer (sell-side reviewer)
--   manage -> (no engine yet; runs inert until a manage engine is defined)
-- The signal nuance each name implies (200-week MA, ATR, etc.) is a property of
-- the agent definition, curated separately — these seeds wire the SYSTEM.

INSERT INTO agents (
    handle, display_name, description, is_house_agent, available_for_hire,
    api_key_hash, api_key_prefix, powered_by, strategy,
    action, triggers, param_schema, sentence_template
) VALUES
-- ---- BUY ----------------------------------------------------------------
(
    'agent-quality-compounder', 'Quality Compounder',
    'Buys elite Rule-of-40 compounders from the screen, sized up to a per-position cap.',
    TRUE, TRUE, 'house-agent', 'ak_house_qc', 'Claude Opus 4.8', 'watchlist_buyer',
    'buy', '{}',
    '[{"key":"maxPos","label":"Max per position","type":"number","min":2,"max":10,"step":1,"unit":"%","default":5}]'::jsonb,
    'Buys elite Rule-of-40 compounders, up to {maxPos}% per position.'
),
(
    'agent-momentum-breakout', 'Momentum Breakout',
    'Buys names breaking out to new multi-week highs.',
    TRUE, TRUE, 'house-agent', 'ak_house_mb', 'Grok 4.3', 'watchlist_buyer',
    'buy', '{}',
    '[{"key":"window","label":"Breakout window","type":"select","default":52,"options":[{"value":26,"label":"26 weeks"},{"value":52,"label":"52 weeks"},{"value":104,"label":"104 weeks"}]}]'::jsonb,
    'Buys names breaking out to new {window}-week highs.'
),
(
    'agent-deep-value-sniper', 'Deep-Value Sniper',
    'Buys quality names trading at a low multiple of sales.',
    TRUE, TRUE, 'house-agent', 'ak_house_dv', 'GPT-5', 'watchlist_buyer',
    'buy', '{}',
    '[{"key":"ps","label":"Max P/S","type":"number","min":1,"max":15,"step":0.5,"unit":"x","default":3}]'::jsonb,
    'Buys quality names trading below {ps}x sales.'
),
(
    'agent-dip-buyer', 'Dip Buyer',
    'Buys quality names that have pulled back from their recent high.',
    TRUE, TRUE, 'house-agent', 'ak_house_db', 'Gemini 2.5 Pro', 'watchlist_buyer',
    'buy', '{}',
    '[{"key":"dip","label":"Minimum pullback","type":"number","min":5,"max":40,"step":1,"unit":"%","default":10}]'::jsonb,
    'Buys quality names that have pulled back at least {dip}% from their high.'
),
(
    'agent-200w-reversion', '200-Week Reversion',
    'Buys quality names trading well below their long-run 200-week average.',
    TRUE, TRUE, 'house-agent', 'ak_house_2w', 'Claude Opus 4.8', 'watchlist_buyer',
    'buy', '{}',
    '[{"key":"discount","label":"Discount to 200w avg","type":"number","min":5,"max":50,"step":1,"unit":"%","default":15}]'::jsonb,
    'Buys quality names trading at least {discount}% below their 200-week average.'
),
-- ---- SELL ---------------------------------------------------------------
(
    'agent-hard-stop-loss', 'Hard Stop-Loss',
    'Sells any holding that falls a fixed depth below its entry price.',
    TRUE, TRUE, 'house-agent', 'ak_house_hs', 'Gemini 2.5 Pro', 'portfolio_reviewer',
    'sell', '{caps-losses}',
    '[{"key":"depth","label":"Stop depth","type":"number","min":5,"max":40,"step":1,"unit":"%","default":12}]'::jsonb,
    'Sells any holding that falls {depth}% below its entry price.'
),
(
    'agent-trailing-stop', 'Trailing Stop',
    'Sells a holding when it drops a set amount from its high since purchase.',
    TRUE, TRUE, 'house-agent', 'ak_house_ts', 'GPT-5', 'portfolio_reviewer',
    'sell', '{caps-losses,banks-gains}',
    '[{"key":"trail","label":"Trail distance","type":"number","min":5,"max":40,"step":1,"unit":"%","default":15}]'::jsonb,
    'Sells a holding when it drops {trail}% from its high since you bought it.'
),
(
    'agent-target-trimmer', 'Target Trimmer',
    'Trims part of a position once it reaches a profit target.',
    TRUE, TRUE, 'house-agent', 'ak_house_tt', 'DeepSeek V3', 'portfolio_reviewer',
    'sell', '{banks-gains}',
    '[{"key":"trimPct","label":"Trim size","type":"number","min":10,"max":100,"step":5,"unit":"%","default":50},{"key":"gain","label":"Profit target","type":"number","min":10,"max":100,"step":5,"unit":"%","default":25}]'::jsonb,
    'Trims {trimPct}% of a position once it is up {gain}%.'
),
(
    'agent-time-based-exit', 'Time-Based Exit',
    'Sells any holding a fixed number of days after it was bought.',
    TRUE, TRUE, 'house-agent', 'ak_house_te', 'Grok 4.3', 'portfolio_reviewer',
    'sell', '{}',
    '[{"key":"hold","label":"Holding period","type":"number","min":7,"max":365,"step":1,"unit":" days","default":90}]'::jsonb,
    'Sells any holding {hold} days after it was bought.'
),
(
    'agent-volatility-stop', 'Volatility Stop',
    'Sells a holding when it falls a multiple of its recent volatility below entry.',
    TRUE, TRUE, 'house-agent', 'ak_house_vs', 'Claude Opus 4.8', 'portfolio_reviewer',
    'sell', '{caps-losses}',
    '[{"key":"mult","label":"Volatility multiple","type":"number","min":1,"max":6,"step":0.5,"unit":"x","default":3}]'::jsonb,
    'Sells a holding when it falls {mult}x its recent volatility below entry.'
),
-- ---- MANAGE -------------------------------------------------------------
(
    'agent-equal-weight-balancer', 'Equal-Weight Balancer',
    'Rebalances toward equal weight whenever a position drifts out of line.',
    TRUE, TRUE, 'house-agent', 'ak_house_eb', 'Claude Opus 4.8', NULL,
    'manage', '{}',
    '[{"key":"drift","label":"Drift tolerance","type":"number","min":1,"max":20,"step":1,"unit":"%","default":5}]'::jsonb,
    'Rebalances toward equal weight whenever a position drifts {drift}% out of line.'
),
(
    'agent-risk-parity-sizer', 'Risk Parity Sizer',
    'Sizes each position so they contribute equal risk, rechecked on a cadence.',
    TRUE, TRUE, 'house-agent', 'ak_house_rp', 'Grok 4.3', NULL,
    'manage', '{}',
    '[{"key":"cadence","label":"Recheck cadence","type":"select","default":"weekly","options":[{"value":"weekly","label":"weekly"},{"value":"monthly","label":"monthly"}]}]'::jsonb,
    'Sizes each position so they contribute equal risk, rechecked {cadence}.'
)
ON CONFLICT (handle) DO UPDATE SET
    display_name      = EXCLUDED.display_name,
    description       = EXCLUDED.description,
    available_for_hire = EXCLUDED.available_for_hire,
    powered_by        = EXCLUDED.powered_by,
    strategy          = EXCLUDED.strategy,
    action            = EXCLUDED.action,
    triggers          = EXCLUDED.triggers,
    param_schema      = EXCLUDED.param_schema,
    sentence_template = EXCLUDED.sentence_template,
    updated_at        = NOW();
