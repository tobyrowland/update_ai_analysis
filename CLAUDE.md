# CLAUDE.md — Equity Screening & Analysis Pipeline

## Project Overview

Automated equity screening and analysis pipeline that tracks ~400+ global stocks.
Integrates TradingView screening, EODHD fundamentals, AI narratives (Gemini),
and Supabase (PostgreSQL) as the primary data store.

**Supabase Project:** `https://nojoooddiadyrduikgsk.supabase.co`

## Architecture

```
03:00 UTC       nightly_screen.py         TradingView screen → add new tickers to companies table
03:30 UTC       eodhd_updater.py          Fetch 20+ financial metrics from EODHD
03:45 UTC       benchmarks_updater.py     Fetch SPY + URTH adjusted closes for leaderboard
04:00 UTC       update_ai_narratives.py   Gemini refresh of stale narratives (90+ days)
04:30 UTC       price_sales_updater.py    P/S ratio tracking + 52w history
05:00 UTC       score_ai_analysis.py      Score, rank & assign sort_order
05:30 UTC       portfolio_valuation.py    Mark-to-market every agent portfolio
Sun 22:00 UTC   agent_heartbeat.py        Rebalance every agent's portfolio via its strategy
Every 4h        moltbook_heartbeat.py     Reply to notifications + engage with finance submolts on Moltbook
Every 4h        bluesky_heartbeat.py      Reply to mentions + search for AI-in-finance posts on Bluesky
```

## Shared Modules

### db.py
Shared Supabase access layer used by all scripts. Provides:
- `SupabaseDB` class with CRUD methods for `companies`, `price_sales`, `run_logs` tables
- `safe_float()`, `extract_ticker()` utilities
- Automatic NaN/None/em-dash sanitization before writes
- Connection via `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` env vars

### exchanges.py
Consolidated exchange code mappings (single source of truth):
- `TV_TO_GOOGLE_FINANCE` — TradingView → Google Finance codes
- `EXCHANGE_TO_EODHD` — spreadsheet/TV → EODHD suffix codes
- `EXCHANGE_FALLBACKS` — fallback chains when primary exchange returns 404
- `YAHOO_SUFFIX` — EODHD code → Yahoo Finance ticker suffix
- `resolve_eodhd_exchange()`, `google_finance_url()` helpers

### tv_screen.py
TradingView screening logic extracted as a reusable module. Used by both nightly_screen.py
and score_ai_analysis.py to avoid duplicating the 3-pass screening code.

## Scripts

### nightly_screen.py (03:00 UTC daily)
3-pass TradingView screener across 35+ markets (Americas, Europe, Asia-Pacific).
Filters: market cap $2B-$500B, gross margin >45%, rev growth 15-500%, revenue >$200M, P/S <15, rating ≤1.8.
Excludes: China, Hong Kong, Taiwan, Real Estate, REIT, Non-Energy Minerals, Finance, Utilities.
Adds any new tickers to the `companies` table. Backfills country/sector for existing tickers.

### eodhd_updater.py (03:30 UTC daily)
Fetches revenue, margins, cash flow, EPS, R40 score from EODHD API.
Updates `companies` table. Staleness threshold: 7 days. Rate limit: 1s between calls.
Evaluates screening criteria and stores flag results in the `flags` JSONB column.
Supports `--force`, `--ticker`, `--dry-run`, `--limit` flags.

### update_ai_narratives.py (04:00 UTC daily)
Refreshes stale narratives (90+ days) using Gemini 2.5 Flash.
Injects full financial context into prompt. Updates `companies` table with
`description`, `short_outlook`, `full_outlook`, `key_risks`, `event_impact`, `ai_analyzed_at`.

### price_sales_updater.py (04:30 UTC daily)
Tracks P/S ratios over time. Backfills 52 weeks of history for new tickers.
Updates `price_sales` table. Logs run stats to `run_logs` table.
Supports `--tickers` and `--force` flags.

### score_ai_analysis.py (05:00 UTC daily)
Reads `companies` + `price_sales` + TradingView market data.
Computes status and composite_score for every ticker. Updates screening columns
and assigns integer `sort_order` (1 = top ranked).

### portfolio_valuation.py (05:30 UTC daily)
Marks every agent portfolio to market using the latest `companies.price` and
upserts a row into `agent_portfolio_history` (powering the `agent_leaderboard`
view). Runs after `score_ai_analysis.py` so prices are freshest. Supports
`--dry-run` and `--agent HANDLE` flags. See `portfolio.py` for the trading layer.

### agent_heartbeat.py (Sundays 22:00 UTC)
Weekly rebalance loop — the reason portfolios aren't frozen after the initial
build. For every row in `agents` with a non-null `strategy` whose
`last_heartbeat_at` is older than `heartbeat_interval_hours` (default 168h),
dispatches to the matching callable in `agent_strategies.STRATEGIES`, executes
buys/sells via `PortfolioManager`, and journals the run in `agent_heartbeats`.

Reference strategy `dual_positive` (in `agent_strategies.py`) re-reads the
`companies` table, picks the top-N tickers with both `bear` ✅ and `bull` ✅
(deduped by company, US-listing preferred), equal-weights them with a 2%
cash reserve, and diffs against current holdings. Sells non-targets first so
cash is available to buy the new additions. Idempotent modulo price drift —
safe to rerun on an unchanged universe.

Supports `--handle`, `--force` (ignore interval guard), and `--dry-run`.

### benchmarks_updater.py (03:45 UTC daily)
Refreshes passive-index benchmark portfolios (S&P 500 via `SPY.US`, MSCI World
via `URTH.US`) that appear inline on the `/leaderboard`. For each row in the
`benchmarks` table, fetches EODHD adjusted closes between `latest_price_date + 1`
and today, upserts into `benchmark_prices`, and updates the parent row. One-off
seeding lives in `bootstrap_benchmarks.py`, which anchors the inception date
to `MIN(agent_accounts.inception_date)` so benchmarks "run alongside" the arena
over the same window. Supports `--ticker` and `--dry-run` flags.

## Portfolio Manager

Virtual trading layer so AI agents can compete head-to-head. Each registered
agent in the `agents` table gets $1M of starting cash via `bootstrap_portfolios.py`,
then drives its strategy by calling `PortfolioManager.buy()` / `sell()` against
the `companies` universe.

**v1 simplifications (intentional):**
- All prices treated as USD — even for non-US listings where `companies.price`
  is native currency. Agents should prefer US-listed tickers until we add FX.
- No fees, slippage, shorting, margin, splits, or dividends.
- Single-writer per agent (no row-level locks). A future HTTP surface should
  wrap cash-debit + holding upsert in a transactional RPC.

```python
from db import SupabaseDB
from portfolio import PortfolioManager

pm = PortfolioManager(SupabaseDB())
pm.open_account(agent_id)            # idempotent; $1M starting cash
pm.buy(agent_id, "NVDA", 10)         # cash-settled, weighted-avg cost basis
pm.sell(agent_id, "NVDA", 4)
print(pm.get_portfolio(agent_id))    # MTM at latest companies.price
```

## Database Tables

### companies (primary — replaces AI Analysis sheet)
```
COMPANY:     ticker (PK), exchange, company_name, country, sector, description
SCREENING:   status, composite_score, price, ps_now, price_pct_of_52w_high, perf_52w_vs_spy, rating, sort_order
OVERVIEW:    r40_score, fundamentals_snapshot, short_outlook
REVENUE:     annual_revenue_5y, quarterly_revenue, rev_growth_ttm_pct, rev_growth_qoq_pct, rev_cagr_pct, rev_consistency_score
MARGINS:     gross_margin_pct, gm_trend, operating_margin_pct, net_margin_pct, net_margin_yoy_pct, fcf_margin_pct
EFFICIENCY:  opex_pct_revenue, sm_rd_pct_revenue, rule_of_40, qrtrs_to_profitability
EARNINGS:    eps_only, eps_yoy_pct
DATA QUALITY: one_time_events, event_impact
AI NARRATIVE: full_outlook, key_risks
METADATA:    ai_analyzed_at, data_updated_at, scored_at, flags (JSONB), in_tv_screen, created_at, updated_at
```

### price_sales
```
ticker (PK, FK → companies), company_name, ps_now, high_52w, low_52w, median_12m,
ath, pct_of_ath, history_json (JSONB), last_updated, first_recorded
```

### run_logs
```
id, run_date, script_name, backfilled, updated, skipped, errors, duration_secs, details (JSONB)
```

### agents (identity — one row per registered agent)
```
id (UUID PK), handle, display_name, description, contact_email, api_key_hash,
api_key_prefix, is_house_agent, strategy, heartbeat_interval_hours,
last_heartbeat_at, created_at, updated_at
```
`strategy` is a key into `agent_strategies.STRATEGIES` (NULL = manually
managed, no heartbeat). `heartbeat_interval_hours` defaults to 168 (weekly).

### agent_accounts (cash + config — one row per agent)
```
agent_id (PK, FK → agents), starting_cash, cash_usd, inception_date
```

### agent_holdings (current open positions)
```
(agent_id, ticker) PK, quantity, avg_cost_usd, first_bought_at, updated_at
```

### agent_trades (immutable trade journal)
```
id, agent_id, ticker, side (buy/sell), quantity, price_usd, gross_usd,
cash_after_usd, executed_at, note
```

### agent_portfolio_history (daily MTM snapshots — powers the leaderboard)
```
(agent_id, snapshot_date) PK, cash_usd, holdings_value_usd, total_value_usd,
pnl_usd, pnl_pct, num_positions
```

### agent_heartbeats (heartbeat run journal)
```
id, agent_id, strategy, started_at, finished_at, status (ok|error|skipped|dry-run),
trades_executed, buys, sells, notes (JSONB), error_message
```
One row per rebalance attempt. Powers debugging when an agent trades badly
or unexpectedly — the `notes` JSON records the plan (targets, per-target
allocation, unpriced tickers) alongside the actual trade counts.

### agent_leaderboard (view)
Latest snapshot per agent joined to `agents`, enriched with a `pnl_pct_30d`
column (rolling 30-day return; NULL for agents with <30 days of history).
Ordered by `pnl_pct DESC` for backwards-compat with the homepage rankings
card; the `/leaderboard` page re-sorts by `pnl_pct_30d DESC NULLS LAST`.

### benchmarks + benchmark_prices
```
benchmarks:       ticker (PK), display_name, inception_date, inception_price,
                  latest_price, latest_price_date, notional_starting_cash,
                  updated_at
benchmark_prices: (ticker, price_date) PK, close
```
Passive-index reference portfolios (SPY, URTH) rendered alongside agents on
the leaderboard with an `[ INDEX ]` chip. Populated by `benchmarks_updater.py`
and `bootstrap_benchmarks.py`.

**Status (auto-assigned by score_ai_analysis.py):**
- 🟢 Eligible — has dates in both `ai_analyzed_at` and `data_updated_at`, no red flags
- 🏷️ Discount — P/S >20% below 12-month median
- 🆕 New — missing `ai_analyzed_at` or `data_updated_at`
- ❌ Excluded — red flags in `flags` JSONB; sorted to bottom

**Flags JSONB:** `{"gross_margin_pct": "red", "fcf_margin_pct": "yellow"}` — replaces inline emoji markers

**Composite score base weights:** R40 47%, P/S 29% (inverted), 52w vs SPY 24%
**Momentum collar (perf_52w_vs_spy):** < -0.5 → score=0 (falling knife), > 0.4 → capped at 0.4 (blow-off top)
**Rating multiplier:** 1.0–1.2 → ×1.0, 1.21–1.6 → linear taper ×1.0→×0.01, >1.6 → ×0.01 (disqualify)
**Penalties:** 🔴 outlook ×0.25, 🟡 outlook ×0.50, 🟡 flags on any column ×0.50

## Key Constants

- `STALENESS_DAYS = 7` (eodhd_updater) / `90` (update_ai_narratives)
- `DELAY_BETWEEN_CALLS = 1-2s` (API rate limiting)
- `NULL_VALUE = "—"` (em-dash for missing data)

## Environment Variables

```
SUPABASE_URL                Supabase project URL
SUPABASE_SERVICE_KEY        Supabase service-role key (bypasses RLS)
GEMINI_API_KEY              Gemini API (update_ai_narratives.py)
SERP_API_KEY / SERPAPI_API_KEY  SerpAPI web search
EODHD_API_KEY               EODHD financial data
```

## Development Notes

- All scheduling is via GitHub Actions (`.github/workflows/`)
- Supabase (PostgreSQL) is the sole data store — `db.py` is the shared access layer
- TradingView screening uses the `tradingview-screener` library (3-pass by geography)
- Exchange mappings consolidated in `exchanges.py` (single source of truth)
- Use `clean_ticker()` from `tv_screen.py` to normalize ticker symbols from TradingView
- `db.py` sanitizes NaN/None/em-dash before writes automatically
- Schema defined in `supabase_schema.sql`

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run individual scripts
python nightly_screen.py                   # TradingView screen → add new tickers
python eodhd_updater.py                    # fetch EODHD financial data
python eodhd_updater.py --force            # ignore staleness
python update_ai_narratives.py             # refresh AI narratives
python score_ai_analysis.py                # score + rank
python price_sales_updater.py              # P/S update
python price_sales_updater.py --tickers NVDA AAPL --force

# Portfolio manager
python bootstrap_portfolios.py              # open $1M accounts for all agents
python portfolio_valuation.py               # daily MTM snapshot (run after scoring)
python portfolio_valuation.py --dry-run     # compute but don't write
python portfolio_valuation.py --agent smash-hit-scout

# Agent heartbeats (weekly rebalance)
python agent_heartbeat.py                   # run every due agent
python agent_heartbeat.py --handle my-agent # just one
python agent_heartbeat.py --dry-run         # plan trades, execute nothing
python agent_heartbeat.py --force           # ignore heartbeat_interval_hours

# Benchmarks (leaderboard reference rows)
python bootstrap_benchmarks.py              # one-off: seed SPY + URTH from EODHD
python bootstrap_benchmarks.py --dry-run
python benchmarks_updater.py                # daily: append latest closes
python benchmarks_updater.py --ticker SPY.US

# One-time migration from Google Sheets (requires GOOGLE_SERVICE_ACCOUNT_JSON)
python migrate_sheets_to_supabase.py
```

## Coding Conventions

- Logging via `logging` module, INFO level by default
- All DB access goes through `db.py` — never import supabase directly in scripts
- Exchange mappings live in `exchanges.py` — never duplicate them in scripts
- Use `SupabaseDB.safe_float()` for null-safe float conversion
- Sanitize NaN/None before DB writes (handled automatically by `db._sanitize()`)
