# CLAUDE.md — Equity Screening & Analysis Pipeline

## Project Overview

Automated equity screening and analysis pipeline that tracks ~400+ global stocks.
Integrates TradingView screening, EODHD fundamentals, AI narratives (Claude + Gemini),
and Google Sheets as the primary data store/UI.

**Spreadsheet ID:** `1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk`

## Architecture

```
02:00 UTC  update_ai_analysis.py     Claude AI narratives for blank descriptions
04:30 UTC  sync_companies.py         Sync new tickers CURRENT → AI Analysis
05:00 UTC  eodhd_updater.py          Fetch 20+ financial metrics from EODHD
06:00 UTC  update_ai_narratives.py   Gemini refresh of stale narratives (90+ days)
06:30 UTC  nightly_current_update.py TradingView screen → enrich → write CURRENT
Sunday     price_sales_updater.py    Weekly P/S ratio tracking + 52w history
Manual     rebuild_current_sheet.py  One-time CURRENT sheet structure rebuild
```

## Scripts

### nightly_current_update.py (06:30 UTC daily)
3-pass TradingView screener across 35+ markets (Americas, Europe, Asia-Pacific).
Filters: market cap $2B-$500B, gross margin >45%, rev growth 20-500%, revenue >$200M, P/S <10, rating ≤1.8.
Excludes: China, Hong Kong, Taiwan, Real Estate, REIT, Non-Energy Minerals, Finance, Utilities.
Enriches with AI Analysis + Price-Sales data, computes composite_score, writes CURRENT tab.
**Manual-only columns (never overwritten):** deep_dive, conviction_tier, next_earnings, status.

### eodhd_updater.py (05:00 UTC daily)
Fetches revenue, margins, cash flow, EPS, R40 score from EODHD API.
Updates AI Analysis sheet. Staleness threshold: 7 days. Rate limit: 1s between calls.
Supports `--force` flag to ignore staleness.

### update_ai_analysis.py (02:00 UTC daily)
Generates AI narratives via Claude Opus for tickers with blank descriptions.
Uses SerpAPI for 3 web searches per ticker (earnings, news, risks).
Produces: description, short_outlook, full_outlook, key_risks.

### update_ai_narratives.py (06:00 UTC daily)
Refreshes stale narratives (90+ days) using Gemini 2.5 Flash.
Dynamic column detection with header aliases. Injects full financial context into prompt.

### price_sales_updater.py (Sundays 02:00 UTC)
Tracks P/S ratios over time. Backfills 52 weeks of history for new tickers.
Columns: ps_now, 52w_high, 52w_low, 12m_median, ath, %_of_ath, history_json.
Supports `--tickers` and `--force` flags.

### sync_companies.py (04:30 UTC daily)
Adds new tickers from CURRENT to AI Analysis sheet (ticker, company_name, exchange).

### rebuild_current_sheet.py (manual)
Rebuilds CURRENT sheet structure with proper headers, formatting, merges.
Post-rebuild validation via `scripts/recalc.py`.

## Google Sheet Tabs

| Tab | Purpose |
|-----|---------|
| CURRENT | Main portfolio view — 20 cols (A-T), row 1 category merges, row 2 headers, row 3+ data |
| AI Analysis | Ticker fundamentals + AI narratives — 27+ cols, 2 header rows |
| Price-Sales | Weekly P/S history — 11 cols |
| Change Log | Manual tracking |
| Email Log | Notification log |
| History | Historical data |
| Logs | Script run audit trail |

## CURRENT Sheet Column Layout (V2)

```
A: deep_dive (manual)     K: short_outlook
B: status (emoji)         L: price
C: conviction_tier        M: ps_now (→ Price-Sales link)
D: ticker (→ Google Fin)  N: price_%_of_52w_high
E: company_name           O: r40_score
F: exchange               P: perf_52w_vs_spy
G: country                Q: rating
H: sector                 R: next_earnings (manual)
I: description            S: days_on_list (formula)
J: fundamentals_snapshot  T: composite_score (formula)
```

**Status emoji priority:** 🟢 Eligible > 🆕 New > 🟡 Watching > ⚫ On Hold > 🔴 Pending > ❌ Exiting

**Composite score weights:** R40 40%, P/S 25% (inverted), 52w vs SPY 20%, Rating 15% (inverted)

## Key Constants

- `STALENESS_DAYS = 7` (eodhd_updater) / `90` (update_ai_narratives)
- `DELAY_BETWEEN_CALLS = 1-2s` (API rate limiting)
- `NULL_VALUE = "—"` (em-dash for missing data)

## Environment Variables

```
SPREADSHEET_ID              Google Sheet ID (has default)
GOOGLE_SERVICE_ACCOUNT_JSON Service account credentials (JSON string)
ANTHROPIC_API_KEY           Claude API (update_ai_analysis.py)
GEMINI_API_KEY              Gemini API (update_ai_narratives.py)
SERP_API_KEY / SERPAPI_API_KEY  SerpAPI web search
EODHD_API_KEY               EODHD financial data
```

## Development Notes

- All scheduling is via GitHub Actions (`.github/workflows/`)
- Google Sheets is the sole data store — no database
- Scripts use `gspread` for auth and `google-api-python-client` for batch operations
- TradingView screening uses the `tradingview-screener` library (3-pass by geography)
- 70+ exchange code mappings (TradingView → Google Finance, spreadsheet → EODHD)
- Hyperlinks: ticker → Google Finance, fundamentals_snapshot → AI Analysis row, ps_now → Price-Sales row
- Scripts read headers dynamically where possible; column indices are fragile — update constants if layout changes

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run individual scripts
python nightly_current_update.py
python eodhd_updater.py
python eodhd_updater.py --force          # ignore staleness
python update_ai_analysis.py
python update_ai_narratives.py
python price_sales_updater.py
python price_sales_updater.py --tickers NVDA,AAPL --force
python sync_companies.py
python rebuild_current_sheet.py
python scripts/recalc.py                 # validate CURRENT structure
```

## Coding Conventions

- Logging via `logging` module, INFO level by default
- All sheet writes use batch API for efficiency
- Manual-override columns are read-before-write to preserve user edits
- Exchange mappings must be kept in sync across scripts (TV, EODHD, Google Finance)
- Use `clean_ticker()` to normalize ticker symbols from TradingView
- Sanitize NaN/None before writing to Sheets API (causes 400 errors)
