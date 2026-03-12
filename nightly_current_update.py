#!/usr/bin/env python3
"""
Nightly CURRENT_V2 Sheet Update.

Three steps in sequence:
1. Screen — Query TradingView for qualifying equities
2. Enrich — Look up pre-existing data from AI Analysis, Price-Sales, and CURRENT tabs
3. Write  — Upsert fully constructed rows into the CURRENT_V2 tab

Runs nightly via GitHub Actions. Writes directly to Google Sheets.
"""

import json
import logging
import math
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

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
V2_SHEET = "CURRENT_V2"
CURRENT_SHEET = "CURRENT"
AI_ANALYSIS_SHEET = "AI Analysis"
PRICE_SALES_SHEET = "Price-Sales"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# CURRENT_V2 column layout (A=0 .. T=19)
# ---------------------------------------------------------------------------

V2_HEADERS = [
    "deep_dive",            # A   0  — Manual only (checkbox)
    "status",               # B   1
    "conviction_tier",      # C   2  — Manual only
    "ticker",               # D   3  — HYPERLINK to Google Finance
    "company_name",         # E   4  — Plain text
    "exchange",             # F   5
    "country",              # G   6
    "sector",               # H   7
    "description",          # I   8  — From AI Analysis
    "fundamentals_snapshot",# J   9  — HYPERLINK to AI Analysis row
    "short_outlook",        # K  10  — From AI Analysis
    "price",                # L  11
    "ps_now",               # M  12  — HYPERLINK to Price-Sales row
    "price_%_of_52w_high",  # N  13  — P/S Now / 52w High
    "r40_score",            # O  14  — From AI Analysis
    "perf_52w_vs_spy",      # P  15
    "rating",               # Q  16
    "next_earnings",        # R  17  — Manual only
    "days_on_list",         # S  18  — From CURRENT first_seen
    "composite_score",      # T  19
]
NUM_COLS = len(V2_HEADERS)  # 20
COL_INDEX = {name: i for i, name in enumerate(V2_HEADERS)}

# Columns that must NEVER be written by any script
MANUAL_ONLY_COLS = {"deep_dive", "conviction_tier", "next_earnings"}

# Statuses that are human-managed and must not be overwritten
PROTECTED_STATUSES = {"🟢", "🟡", "⚫", "❌"}

# Status priority for sorting
STATUS_PRIORITY = {
    "🟢 Eligible": 1, "🟢": 1,
    "🆕 New": 2, "🆕": 2,
    "🟡 Watching": 3, "🟡": 3,
    "⚫ On Hold": 4, "⚫": 4,
    "🔴 Pending": 5, "🔴": 5,
    "❌ Exiting": 6, "❌": 6,
}

# Row 1 category merges for CURRENT_V2
CATEGORY_MERGES = [
    ("A1:C1", "STATUS"),
    ("D1:H1", "IDENTITY"),
    ("I1:K1", "NARRATIVE"),
    ("L1:N1", "VALUATION"),
    ("O1:P1", "FUNDAMENTALS"),
    ("Q1", "MARKET"),
    ("R1:T1", "TRACKING"),
]

# TradingView screener fields
TV_SELECT_FIELDS = [
    "name", "exchange", "description", "country", "sector",
    "close", "market_cap_basic", "price_revenue_ttm",
    "total_revenue_ttm", "total_revenue_yoy_growth_ttm",
    "gross_profit_margin_fy", "after_tax_margin",
    "free_cash_flow_margin_ttm", "Perf.Y",
    "recommendation_mark", "recommend_all_count",
    "net_income_ttm",
]

# Countries to exclude
EXCLUDED_COUNTRIES = {"China", "Hong Kong", "Taiwan"}

# Sectors to exclude (REITs / real estate)
EXCLUDED_SECTORS = {"Real Estate", "REIT", "Real Estate Investment Trusts"}

# Google Finance exchange mapping
GF_EXCHANGE_MAP = {
    "NASDAQ": "NASDAQ", "NYSE": "NYSE", "AMEX": "NYSEAMERICAN",
    "OTC": "OTCMKTS", "TSX": "TSE", "LSE": "LON", "ASX": "ASX",
    "NSE": "NSE", "BSE": "BOM", "XETRA": "ETR", "FRA": "FRA",
    "EPA": "EPA", "AMS": "AMS", "SWX": "SWX", "BIT": "BIT",
    "BME": "BME", "TSE": "TYO", "KRX": "KRX", "SGX": "SGX",
    "NZX": "NZE", "JSE": "JSE", "SAU": "TADAWUL",
}


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


def read_sheet(service, sheet_name: str, end_col: str = "T", value_render="FORMATTED_VALUE"):
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


def get_sheet_gids(service) -> dict:
    """Return {sheet_title: gid} for all sheets in the spreadsheet."""
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties")
        .execute()
    )
    return {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }


def sanitize_value(val):
    """Sanitize a value for Google Sheets JSON payload (no NaN/Inf)."""
    if val is None:
        return ""
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return ""
    return val


def sanitize_row(row: list) -> list:
    """Sanitize all values in a row for safe JSON serialization."""
    return [sanitize_value(v) for v in row]


def clean_ticker(raw_name: str) -> str:
    """Clean ticker from TradingView name field."""
    return re.sub(r"^\d(?=\D)", "", raw_name)


def google_finance_url(ticker: str, exchange: str) -> str:
    """Build a Google Finance URL for a ticker."""
    gf_exchange = GF_EXCHANGE_MAP.get(exchange, exchange)
    return f"https://www.google.com/finance/quote/{ticker}:{gf_exchange}"


def safe_divide_100(val):
    """If value looks like a whole-number percentage (>1 or <-1), divide by 100."""
    if val is None:
        return None
    try:
        v = float(val)
    except (ValueError, TypeError):
        return None
    if abs(v) > 1.0:
        return v / 100.0
    return v


def _safe_float(val):
    """Try to convert a value to float, return None on failure."""
    if val is None or val == "" or val == "—":
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


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

    spy_perf_y = _get_spy_perf_y(logger)

    all_results = {}

    pass1_markets = ["america", "canada", "brazil", "mexico"]
    pass2_markets = [
        "uk", "germany", "france", "spain", "italy", "netherlands",
        "switzerland", "sweden", "norway", "denmark", "finland", "belgium",
        "austria", "portugal", "ireland", "israel", "south_africa",
        "saudi_arabia", "uae", "poland", "greece", "turkey",
    ]
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
        total_count, df = (
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

        if country in EXCLUDED_COUNTRIES:
            continue
        if sector in EXCLUDED_SECTORS:
            continue

        exchange = str(row.get("exchange", ""))
        company_name = str(row.get("description", ""))

        price = row.get("close")

        # Relative performance vs SPY
        perf_y = row.get("Perf.Y")
        if perf_y is not None:
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
            "company_name": company_name,
            "country": country,
            "sector": sector,
            "price": price,
            "perf_52w_vs_spy": perf_52w_vs_spy,
            "rating": rating,
        })

    return equities


# ---------------------------------------------------------------------------
# Step 2 — Enrich from Google Sheet
# ---------------------------------------------------------------------------


def load_ai_analysis(service, logger) -> tuple[dict, dict]:
    """
    Load AI Analysis tab into a dict keyed by ticker.
    Also returns {ticker: row_number} for deep linking.
    """
    logger.info("Loading AI Analysis tab...")
    rows = read_sheet(service, AI_ANALYSIS_SHEET, end_col="AD")
    if len(rows) < 3:
        logger.warning("AI Analysis tab has fewer than 3 rows")
        return {}, {}

    # Row 1 = category headers, Row 2 = column titles, Row 3+ = data
    headers = [str(h).strip().lower() for h in rows[1]]
    data_rows = rows[2:]

    col_map = {}
    for idx, h in enumerate(headers):
        col_map[h] = idx

    ticker_idx = col_map.get("ticker")
    if ticker_idx is None:
        logger.error("Cannot find 'ticker' column in AI Analysis headers: %s", headers)
        return {}, {}

    ai_data = {}
    ai_row_map = {}  # ticker -> 1-indexed sheet row number
    for row_idx, row in enumerate(data_rows):
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
        ai_row_map[ticker] = row_idx + 3  # data starts at row 3

    logger.info("Loaded %d tickers from AI Analysis", len(ai_data))
    return ai_data, ai_row_map


def load_price_sales(service, logger) -> tuple[dict, dict]:
    """
    Load Price-Sales tab into a dict keyed by ticker.
    Also returns {ticker: row_number} for deep linking.
    """
    logger.info("Loading Price-Sales tab...")
    rows = read_sheet(service, PRICE_SALES_SHEET, end_col="K")
    if len(rows) < 2:
        logger.warning("Price-Sales tab has fewer than 2 rows")
        return {}, {}

    # Detect header row
    row1_lower = [str(h).strip().lower() for h in rows[0]]
    if "ticker" in row1_lower:
        headers = row1_lower
        data_rows = rows[1:]
        data_start_row = 2  # 1-indexed
    elif len(rows) >= 3:
        headers = [str(h).strip().lower() for h in rows[1]]
        data_rows = rows[2:]
        data_start_row = 3
    else:
        logger.warning("Price-Sales tab: cannot find column titles")
        return {}, {}

    col_map = {h: i for i, h in enumerate(headers)}

    ticker_idx = col_map.get("ticker")
    ps_now_idx = col_map.get("ps_now")
    high_52w_idx = col_map.get("52w_high")

    if ticker_idx is None:
        logger.error("Cannot find 'ticker' column in Price-Sales headers: %s", headers)
        return {}, {}
    if ps_now_idx is None:
        logger.error("Cannot find 'ps_now' column in Price-Sales headers: %s", headers)
        return {}, {}

    ps_data = {}
    ps_row_map = {}  # ticker -> 1-indexed sheet row number
    max_idx = max(c for c in [ticker_idx, ps_now_idx, high_52w_idx] if c is not None)
    for row_idx, row in enumerate(data_rows):
        padded = row + [""] * (max_idx + 1 - len(row))
        ticker = padded[ticker_idx].strip().upper()
        if not ticker:
            continue

        ps_now = _safe_float(padded[ps_now_idx])
        high_52w = _safe_float(padded[high_52w_idx]) if high_52w_idx is not None else None

        ps_data[ticker] = {"ps_now": ps_now, "52w_high": high_52w}
        ps_row_map[ticker] = data_start_row + row_idx

    logger.info("Loaded %d tickers from Price-Sales", len(ps_data))
    return ps_data, ps_row_map


def load_current_first_seen(service, logger) -> dict:
    """
    Read CURRENT tab to get first_seen dates keyed by ticker.
    Returns {ticker: first_seen_str}.
    """
    logger.info("Loading CURRENT tab for first_seen dates...")
    rows = read_sheet(service, CURRENT_SHEET, end_col="AF")
    if len(rows) < 3:
        logger.warning("CURRENT tab has fewer than 3 rows")
        return {}

    headers = [str(h).strip().lower() for h in rows[1]]
    data_rows = rows[2:]

    ticker_idx = None
    first_seen_idx = None
    for idx, h in enumerate(headers):
        if h == "ticker":
            ticker_idx = idx
        if h == "first_seen":
            first_seen_idx = idx

    if ticker_idx is None or first_seen_idx is None:
        logger.warning("Cannot find ticker/first_seen in CURRENT headers: %s", headers)
        return {}

    first_seen_map = {}
    for row in data_rows:
        padded = row + [""] * (max(ticker_idx, first_seen_idx) + 1 - len(row))
        ticker = padded[ticker_idx].strip().upper()
        fs = padded[first_seen_idx].strip()
        if ticker and fs:
            first_seen_map[ticker] = fs

    logger.info("Loaded %d first_seen dates from CURRENT", len(first_seen_map))
    return first_seen_map


# ---------------------------------------------------------------------------
# Step 3 — Upsert into CURRENT_V2
# ---------------------------------------------------------------------------


def load_v2_data(service, logger) -> dict:
    """
    Read existing CURRENT_V2 data rows into a dict keyed by ticker.
    Ticker is in col D (index 3). Uses FORMULA render to preserve hyperlinks.
    """
    logger.info("Loading existing CURRENT_V2 data...")
    rows = read_sheet(service, V2_SHEET, end_col="T", value_render="FORMULA")
    if len(rows) < 3:
        logger.warning("CURRENT_V2 tab has fewer than 3 rows (headers + data)")
        return {}

    headers = [str(h).strip().lower() for h in rows[1]]
    data_rows = rows[2:]

    ticker_idx = None
    for idx, h in enumerate(headers):
        if h == "ticker":
            ticker_idx = idx
            break
    if ticker_idx is None:
        logger.error("Cannot find 'ticker' in CURRENT_V2 headers: %s", headers)
        return {}

    existing = {}
    for row in data_rows:
        padded = row + [""] * (NUM_COLS - len(row))
        # ticker col may be a HYPERLINK formula — extract the display text
        raw_ticker = padded[ticker_idx]
        ticker = _extract_ticker_from_hyperlink(raw_ticker)
        if not ticker:
            continue
        row_data = {}
        for i, header_name in enumerate(V2_HEADERS):
            row_data[header_name] = padded[i] if i < len(padded) else ""
        existing[ticker] = row_data

    logger.info("Loaded %d existing tickers from CURRENT_V2", len(existing))
    return existing


def _extract_ticker_from_hyperlink(val):
    """Extract ticker from a HYPERLINK formula or plain text."""
    val = str(val).strip()
    if not val:
        return ""
    # Match =HYPERLINK("...", "TICKER")
    match = re.search(r'=HYPERLINK\([^,]+,\s*"([^"]+)"\)', val)
    if match:
        return match.group(1).strip().upper()
    return val.strip().upper()


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


def _status_base(status_str):
    """Extract the emoji prefix from a status string."""
    if not status_str:
        return ""
    for emoji in PROTECTED_STATUSES:
        if status_str.startswith(emoji):
            return emoji
    return status_str


def compute_composite_score(row_data, all_rows):
    """Calculate composite score for sorting."""
    def pct(values, v, invert=False):
        if v is None or not values:
            return 0.5
        p = percentileofscore(values, v, kind="mean") / 100
        return (1 - p) if invert else p

    all_ps = [r["_ps_now_f"] for r in all_rows if r.get("_ps_now_f") is not None]
    all_perf = [r["_perf_f"] for r in all_rows if r.get("_perf_f") is not None]
    all_r40 = [r["_r40_f"] for r in all_rows if r.get("_r40_f") is not None]
    all_rtg = [r["_rating_f"] for r in all_rows if r.get("_rating_f") is not None]

    return (
        pct(all_r40, row_data.get("_r40_f")) * 40
        + pct(all_ps, row_data.get("_ps_now_f"), invert=True) * 25
        + pct(all_perf, row_data.get("_perf_f")) * 20
        + pct(all_rtg, row_data.get("_rating_f"), invert=True) * 15
    )


def upsert_v2(
    screened: list[dict],
    ai_data: dict,
    ai_row_map: dict,
    ps_data: dict,
    ps_row_map: dict,
    first_seen_map: dict,
    existing: dict,
    sheet_gids: dict,
    logger,
) -> list[list]:
    """
    Build the full data rows for CURRENT_V2 after upserting screened equities.
    Returns list of row-lists ready for batch write.
    """
    today_str = date.today().isoformat()
    ai_gid = sheet_gids.get(AI_ANALYSIS_SHEET, 0)
    ps_gid = sheet_gids.get(PRICE_SALES_SHEET, 0)

    # Start with all existing tickers (we never delete rows)
    merged = dict(existing)

    for eq in screened:
        ticker = eq["ticker"].upper()
        is_new = ticker not in merged

        if is_new:
            row = {h: "" for h in V2_HEADERS}
        else:
            row = dict(merged[ticker])

        # -- IDENTITY columns (always update) --
        gf_link = google_finance_url(ticker, eq["exchange"])
        row["ticker"] = f'=HYPERLINK("{gf_link}", "{eq["ticker"]}")'
        row["company_name"] = eq["company_name"]
        row["exchange"] = eq["exchange"]
        row["country"] = eq["country"]
        row["sector"] = eq["sector"]

        # -- VALUATION --
        row["price"] = eq.get("price", "")

        # -- MARKET --
        row["perf_52w_vs_spy"] = eq.get("perf_52w_vs_spy", "")
        row["rating"] = eq.get("rating", "")

        # -- AI Analysis enrichment --
        ai_row = ai_data.get(ticker, {})
        if ai_row:
            if ai_row.get("description"):
                row["description"] = ai_row["description"]
            if ai_row.get("short_outlook"):
                row["short_outlook"] = ai_row["short_outlook"]
            if ai_row.get("r40_score"):
                row["r40_score"] = ai_row["r40_score"]

            # fundamentals_snapshot as HYPERLINK to AI Analysis row
            fs_text = ai_row.get("fundamentals_snapshot", "")
            if fs_text and ticker in ai_row_map:
                ai_sheet_row = ai_row_map[ticker]
                # Escape double quotes in display text
                safe_text = str(fs_text).replace('"', '""')
                row["fundamentals_snapshot"] = (
                    f'=HYPERLINK("#gid={ai_gid}&range=A{ai_sheet_row}", "{safe_text}")'
                )
            elif fs_text:
                row["fundamentals_snapshot"] = fs_text

        # -- Price-Sales enrichment --
        ps_row = ps_data.get(ticker, {})
        ps_now_val = ps_row.get("ps_now")
        if ps_now_val is not None and ticker in ps_row_map:
            ps_sheet_row = ps_row_map[ticker]
            row["ps_now"] = (
                f'=HYPERLINK("#gid={ps_gid}&range=A{ps_sheet_row}", "{ps_now_val:.2f}")'
            )
        elif ps_now_val is not None:
            row["ps_now"] = ps_now_val

        # price_%_of_52w_high = P/S Now / 52w High
        high_52w = ps_row.get("52w_high")
        if ps_now_val is not None and high_52w is not None and high_52w > 0:
            row["price_%_of_52w_high"] = ps_now_val / high_52w
        else:
            row["price_%_of_52w_high"] = ""

        # -- days_on_list from CURRENT first_seen --
        fs_date = first_seen_map.get(ticker)
        if fs_date:
            try:
                from datetime import datetime
                fs_parsed = datetime.strptime(fs_date, "%Y-%m-%d").date()
                row["days_on_list"] = (date.today() - fs_parsed).days
            except (ValueError, TypeError):
                row["days_on_list"] = ""
        elif is_new:
            row["days_on_list"] = 0
        # else keep existing value

        # -- Status logic --
        if is_new:
            row["status"] = "🆕 New"
        else:
            current_status = str(row.get("status", "")).strip()
            status_emoji = _status_base(current_status)
            if status_emoji in PROTECTED_STATUSES:
                pass  # Leave unchanged
            elif ai_row and ai_row.get("short_outlook"):
                row["status"] = "🟡 Watching"
            else:
                row["status"] = "🔴 Pending"

        # -- Manual-only cols: preserve existing values --
        if not is_new:
            for manual_col in MANUAL_ONLY_COLS:
                # Already in row from existing data, don't touch
                pass
        # For new rows, manual cols stay as empty string (already set above)

        merged[ticker] = row

    # Also enrich existing tickers not in today's screen
    screened_tickers = {eq["ticker"].upper() for eq in screened}
    for ticker, row in merged.items():
        if ticker in screened_tickers:
            continue

        ai_row = ai_data.get(ticker, {})
        if ai_row:
            if ai_row.get("description"):
                row["description"] = ai_row["description"]
            if ai_row.get("short_outlook"):
                row["short_outlook"] = ai_row["short_outlook"]
            if ai_row.get("r40_score"):
                row["r40_score"] = ai_row["r40_score"]
            fs_text = ai_row.get("fundamentals_snapshot", "")
            if fs_text and ticker in ai_row_map:
                ai_sheet_row = ai_row_map[ticker]
                safe_text = str(fs_text).replace('"', '""')
                row["fundamentals_snapshot"] = (
                    f'=HYPERLINK("#gid={ai_gid}&range=A{ai_sheet_row}", "{safe_text}")'
                )

        ps_row = ps_data.get(ticker, {})
        ps_now_val = ps_row.get("ps_now")
        if ps_now_val is not None and ticker in ps_row_map:
            ps_sheet_row = ps_row_map[ticker]
            row["ps_now"] = (
                f'=HYPERLINK("#gid={ps_gid}&range=A{ps_sheet_row}", "{ps_now_val:.2f}")'
            )
        high_52w = ps_row.get("52w_high")
        if ps_now_val is not None and high_52w is not None and high_52w > 0:
            row["price_%_of_52w_high"] = ps_now_val / high_52w

        fs_date = first_seen_map.get(ticker)
        if fs_date:
            try:
                from datetime import datetime
                fs_parsed = datetime.strptime(fs_date, "%Y-%m-%d").date()
                row["days_on_list"] = (date.today() - fs_parsed).days
            except (ValueError, TypeError):
                pass

    # -- Build scoring data --
    scoring_rows = []
    for ticker, row in merged.items():
        sr = dict(row)
        sr["_ticker"] = ticker
        # Extract numeric P/S from hyperlink or plain value
        ps_raw = row.get("ps_now", "")
        if isinstance(ps_raw, str) and "HYPERLINK" in ps_raw:
            match = re.search(r'"([\d.]+)"[)\s]*$', ps_raw)
            sr["_ps_now_f"] = float(match.group(1)) if match else None
        else:
            sr["_ps_now_f"] = _safe_float(ps_raw)
        sr["_perf_f"] = _safe_float(row.get("perf_52w_vs_spy"))
        sr["_r40_f"] = parse_r40_score(row.get("r40_score"))
        sr["_rating_f"] = parse_rating_numeric(row.get("rating"))
        scoring_rows.append(sr)

    # Compute composite scores
    for sr in scoring_rows:
        sr["_composite_score"] = compute_composite_score(sr, scoring_rows)

    # Sort by status priority, then composite score descending
    def sort_key(sr):
        status = str(sr.get("status", "")).strip()
        priority = STATUS_PRIORITY.get(status, 99)
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
    for sr in scoring_rows:
        ticker = sr["_ticker"]
        row = merged[ticker]
        # Write composite_score to col T
        row["composite_score"] = round(sr["_composite_score"], 1)

        row_list = []
        for col_name in V2_HEADERS:
            val = row.get(col_name, "")
            if val is None:
                val = ""
            row_list.append(val)

        final_rows.append(row_list)

    return final_rows


def write_v2(service, final_rows: list[list], logger):
    """Write all data rows to CURRENT_V2 tab via batch update."""
    logger.info("=" * 60)
    logger.info("Step 3: Writing to CURRENT_V2 tab")
    logger.info("=" * 60)

    if not final_rows:
        logger.warning("No data rows to write!")
        return

    # Read existing header rows to preserve them
    existing_headers = read_sheet(service, V2_SHEET, end_col="T", value_render="FORMULA")
    if len(existing_headers) >= 2:
        cat_row = existing_headers[0]
        header_row = existing_headers[1]
        cat_row = (cat_row + [""] * NUM_COLS)[:NUM_COLS]
        header_row = (header_row + [""] * NUM_COLS)[:NUM_COLS]
    else:
        # Build category row from spec
        cat_row = [""] * NUM_COLS
        cat_row[0] = "STATUS"    # A (STATUS covers A-C)
        cat_row[3] = "IDENTITY"  # D (IDENTITY covers D-H)
        cat_row[8] = "NARRATIVE" # I (NARRATIVE covers I-K)
        cat_row[11] = "VALUATION"  # L (VALUATION covers L-N)
        cat_row[14] = "FUNDAMENTALS"  # O (FUNDAMENTALS covers O-P)
        cat_row[16] = "MARKET"   # Q
        cat_row[17] = "TRACKING" # R (TRACKING covers R-T)
        header_row = list(V2_HEADERS)

    sanitized_rows = [sanitize_row(r) for r in final_rows]
    all_rows = [cat_row, header_row] + sanitized_rows
    end_col = _col_letter(NUM_COLS - 1)
    end_row = len(all_rows)
    write_range = f"'{V2_SHEET}'!A1:{end_col}{end_row}"

    # Clear data area first
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{V2_SHEET}'!A3:{end_col}",
    ).execute()

    # Write all rows
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=write_range,
        valueInputOption="USER_ENTERED",
        body={"values": all_rows},
    ).execute()

    logger.info("Wrote %d data rows to CURRENT_V2 (%s)", len(final_rows), write_range)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("Nightly CURRENT_V2 Sheet Update — starting")
    logger.info("=" * 60)

    # Step 1: TradingView Screen
    screened = run_tradingview_screen(logger)
    logger.info("Screened %d equities from TradingView", len(screened))

    # Connect to Google Sheets
    service = get_sheets_service()

    # Step 2: Enrich
    logger.info("=" * 60)
    logger.info("Step 2: Enriching from Google Sheet tabs")
    logger.info("=" * 60)

    sheet_gids = get_sheet_gids(service)
    logger.info("Sheet GIDs: %s", sheet_gids)

    ai_data, ai_row_map = load_ai_analysis(service, logger)
    ps_data, ps_row_map = load_price_sales(service, logger)
    first_seen_map = load_current_first_seen(service, logger)

    # Load existing CURRENT_V2 data
    existing = load_v2_data(service, logger)

    # Step 3: Upsert
    final_rows = upsert_v2(
        screened, ai_data, ai_row_map, ps_data, ps_row_map,
        first_seen_map, existing, sheet_gids, logger,
    )

    write_v2(service, final_rows, logger)

    logger.info("=" * 60)
    logger.info("Nightly CURRENT_V2 Sheet Update — complete (%d rows)", len(final_rows))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
