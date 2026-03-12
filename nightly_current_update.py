#!/usr/bin/env python3
"""
Nightly CURRENT Sheet Update.

Three steps in sequence:
1. Screen — Query TradingView for qualifying equities
2. Enrich — Look up pre-existing data from AI Analysis and Price-Sales tabs
3. Write  — Upsert fully constructed rows into the CURRENT tab

Runs nightly via GitHub Actions. Writes directly to Google Sheets.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from scipy.stats import percentileofscore
from tradingview_screener import Query, col

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
CURRENT_SHEET = "CURRENT"
AI_ANALYSIS_SHEET = "AI Analysis"
PRICE_SALES_SHEET = "Price-Sales"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column layout for CURRENT tab (A=0 .. AF=31)
CURRENT_HEADERS = [
    "ticker",               # A   0
    "exchange",             # B   1
    "company_name",         # C   2
    "country",              # D   3
    "sector",               # E   4
    "price",                # F   5
    "market_cap",           # G   6
    "ps_ratio_ttm",         # H   7
    "entry_ps_ttm",         # I   8
    "ps_discount",          # J   9
    "total_revenue_ttm",    # K  10
    "rev_growth_ttm%",      # L  11
    "gross_margin_%",       # M  12
    "net_margin_ttm",       # N  13
    "net_margin_direction", # O  14
    "net_margin_annual",    # P  15
    "net_margin_qoq",       # Q  16
    "fcf_margin_ttm",       # R  17
    "perf_52w_vs_spy",      # S  18
    "rating",               # T  19
    "status",               # U  20
    "description",          # V  21
    "fundamentals",         # W  22
    "short_outlook",        # X  23
    "signal",               # Y  24
    "outlook",              # Z  25
    "ai_analysis_date",     # AA 26
    "net_income_ttm",       # AB 27
    "first_seen",           # AC 28
    "days_on_list",         # AD 29
    "chart_data",           # AE 30
    "chart_data_date",      # AF 31
]
NUM_COLS = len(CURRENT_HEADERS)  # 32
COL_INDEX = {name: i for i, name in enumerate(CURRENT_HEADERS)}

# Statuses that are human-managed and must not be overwritten
PROTECTED_STATUSES = {"🟢", "🟡", "⚫", "❌"}

# Status priority for sorting
STATUS_PRIORITY = {
    "🟢 Eligible": 1,
    "🟢": 1,
    "🆕 New": 2,
    "🆕": 2,
    "🟡 Watching": 3,
    "🟡": 3,
    "⚫ On Hold": 4,
    "⚫": 4,
    "🔴 Pending": 5,
    "🔴": 5,
    "❌ Exiting": 6,
    "❌": 6,
}

# TradingView screener fields
TV_SELECT_FIELDS = [
    "name",
    "exchange",
    "description",
    "country",
    "sector",
    "close",
    "market_cap_basic",
    "price_revenue_ttm",
    "total_revenue_ttm",
    "total_revenue_yoy_growth_ttm",
    "gross_profit_margin_fy",
    "after_tax_margin",
    "free_cash_flow_margin_ttm",
    "Perf.Y",
    "recommendation_mark",
    "recommend_all_count",
    "net_income_ttm",
]

# Countries to exclude
EXCLUDED_COUNTRIES = {"China", "Hong Kong", "Taiwan"}

# Sectors to exclude (REITs / real estate)
EXCLUDED_SECTORS = {
    "Real Estate",
    "REIT",
    "Real Estate Investment Trusts",
}

# Markets to scan (broad global coverage)
TV_MARKETS = [
    "america", "australia", "canada", "uk", "germany", "france",
    "spain", "italy", "netherlands", "switzerland", "sweden", "norway",
    "denmark", "finland", "belgium", "austria", "portugal", "ireland",
    "israel", "india", "japan", "south_korea", "singapore", "new_zealand",
    "brazil", "mexico", "south_africa", "saudi_arabia", "uae",
    "indonesia", "thailand", "malaysia", "philippines", "vietnam",
    "poland", "greece", "turkey",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"nightly_current_{date.today().isoformat()}.txt"

    logger = logging.getLogger("nightly_current")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------


def get_sheets_service():
    sa_value = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_value:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set")

    if sa_value.strip().startswith("{"):
        info = json.loads(sa_value)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            sa_value, scopes=SCOPES
        )
    return build("sheets", "v4", credentials=creds)


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s)."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def read_sheet(service, sheet_name: str, end_col: str = "AF", value_render="FORMATTED_VALUE"):
    """Read all rows from a sheet tab with a bounded column range."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!A1:{end_col}",
            valueRenderOption=value_render,
        )
        .execute()
    )
    return result.get("values", [])


def find_column(headers, *names):
    """Find column index by trying multiple header names (case-insensitive)."""
    for idx, h in enumerate(headers):
        norm = h.strip().lower()
        for name in names:
            if norm == name.lower():
                return idx
    return None


def round_int_0dp(val):
    """Round a numeric value to integer with 0 decimal places."""
    if val is None:
        return None
    try:
        return round(float(val))
    except (ValueError, TypeError):
        return None


def clean_ticker(raw_name: str) -> str:
    """Clean ticker from TradingView name field."""
    # Remove leading digit followed by non-digit (TradingView quirk)
    return re.sub(r"^\d(?=\D)", "", raw_name)


def gf_url(ticker: str, exchange: str) -> str:
    """Build a GuruFocus URL for a ticker."""
    exchange_map = {
        "NASDAQ": "NAS",
        "NYSE": "NYSE",
        "AMEX": "NYSE",
        "OTC": "OTC",
        "TSX": "TSX",
        "LSE": "LSE",
        "ASX": "ASX",
        "NSE": "NSE",
        "BSE": "BSE",
        "XETRA": "FRA",
        "FRA": "FRA",
    }
    gf_exchange = exchange_map.get(exchange, exchange)
    return f"https://www.gurufocus.com/stock/{gf_exchange}:{ticker}/summary"


def safe_divide_100(val):
    """If value looks like a whole-number percentage (>1 or <-1), divide by 100."""
    if val is None:
        return None
    try:
        v = float(val)
    except (ValueError, TypeError):
        return None
    # TradingView returns some fields as percentages (e.g. 34.5 meaning 34.5%)
    # and some as decimals (e.g. 0.345 meaning 34.5%).
    # Fields > 1 or < -1 are likely whole-number percentages.
    if abs(v) > 1.0:
        return v / 100.0
    return v


# ---------------------------------------------------------------------------
# Step 1 — TradingView Screen
# ---------------------------------------------------------------------------


def run_tradingview_screen(logger) -> list[dict]:
    """
    Query TradingView screener with filters and return list of equity dicts.
    Runs 3 passes with deduplication across passes.
    """
    logger.info("=" * 60)
    logger.info("Step 1: TradingView Screening")
    logger.info("=" * 60)

    # First, get SPY's Perf.Y for relative performance calculation
    spy_perf_y = _get_spy_perf_y(logger)

    all_results = {}  # ticker -> dict, for deduplication

    # Pass 1: Americas
    pass1_markets = ["america", "canada", "brazil", "mexico"]
    # Pass 2: Europe / Middle East / Africa
    pass2_markets = [
        "uk", "germany", "france", "spain", "italy", "netherlands",
        "switzerland", "sweden", "norway", "denmark", "finland", "belgium",
        "austria", "portugal", "ireland", "israel", "south_africa",
        "saudi_arabia", "uae", "poland", "greece", "turkey",
    ]
    # Pass 3: Asia-Pacific
    pass3_markets = [
        "australia", "india", "japan", "south_korea", "singapore",
        "new_zealand", "indonesia", "thailand", "malaysia", "philippines",
        "vietnam",
    ]

    for pass_num, markets in enumerate(
        [pass1_markets, pass2_markets, pass3_markets], start=1
    ):
        logger.info("Pass %d: scanning %d markets...", pass_num, len(markets))
        equities = _screen_markets(markets, spy_perf_y, logger)
        new_count = 0
        for eq in equities:
            ticker = eq["ticker"]
            if ticker not in all_results:
                all_results[ticker] = eq
                new_count += 1
        logger.info(
            "Pass %d: found %d equities, %d new (total so far: %d)",
            pass_num, len(equities), new_count, len(all_results),
        )

    logger.info("TradingView screening complete: %d unique equities", len(all_results))
    return list(all_results.values())


def _get_spy_perf_y(logger) -> float:
    """Fetch SPY's Perf.Y from TradingView."""
    try:
        _, df = (
            Query()
            .set_tickers("AMEX:SPY")
            .select("Perf.Y")
            .get_scanner_data()
        )
        if len(df) > 0:
            spy_val = df.iloc[0]["Perf.Y"]
            logger.info("SPY Perf.Y = %.4f", spy_val)
            return float(spy_val)
    except Exception as e:
        logger.warning("Failed to fetch SPY Perf.Y: %s — defaulting to 0", e)
    return 0.0


def _screen_markets(markets: list[str], spy_perf_y: float, logger) -> list[dict]:
    """Run TradingView screener for a set of markets and return equity dicts."""
    try:
        query = (
            Query()
            .set_markets(*markets)
            .select(*TV_SELECT_FIELDS)
            .where(
                col("market_cap_basic").between(2_000_000_000, 500_000_000_000),
                col("gross_profit_margin_fy") > 45,
                col("total_revenue_yoy_growth_ttm").between(20, 500),
                col("total_revenue_ttm") > 100_000_000,
                col("price_revenue_ttm") < 15,
                col("recommendation_mark") <= 2.5,
            )
            .limit(5000)
            .get_scanner_data()
        )
        total_count, df = query
        logger.info("Screener returned %d results (total available: %d)", len(df), total_count)
    except Exception as e:
        logger.error("TradingView screener error: %s", e)
        return []

    equities = []
    for _, row in df.iterrows():
        raw_name = str(row.get("name", ""))
        ticker = clean_ticker(raw_name)
        if not ticker:
            continue

        country = str(row.get("country", ""))
        sector = str(row.get("sector", ""))

        # Exclude countries
        if country in EXCLUDED_COUNTRIES:
            continue

        # Exclude real estate / REITs
        if sector in EXCLUDED_SECTORS:
            continue

        exchange = str(row.get("exchange", ""))
        company_name = str(row.get("description", ""))
        url = gf_url(ticker, exchange)
        hyperlink = f'=HYPERLINK("{url}", "{company_name}")'

        price = row.get("close")
        market_cap = round_int_0dp(row.get("market_cap_basic"))
        ps_ratio = row.get("price_revenue_ttm")
        total_revenue = row.get("total_revenue_ttm")
        rev_growth = safe_divide_100(row.get("total_revenue_yoy_growth_ttm"))
        gross_margin = safe_divide_100(row.get("gross_profit_margin_fy"))
        net_margin = safe_divide_100(row.get("after_tax_margin"))
        fcf_margin = safe_divide_100(row.get("free_cash_flow_margin_ttm"))
        net_income = row.get("net_income_ttm")

        # Relative performance vs SPY
        perf_y = row.get("Perf.Y")
        if perf_y is not None and spy_perf_y is not None:
            perf_52w_vs_spy = safe_divide_100(perf_y) - safe_divide_100(spy_perf_y)
        else:
            perf_52w_vs_spy = None

        # Rating string
        rec_mark = row.get("recommendation_mark")
        rec_count = row.get("recommend_all_count")
        if rec_mark is not None and rec_count is not None:
            rating = f"{float(rec_mark):.1f} ({int(rec_count)})"
        else:
            rating = ""

        equities.append({
            "ticker": ticker,
            "exchange": exchange,
            "company_name": hyperlink,
            "country": country,
            "sector": sector,
            "price": price,
            "market_cap": market_cap,
            "ps_ratio_ttm": ps_ratio,
            "total_revenue_ttm": total_revenue,
            "rev_growth_ttm%": rev_growth,
            "gross_margin_%": gross_margin,
            "net_margin_ttm": net_margin,
            "fcf_margin_ttm": fcf_margin,
            "perf_52w_vs_spy": perf_52w_vs_spy,
            "rating": rating,
            "net_income_ttm": net_income,
        })

    return equities


# ---------------------------------------------------------------------------
# Step 2 — Enrich from Google Sheet
# ---------------------------------------------------------------------------


def load_ai_analysis(service, logger) -> dict:
    """Load AI Analysis tab into a dict keyed by ticker."""
    logger.info("Loading AI Analysis tab...")
    rows = read_sheet(service, AI_ANALYSIS_SHEET, end_col="AD")
    if len(rows) < 3:
        logger.warning("AI Analysis tab has fewer than 3 rows")
        return {}

    # Row 1 = category headers, Row 2 = column titles, Row 3+ = data
    headers = [str(h).strip().lower() for h in rows[1]]
    data_rows = rows[2:]

    # Find column indices
    col_map = {}
    for idx, h in enumerate(headers):
        col_map[h] = idx

    ticker_idx = col_map.get("ticker")
    if ticker_idx is None:
        logger.error("Cannot find 'ticker' column in AI Analysis headers: %s", headers)
        return {}

    ai_data = {}
    for row in data_rows:
        padded = row + [""] * (max(col_map.values()) + 1 - len(row))
        ticker = padded[ticker_idx].strip().upper()
        if not ticker:
            continue

        def get_val(key):
            idx = col_map.get(key)
            if idx is None:
                return None
            val = padded[idx].strip() if idx < len(padded) else ""
            return val if val else None

        ai_data[ticker] = {
            "description": get_val("description"),
            "fundamentals_snapshot": get_val("fundamentals_snapshot"),
            "short_outlook": get_val("short_outlook"),
            "full_outlook": get_val("full_outlook"),
            "net_margin%": get_val("net_margin%"),
            "net_margin_yoy%": get_val("net_margin_yoy%"),
            "data": get_val("data"),
            "r40_score": get_val("r40_score"),
        }

    logger.info("Loaded %d tickers from AI Analysis", len(ai_data))
    return ai_data


def load_price_sales(service, logger) -> dict:
    """Load Price-Sales tab into a dict keyed by ticker."""
    logger.info("Loading Price-Sales tab...")
    rows = read_sheet(service, PRICE_SALES_SHEET, end_col="K")
    if len(rows) < 2:
        logger.warning("Price-Sales tab has fewer than 2 rows")
        return {}

    # Row 1 = headers, Row 2+ = data
    headers = [str(h).strip().lower() for h in rows[0]]
    data_rows = rows[1:]

    col_map = {h: i for i, h in enumerate(headers)}

    ticker_idx = col_map.get("ticker")
    ps_now_idx = col_map.get("ps_now")

    if ticker_idx is None:
        logger.error("Cannot find 'ticker' column in Price-Sales headers: %s", headers)
        return {}
    if ps_now_idx is None:
        logger.error("Cannot find 'ps_now' column in Price-Sales headers: %s", headers)
        return {}

    ps_data = {}
    for row in data_rows:
        padded = row + [""] * (max(ticker_idx, ps_now_idx) + 1 - len(row))
        ticker = padded[ticker_idx].strip().upper()
        if not ticker:
            continue
        ps_now_raw = padded[ps_now_idx].strip()
        try:
            ps_now = float(ps_now_raw)
        except (ValueError, TypeError):
            ps_now = None
        ps_data[ticker] = {"ps_now": ps_now}

    logger.info("Loaded %d tickers from Price-Sales", len(ps_data))
    return ps_data


def derive_direction(net_margin_pct, net_margin_yoy_pct):
    """Derive net margin direction arrow from margin values."""
    if net_margin_yoy_pct is None or net_margin_pct is None:
        return None
    try:
        yoy = float(net_margin_yoy_pct)
    except (ValueError, TypeError):
        return None
    if yoy > 0.01:
        return "↑"
    elif yoy < -0.01:
        return "↓"
    else:
        return "→"


def parse_net_margin_value(val):
    """Parse a net margin string value to a float."""
    if val is None:
        return None
    try:
        v = float(str(val).replace("%", "").strip())
        # If it looks like a whole percentage, convert
        if abs(v) > 1.0:
            return v / 100.0
        return v
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Step 3 — Upsert into CURRENT
# ---------------------------------------------------------------------------


def load_current_data(service, logger) -> dict:
    """
    Read existing CURRENT sheet data rows into a dict keyed by ticker.
    Returns {ticker: {col_name: value, ...}, ...} and the raw rows for formula reads.
    """
    logger.info("Loading existing CURRENT data...")
    rows = read_sheet(service, CURRENT_SHEET, value_render="FORMULA")
    if len(rows) < 3:
        logger.warning("CURRENT tab has fewer than 3 rows (headers + data)")
        return {}

    # Row 1 = category, Row 2 = column titles, Row 3+ = data
    headers = [str(h).strip().lower() for h in rows[1]]
    data_rows = rows[2:]

    ticker_idx = None
    for idx, h in enumerate(headers):
        if h == "ticker":
            ticker_idx = idx
            break
    if ticker_idx is None:
        logger.error("Cannot find 'ticker' in CURRENT headers: %s", headers)
        return {}

    existing = {}
    for row_idx, row in enumerate(data_rows):
        padded = row + [""] * (NUM_COLS - len(row))
        ticker = padded[ticker_idx].strip().upper()
        if not ticker:
            continue
        row_data = {}
        for i, header_name in enumerate(CURRENT_HEADERS):
            row_data[header_name] = padded[i] if i < len(padded) else ""
        row_data["_sheet_row"] = row_idx + 3  # 1-indexed sheet row
        existing[ticker] = row_data

    logger.info("Loaded %d existing tickers from CURRENT", len(existing))
    return existing


def parse_r40_score(r40_str):
    """Parse r40_score like '💎💎💎 R40: 90' → 90."""
    if not r40_str:
        return None
    match = re.search(r"R40:\s*(\d+)", str(r40_str))
    if match:
        return int(match.group(1))
    return None


def parse_rating_numeric(rating_str):
    """Parse rating like '1.8 (12)' → 1.8."""
    if not rating_str:
        return None
    match = re.match(r"([\d.]+)", str(rating_str))
    if match:
        return float(match.group(1))
    return None


def compute_composite_score(row_data, all_rows):
    """Calculate composite score for sorting."""
    def pct(values, v, invert=False):
        if v is None or not values:
            return 0.5
        p = percentileofscore(values, v, kind="mean") / 100
        return (1 - p) if invert else p

    all_ps = [r["ps_ratio_ttm"] for r in all_rows if r.get("ps_ratio_ttm") is not None]
    all_perf = [r["perf_52w_vs_spy"] for r in all_rows if r.get("perf_52w_vs_spy") is not None]
    all_r40 = [r["r40_numeric"] for r in all_rows if r.get("r40_numeric") is not None]
    all_rtg = [r["rating_numeric"] for r in all_rows if r.get("rating_numeric") is not None]

    return (
        pct(all_r40, row_data.get("r40_numeric")) * 40
        + pct(all_ps, row_data.get("ps_ratio_ttm"), invert=True) * 25
        + pct(all_perf, row_data.get("perf_52w_vs_spy")) * 20
        + pct(all_rtg, row_data.get("rating_numeric"), invert=True) * 15
    )


def _safe_float(val):
    """Try to convert a value to float, return None on failure."""
    if val is None or val == "" or val == "—":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _status_base(status_str):
    """Extract the emoji prefix from a status string for comparison."""
    if not status_str:
        return ""
    # Get the first character cluster (emoji)
    for emoji in PROTECTED_STATUSES:
        if status_str.startswith(emoji):
            return emoji
    return status_str


def upsert_current(
    screened: list[dict],
    ai_data: dict,
    ps_data: dict,
    existing: dict,
    logger,
) -> list[list]:
    """
    Build the full data rows for CURRENT tab after upserting screened equities.
    Returns list of row-lists ready for batch_update.
    """
    today_str = date.today().isoformat()

    # Start with all existing tickers (we never delete rows)
    merged = dict(existing)

    for eq in screened:
        ticker = eq["ticker"].upper()
        is_new = ticker not in merged

        if is_new:
            row = {h: "" for h in CURRENT_HEADERS}
        else:
            row = dict(merged[ticker])

        # -- TradingView columns (always update) --
        row["ticker"] = eq["ticker"]
        row["exchange"] = eq["exchange"]
        row["company_name"] = eq["company_name"]
        row["country"] = eq["country"]
        row["sector"] = eq["sector"]
        row["price"] = eq.get("price", "")
        row["market_cap"] = eq.get("market_cap", "")
        row["ps_ratio_ttm"] = eq.get("ps_ratio_ttm", "")
        row["total_revenue_ttm"] = eq.get("total_revenue_ttm", "")
        row["rev_growth_ttm%"] = eq.get("rev_growth_ttm%", "")
        row["gross_margin_%"] = eq.get("gross_margin_%", "")
        row["net_margin_ttm"] = eq.get("net_margin_ttm", "")
        row["fcf_margin_ttm"] = eq.get("fcf_margin_ttm", "")
        row["perf_52w_vs_spy"] = eq.get("perf_52w_vs_spy", "")
        row["rating"] = eq.get("rating", "")
        row["net_income_ttm"] = eq.get("net_income_ttm", "")

        # -- Price-Sales override for ps_ratio_ttm (col H) --
        ai_row = ai_data.get(ticker, {})
        ps_row = ps_data.get(ticker, {})
        if ps_row.get("ps_now") is not None:
            row["ps_ratio_ttm"] = ps_row["ps_now"]

        # -- AI Analysis enrichment --
        if ai_row:
            if ai_row.get("net_margin%"):
                row["net_margin_annual"] = ai_row["net_margin%"]
            if ai_row.get("net_margin_yoy%"):
                row["net_margin_qoq"] = ai_row["net_margin_yoy%"]
            if ai_row.get("description"):
                row["description"] = ai_row["description"]
            if ai_row.get("fundamentals_snapshot"):
                row["fundamentals"] = ai_row["fundamentals_snapshot"]
            if ai_row.get("short_outlook"):
                row["short_outlook"] = ai_row["short_outlook"]
            if ai_row.get("full_outlook"):
                row["outlook"] = ai_row["full_outlook"]
            if ai_row.get("data"):
                row["ai_analysis_date"] = ai_row["data"]

        # -- Derive net_margin_direction (col O) --
        nm_pct = parse_net_margin_value(ai_row.get("net_margin%"))
        nm_yoy = parse_net_margin_value(ai_row.get("net_margin_yoy%"))
        direction = derive_direction(nm_pct, nm_yoy)
        if direction is not None:
            row["net_margin_direction"] = direction

        # -- Signal: leave as-is if already populated and AI has no value --
        if is_new:
            row["signal"] = ""

        # -- Never-overwrite columns on existing rows --
        if is_new:
            row["entry_ps_ttm"] = row["ps_ratio_ttm"]
            row["first_seen"] = today_str
            row["status"] = "🆕 New"
        else:
            # Preserve entry_ps_ttm and first_seen (never overwrite)
            pass

        # -- Status auto-logic --
        if not is_new:
            current_status = str(row.get("status", "")).strip()
            status_emoji = _status_base(current_status)

            if status_emoji in PROTECTED_STATUSES:
                pass  # Leave unchanged
            elif ai_row and ai_row.get("short_outlook"):
                row["status"] = "🟡 Watching"
            elif not ai_row or not ai_row.get("short_outlook"):
                row["status"] = "🔴 Pending"

        # -- chart_data and chart_data_date: never touch --
        # (they remain as-is from existing data or empty for new rows)

        merged[ticker] = row

    # Now also enrich existing tickers that weren't in today's screen
    # but have AI/PS data
    for ticker, row in merged.items():
        if ticker in {eq["ticker"].upper() for eq in screened}:
            continue  # Already processed above

        ai_row = ai_data.get(ticker, {})
        ps_row = ps_data.get(ticker, {})

        # Update AI Analysis columns if data found
        if ai_row:
            if ai_row.get("net_margin%"):
                row["net_margin_annual"] = ai_row["net_margin%"]
            if ai_row.get("net_margin_yoy%"):
                row["net_margin_qoq"] = ai_row["net_margin_yoy%"]
            if ai_row.get("description"):
                row["description"] = ai_row["description"]
            if ai_row.get("fundamentals_snapshot"):
                row["fundamentals"] = ai_row["fundamentals_snapshot"]
            if ai_row.get("short_outlook"):
                row["short_outlook"] = ai_row["short_outlook"]
            if ai_row.get("full_outlook"):
                row["outlook"] = ai_row["full_outlook"]
            if ai_row.get("data"):
                row["ai_analysis_date"] = ai_row["data"]

            nm_pct = parse_net_margin_value(ai_row.get("net_margin%"))
            nm_yoy = parse_net_margin_value(ai_row.get("net_margin_yoy%"))
            direction = derive_direction(nm_pct, nm_yoy)
            if direction is not None:
                row["net_margin_direction"] = direction

        # Update P/S from Price-Sales if found
        if ps_row.get("ps_now") is not None:
            row["ps_ratio_ttm"] = ps_row["ps_now"]

    # -- Build scoring data for sorting --
    scoring_rows = []
    for ticker, row in merged.items():
        score_data = dict(row)
        score_data["ps_ratio_ttm"] = _safe_float(row.get("ps_ratio_ttm"))
        score_data["perf_52w_vs_spy"] = _safe_float(row.get("perf_52w_vs_spy"))
        score_data["r40_numeric"] = parse_r40_score(
            ai_data.get(ticker, {}).get("r40_score")
        )
        score_data["rating_numeric"] = parse_rating_numeric(row.get("rating"))
        score_data["_ticker"] = ticker
        scoring_rows.append(score_data)

    # Compute composite scores
    for sr in scoring_rows:
        sr["_composite_score"] = compute_composite_score(sr, scoring_rows)

    # Sort by status priority, then composite score descending
    def sort_key(sr):
        status = str(sr.get("status", "")).strip()
        priority = STATUS_PRIORITY.get(status, 99)
        # Also try matching just the emoji
        if priority == 99:
            emoji = _status_base(status)
            for k, v in STATUS_PRIORITY.items():
                if k.startswith(emoji) and emoji:
                    priority = v
                    break
        return (priority, -sr.get("_composite_score", 0))

    scoring_rows.sort(key=sort_key)

    logger.info("Sorted %d rows by status priority + composite score", len(scoring_rows))

    # Build final row-lists
    final_rows = []
    for idx, sr in enumerate(scoring_rows):
        ticker = sr["_ticker"]
        row = merged[ticker]
        sheet_row = idx + 3  # data starts at row 3

        # Build the row as a list matching CURRENT_HEADERS
        row_list = []
        for col_name in CURRENT_HEADERS:
            val = row.get(col_name, "")

            # Formula columns: ps_discount and days_on_list
            if col_name == "ps_discount":
                i_col = _col_letter(COL_INDEX["entry_ps_ttm"])
                h_col = _col_letter(COL_INDEX["ps_ratio_ttm"])
                val = f'=IFERROR(({i_col}{sheet_row}-{h_col}{sheet_row})/{i_col}{sheet_row},"")'
            elif col_name == "days_on_list":
                ac_col = _col_letter(COL_INDEX["first_seen"])
                val = f'=IFERROR(TODAY()-{ac_col}{sheet_row},"")'
            elif col_name in ("chart_data", "chart_data_date"):
                # Never touch these — keep existing value or empty
                val = row.get(col_name, "")

            if val is None:
                val = ""
            row_list.append(val)

        final_rows.append(row_list)

    return final_rows


def write_current(service, final_rows: list[list], logger):
    """Write all data rows to CURRENT tab via batch update."""
    logger.info("=" * 60)
    logger.info("Step 3: Writing to CURRENT tab")
    logger.info("=" * 60)

    if not final_rows:
        logger.warning("No data rows to write!")
        return

    # Read existing category row and header row to preserve them
    existing_headers = read_sheet(service, CURRENT_SHEET, value_render="FORMULA")
    if len(existing_headers) >= 2:
        cat_row = existing_headers[0]
        header_row = existing_headers[1]
        # Pad to NUM_COLS
        cat_row = (cat_row + [""] * NUM_COLS)[:NUM_COLS]
        header_row = (header_row + [""] * NUM_COLS)[:NUM_COLS]
    else:
        # Rebuild from scratch
        cat_row = [""] * NUM_COLS
        header_row = list(CURRENT_HEADERS)

    all_rows = [cat_row, header_row] + final_rows
    end_col = _col_letter(NUM_COLS - 1)
    end_row = len(all_rows)
    write_range = f"'{CURRENT_SHEET}'!A1:{end_col}{end_row}"

    # Clear data area first (preserve headers by overwriting everything)
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{CURRENT_SHEET}'!A3:ZZ",
    ).execute()

    # Write all rows
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=write_range,
        valueInputOption="USER_ENTERED",
        body={"values": all_rows},
    ).execute()

    logger.info(
        "Wrote %d data rows to CURRENT tab (%s)", len(final_rows), write_range
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("Nightly CURRENT Sheet Update — starting")
    logger.info("=" * 60)

    # Step 1: TradingView Screen
    screened = run_tradingview_screen(logger)
    logger.info("Screened %d equities from TradingView", len(screened))

    # Connect to Google Sheets
    service = get_sheets_service()

    # Step 2: Enrich from Google Sheet tabs
    logger.info("=" * 60)
    logger.info("Step 2: Enriching from Google Sheet tabs")
    logger.info("=" * 60)

    ai_data = load_ai_analysis(service, logger)
    ps_data = load_price_sales(service, logger)

    # Load existing CURRENT data
    existing = load_current_data(service, logger)

    # Step 3: Upsert
    final_rows = upsert_current(screened, ai_data, ps_data, existing, logger)

    # Write to sheet
    write_current(service, final_rows, logger)

    logger.info("=" * 60)
    logger.info("Nightly CURRENT Sheet Update — complete (%d rows)", len(final_rows))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
