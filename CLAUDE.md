# CLAUDE.md — Equity Screening & Analysis Pipeline

## Project Overview

Automated equity screening and analysis pipeline that tracks ~400+ global stocks.
Integrates TradingView screening, EODHD fundamentals, AI narratives (Claude + Gemini),
and Google Sheets as the primary data store/UI.

AI Analysis is the primary working sheet — all tickers are screened, enriched, scored,
and ranked there. A separate Portfolio sheet (planned) will hold curated selections.

**Spreadsheet ID:** `1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk`

## Architecture

```
04:30 UTC  nightly_screen.py         TradingView screen → add new tickers to AI Analysis
05:00 UTC  eodhd_updater.py          Fetch 20+ financial metrics from EODHD
06:00 UTC  update_ai_narratives.py   Gemini refresh of stale narratives (90+ days)
06:30 UTC  score_ai_analysis.py      Score, rank & sort AI Analysis sheet
Sunday     price_sales_updater.py    Weekly P/S ratio tracking + 52w history
```

## Scripts

### nightly_screen.py (04:30 UTC daily)
3-pass TradingView screener across 35+ markets (Americas, Europe, Asia-Pacific).
Filters: market cap $2B-$500B, gross margin >45%, rev growth 25-500%, revenue >$200M, P/S <15, rating ≤1.8.
Excludes: China, Hong Kong, Taiwan, Real Estate, REIT, Non-Energy Minerals, Finance, Utilities.
Merges Manual sheet tickers. Adds any new tickers to AI Analysis (ticker, exchange, company_name, country, sector).
Also backfills country/sector for existing tickers where missing.

### tv_screen.py (shared module)
TradingView screening logic extracted as a reusable module. Used by both nightly_screen.py
and score_ai_analysis.py to avoid duplicating the 3-pass screening code.

### score_ai_analysis.py (06:30 UTC daily)
Reads AI Analysis + Price-Sales + TradingView market data + Manual sheet.
Computes status and composite_score for every ticker. Updates screening columns
(status, composite_score, price, ps_now, price_%_of_52w_high, perf_52w_vs_spy, rating).
Sorts AI Analysis by status priority then composite score descending.

### eodhd_updater.py (05:00 UTC daily)
Fetches revenue, margins, cash flow, EPS, R40 score from EODHD API.
Updates AI Analysis sheet. Staleness threshold: 7 days. Rate limit: 1s between calls.
Supports `--force` flag to ignore staleness.

### update_ai_narratives.py (06:00 UTC daily)
Refreshes stale narratives (90+ days) using Gemini 2.5 Flash.
Dynamic column detection with header aliases. Injects full financial context into prompt.
When `one_time_events` column has content, generates `event_impact` analysis with traffic-light
indicators: 🔴 flatters (inflates metrics), 🟢 understates (hidden upside), 🟡 neutral/mixed.

### price_sales_updater.py (Sundays 02:00 UTC)
Tracks P/S ratios over time. Backfills 52 weeks of history for new tickers.
Columns: ps_now, 52w_high, 52w_low, 12m_median, ath, %_of_ath, history_json.
Supports `--tickers` and `--force` flags.

### nightly_current_update.py (DEPRECATED)
Legacy script that built the CURRENT sheet. Replaced by nightly_screen.py + score_ai_analysis.py.
Kept for reference but no longer scheduled.

## Google Sheet Tabs

| Tab | Purpose |
|-----|---------|
| AI Analysis | Primary working sheet — screening, fundamentals, AI narratives, scoring — 34+ cols, 2 header rows |
| Price-Sales | Weekly P/S history — 11 cols |
| Manual | User-curated tickers to include (bypasses TradingView filters) |
| Change Log | Manual tracking |
| Email Log | Notification log |
| History | Historical data |
| Logs | Script run audit trail |

## AI Analysis Sheet Column Layout

```
COMPANY:     ticker, exchange, company_name, country, sector, description
SCREENING:   status, composite_score, price, ps_now, price_%_of_52w_high, perf_52w_vs_spy, rating
OVERVIEW:    r40_score, fundamentals_snapshot, short_outlook
REVENUE:     annual_revenue_5y, quarterly_revenue, rev_growth_ttm%, rev_growth_qoq%, rev_cagr%, rev_consistency_score
MARGINS:     gross_margin%, gm_trend%, operating_margin%, net_margin%, net_margin_yoy%, fcf_margin%
EFFICIENCY:  opex_%_of_revenue, s&m+r&d_%_of_revenue, rule_of_40, qrtrs_to_profitability
EARNINGS:    eps_only, eps_yoy%
AI NARRATIVE: full_outlook, key_risks
LAST ANALYSIS: ai, data
```

**Status (auto-assigned):**
- 🟢 Eligible — has dates in both `ai` and `data` columns, no 🔴 flags
- 🏷️ Discount — P/S >20% below 12-month median
- 📌 Manual — manual-only ticker (not in TradingView screen), no flags
- 📌❌ Manual Excluded — manual ticker with 🔴 flags
- 🆕 New — missing `ai` or `data` date
- ❌ Excluded — 🔴 marker on any column; sorted to bottom with note of flagged columns

**Composite score weights:** R40 40%, P/S 25% (inverted), 52w vs SPY 20%, Rating 15% (inverted)
**Penalties:** 🔴 outlook ×0.25, 🟡 outlook ×0.50, 🟡 flags on any column ×0.50

## Key Constants

- `STALENESS_DAYS = 7` (eodhd_updater) / `90` (update_ai_narratives)
- `DELAY_BETWEEN_CALLS = 1-2s` (API rate limiting)
- `NULL_VALUE = "—"` (em-dash for missing data)

## Environment Variables

```
SPREADSHEET_ID              Google Sheet ID (has default)
GOOGLE_SERVICE_ACCOUNT_JSON Service account credentials (JSON string)
GEMINI_API_KEY              Gemini API (update_ai_narratives.py)
SERP_API_KEY / SERPAPI_API_KEY  SerpAPI web search
EODHD_API_KEY               EODHD financial data
```

## Development Notes

- All scheduling is via GitHub Actions (`.github/workflows/`)
- Google Sheets is the sole data store — no database
- Scripts use `google-api-python-client` for batch operations
- TradingView screening uses the `tradingview-screener` library (3-pass by geography)
- 70+ exchange code mappings (TradingView → Google Finance, spreadsheet → EODHD)
- Scripts read headers dynamically with alias support; `HEADER_ALIASES` maps legacy names to current keys
- Column `ticker_clean` is aliased to `ticker` for backward compatibility

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run individual scripts
python nightly_screen.py                   # TradingView screen → add to AI Analysis
python eodhd_updater.py                    # fetch EODHD financial data
python eodhd_updater.py --force            # ignore staleness
python update_ai_narratives.py             # refresh AI narratives
python score_ai_analysis.py                # score + rank AI Analysis
python price_sales_updater.py              # weekly P/S update
python price_sales_updater.py --tickers NVDA,AAPL --force
```

## Coding Conventions

- Logging via `logging` module, INFO level by default
- All sheet writes use batch API for efficiency
- Exchange mappings must be kept in sync across scripts (TV, EODHD, Google Finance)
- Use `clean_ticker()` from `tv_screen.py` to normalize ticker symbols from TradingView
- Sanitize NaN/None before writing to Sheets API (causes 400 errors)
- Header aliases: all scripts support `HEADER_ALIASES` dict to map legacy/alternative column names
