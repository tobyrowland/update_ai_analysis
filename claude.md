# CLAUDE.md — Stock Screening Project

## Purpose

Automated **"Revenue Builders"** stock screening system. Identifies mid-cap companies ($2B–$500B) with strong unit economics (>45% gross margin, >20% revenue growth) that are on a path toward profitability. The thesis: companies with high gross margins and consistent revenue growth will eventually reach a profitability inflection — the screen finds them before the market prices it in.

This is **not investment advice**. Research tool only.

## Architecture

Three Python scripts run on GitHub Actions, all writing to a single Google Sheet (`EJ2N`):

```
┌─────────────────────────────────────────────────────┐
│  GitHub Actions (Ubuntu 24, Python 3.11)            │
│                                                     │
│  1. main.py          — nightly                      │
│     TradingView screen → enrich → write CURRENT     │
│                                                     │
│  2. ai_analysis_updater.py — nightly                │
│     SerpAPI search → Claude analysis → AI Analysis  │
│                                                     │
│  3. price_sales_updater.py — weekly (Sunday 02:00)  │
│     EODHD + Yahoo Finance → Price-Sales history     │
└─────────────────┬───────────────────────────────────┘
                  │ Google Sheets API
                  ▼
┌─────────────────────────────────────────────────────┐
│  Google Sheet: EJ2N                                 │
│  Tabs: CURRENT | AI Analysis | Price-Sales |        │
│        Change Log | Email Log | History | Logs      │
└─────────────────────────────────────────────────────┘
```

### Legacy System (being phased out)

An older n8n-based pipeline exists with:
- `stock_screener_revenue_builders.py` — sends payload to n8n webhook
- `build_new_current_n8n.js` — n8n classification/sorting node
- Create History, email filter, email composition nodes

The new system (main.py + ai_analysis_updater.py + price_sales_updater.py) replaces this by writing directly to Google Sheets.

## Tech Stack

| Component | Library/Service |
|---|---|
| Screening | `tradingview-screener` (Python). Uses `Query()` and `col()` |
| Market data | EODHD API (fundamentals, P/S), Yahoo Finance (weekly prices) |
| AI analysis | Anthropic Claude API (`claude-opus-4-5`), SerpAPI for web context |
| Data store | Google Sheets API (`google-api-python-client`, `gspread`) |
| Scheduling | GitHub Actions cron |
| Scoring | `scipy.stats.percentileofscore` for composite ranking |

## Environment Variables

```
GOOGLE_SERVICE_ACCOUNT_JSON  # Service account JSON for Sheets API
SPREADSHEET_ID               # Google Sheet ID (default: 1js3dUTJ...)
EODHD_API_KEY                # EODHD market data API
SERP_API_KEY                 # SerpAPI for web searches
ANTHROPIC_API_KEY            # Claude API key
```

## Key Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run nightly CURRENT update
python main.py

# Run AI analysis (processes tickers with blank description)
python ai_analysis_updater.py

# Run P/S updater (weekly)
python price_sales_updater.py
python price_sales_updater.py --tickers DDOG SNOW  # test specific tickers
python price_sales_updater.py --force               # force update all
```

## TradingView Screen Filters

```python
col("market_cap_basic").between(2_000_000_000, 500_000_000_000)
col("gross_profit_margin_fy") > 45          # Strong unit economics
col("total_revenue_yoy_growth_ttm").between(20, 500)  # Growing, not spiky
col("total_revenue_ttm") > 100_000_000     # Not pre-revenue
col("price_revenue_ttm") < 10              # Not hyper-premium
col("recommendation_mark") <= 1.8           # Analyst conviction
```

Target output: **~80 stocks**. If count drifts much higher, tighten P/S cap or rating first.

Exclusions: China/HK/Taiwan, REITs/Real Estate, Non-Energy Minerals (gold miners).

The screener runs 3 passes: Americas → Europe/Middle East/Africa → Asia-Pacific, deduplicating across passes.

## Google Sheet Tab Schemas

### CURRENT (20 columns, grouped headers)

The main view. Row 1 = category headers, Row 2 = column names, Row 3+ = data.

| Group | Columns |
|---|---|
| STATUS | deep_dive, status, conviction_tier |
| IDENTITY | ticker, company_name, exchange, country, sector |
| NARRATIVE | description, fundamentals_snapshot, short_outlook |
| VALUATION | price, ps_now, price_%_of_52w_high |
| FUNDAMENTALS | r40_score, perf_52w_vs_spy |
| MARKET | rating |
| TRACKING | next_earnings, days_on_list, composite_score |

Status values: `🟢` (eligible), `🆕 New`, `🟡 Watching`, `⚫ On Hold`, `🔴 Pending`, `❌ Exiting`. Statuses with emoji prefixes `🟢 🟡 ⚫ ❌` are **human-managed** and must never be overwritten by automation.

### AI Analysis (29 columns, grouped headers)

Row 1 = category headers, Row 2 = column names, Row 3+ = data.

| Group | Columns |
|---|---|
| IDENTITY | ticker, exchange, company_name, description |
| AI NARRATIVE | r40_score, fundamentals_snapshot, short_outlook, full_outlook, key_risks |
| REVENUE | annual_revenue_5y, quarterly_revenue, rev_growth_ttm%, rev_growth_qoq%, rev_cagr%, rev_consistency_score |
| MARGINS | gross_margin%, gm_trend%, operating_margin%, net_margin%, net_margin_yoy%, fcf_margin% |
| PROFITABILITY PATH | opex_%_of_revenue, s&m+r&d_%_of_revenue, rule_of_40, qrtrs_to_profitability |
| VALUATION | eps_only, eps_yoy% |
| Updated | ai (model), data (date) |

### Price-Sales (11 columns)

Single header row. Ticker-keyed with 52-week rolling P/S history stored as JSON.

```
ticker, name, ps_now, 52w_high, 52w_low, 12m_median, ath, %_of_ath,
history_json, last_updated, first_recorded
```

`history_json` format: `[["2025-03-07", 8.45], ["2025-03-14", 8.21], ...]`

### History

Weekly P/S tracking per ticker:
```
ticker, date, ps_ratio, entry_ps, ps_ratio_ttm, entry_ps_ttm, price
```

### Other Tabs

- **Change Log**: `date, ticker, company_name, event, alert, details, status_before, status_after, days_tracked, key_metrics`
- **Email Log**: `ticker, date_emailed, email_type`
- **Logs**: `run_date, backfilled, updated, skipped, errors, duration_secs`

## Composite Scoring

Stocks are ranked by a weighted percentile composite:

| Factor | Weight | Direction |
|---|---|---|
| R40 score | 40% | Higher = better |
| P/S ratio | 25% | Lower = better (inverted) |
| 52-week perf vs SPY | 20% | Higher = better |
| Analyst rating | 15% | Lower = better (inverted, 1.0 = strong buy) |

Sorted by: status priority → composite score descending.

## Coding Standards

### Python
- **Python 3.11** on GitHub Actions (Ubuntu 24)
- Use `logging` module throughout — never bare `print()` in production scripts
- All API calls wrapped in try/except with logger.warning/error
- Rate limiting: `time.sleep()` between API calls (0.5s EODHD, 1.0s between Claude calls)
- Type hints on function signatures (not enforced strictly)
- `_safe_float()` helper for any value that might be None/NaN/string
- Percentages: TradingView returns some as whole numbers (34.5 = 34.5%), some as decimals (0.345 = 34.5%). Use `safe_divide_100()` to normalize.
- Exchange mapping dictionaries with fallback chains for cross-market tickers

### Google Sheets
- Row 1 = grouped category headers (merged cells), Row 2 = column names, Row 3+ = data
- Use `USER_ENTERED` value input option for formulas, `FORMATTED_VALUE` for reading display values, `FORMULA` for reading formulas
- Protected statuses (🟢 🟡 ⚫ ❌) must never be overwritten by automation
- `entry_ps_ttm` and `first_seen` are write-once fields — set on first appearance, never update
- Formulas for `ps_discount` and `days_on_list` — dynamically reference row number

### Error Handling
- Never crash the whole run for a single ticker failure — log and continue
- EODHD/Yahoo: exchange fallback chains (try primary exchange, then alternatives)
- yfinance: NaN values are truthy in Python — always use `pd.notna()` not `if val`
- yfinance: try both `.financials` and `.income_stmt` API paths (old vs new)

### Git
- Repo: `tobyrowland/TV` on GitHub
- Branch: `main`
- Secrets stored in GitHub Actions environment

## Key Design Decisions

1. **Google Sheets as database** — the "UI" is the spreadsheet itself. No web frontend. This means the sheet schema is the API contract.

2. **Never delete rows from CURRENT** — stocks that drop off the TradingView screen stay in the sheet with their last data. Human-set statuses are preserved.

3. **Three-pass TradingView scanning** — TradingView limits results per query. Americas/Europe/Asia run separately, deduplicated by ticker.

4. **AI analysis is idempotent** — only runs for tickers with blank `description` field. Re-running is safe. Uses SerpAPI for web context → Claude for structured JSON output.

5. **P/S history uses price-ratio method** — instead of computing market_cap/revenue for each historical week, uses `ps_current * (week_close / latest_close)`. Simpler, avoids needing historical shares outstanding.

6. **Revenue trend matters more than earnings** — for pre-profit companies, earnings are meaningless. The screen prioritizes gross margin trend, revenue consistency, and margin trajectory over EPS or P/E.

## Common Tasks

### Adding a new TradingView filter
Edit `_screen_markets()` in `main.py`. Add a `.where(col("field") ...)` clause. Test with a single market pass first.

### Adding a new column to CURRENT
1. Add to `CURRENT_HEADERS` list in `main.py`
2. Update `NUM_COLS`
3. Populate in `upsert_current()` 
4. Add column header to the sheet manually (or let the script rebuild)

### Changing AI analysis fields
1. Edit `USER_PROMPT_TEMPLATE` in `ai_analysis_updater.py`
2. Update `write_ticker_updates()` column mappings
3. Ensure the AI Analysis sheet headers match

### Debugging missing data
- Margin trajectory failures: check yfinance — NaN values crash `round()`, use `pd.notna()`
- OTC tickers: Yahoo Finance doesn't have data for many OTC wrappers. Need ticker overrides mapping OTC → primary exchange.
- Exchange mismatches: TradingView exchange field ≠ Google Finance ≠ Yahoo Finance ≠ EODHD. Each has its own mapping dictionary.
