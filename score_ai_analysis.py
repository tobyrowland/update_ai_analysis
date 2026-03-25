#!/usr/bin/env python3
"""
Score & Rank AI Analysis Sheet.

Reads AI Analysis, Price-Sales, Manual, and TradingView market data to compute
status and composite_score for every ticker. Sorts the sheet by status priority
then composite score descending, and writes the screening columns back.

Schedule: 06:30 UTC daily (after eodhd_updater and update_ai_narratives).
"""

import json
import logging
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from scipy.stats import percentileofscore

from tv_screen import run_tradingview_screen, fetch_market_data

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
AI_ANALYSIS_SHEET = "AI Analysis"
PRICE_SALES_SHEET = "Price-Sales"
MANUAL_SHEET = "Manual"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

NULL_VALUE = "—"

# Map alternative/legacy header names → canonical internal keys.
# The sheet uses display names like "price_%_of_52w_high"; our internal key
# is "price_pct_of_52w_high".  Both must resolve to the same key.
HEADER_ALIASES = {
    "Ticker": "ticker",
    "ticker_clean": "ticker",
    "Company": "company_name",
    "Company Name": "company_name",
    "Exchange": "exchange",
    "Country": "country",
    "Sector": "sector",
    "Status": "status",
    "Composite Score": "composite_score",
    "composite_score": "composite_score",
    "Price": "price",
    "PS Now": "ps_now",
    "ps_now": "ps_now",
    "price_%_of_52w_high": "price_pct_of_52w_high",
    "perf_52w_vs_spy": "perf_52w_vs_spy",
    "Perf 52W vs SPY": "perf_52w_vs_spy",
    "Rating": "rating",
    "Short Outlook": "short_outlook",
    "R40 Score": "r40_score",
    "AI": "ai",
    "Analyzed": "ai",
    "AI Analyzed": "ai",
    "Data": "data",
    "Data As Of": "data",
    "Fundamentals Date": "data",
}

# Status priority for sorting (❌ Excluded always at bottom)
STATUS_PRIORITY = {
    "📌": 1, "🏷️": 1, "🟢 Eligible": 1, "🟢": 1,
    "🆕 New": 2, "🆕": 2,
    "📌❌": 3, "❌": 3,
}

# Screening columns we write to AI Analysis
SCREENING_COLS = [
    "status", "composite_score", "price",
    "ps_now", "price_pct_of_52w_high", "perf_52w_vs_spy", "rating",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"score_ai_analysis_{date.today().isoformat()}.txt"

    logger = logging.getLogger("score_ai_analysis")
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


def _extract_ticker(val):
    """Extract ticker from a HYPERLINK formula or plain text."""
    val = str(val).strip()
    if not val:
        return ""
    match = re.search(r'=HYPERLINK\([^,]+,\s*"([^"]+)"\)', val)
    if match:
        return match.group(1).strip().upper()
    return val.strip().upper()


def _safe_float(val):
    """Try to convert a value to float, return None on failure."""
    if val is None or val == "" or val == NULL_VALUE:
        return None
    try:
        cleaned = str(val).strip().rstrip("%")
        f = float(cleaned)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def read_sheet(service, sheet_name: str, end_col: str = "AZ"):
    """Read all rows from a sheet tab."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!A1:{end_col}",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


# ---------------------------------------------------------------------------
# Load data sources
# ---------------------------------------------------------------------------


def load_ai_analysis(service, logger) -> tuple[list[dict], dict[str, int]]:
    """
    Read AI Analysis sheet.
    Returns:
        rows: list of dicts with all column values + metadata
        col_map: {header_name: col_index}
    """
    raw_rows = read_sheet(service, AI_ANALYSIS_SHEET)
    if len(raw_rows) < 2:
        logger.warning("AI Analysis has fewer than 2 rows")
        return [], {}

    # Build column map from row 2 headers
    raw_headers = [str(h).strip() for h in raw_rows[1]]
    col_map = {}
    for idx, h in enumerate(raw_headers):
        key = HEADER_ALIASES.get(h, h.lower())
        col_map[key] = idx

    ticker_idx = col_map.get("ticker")
    if ticker_idx is None:
        logger.error("Cannot find 'ticker' column in AI Analysis headers: %s", raw_headers)
        return [], col_map

    data = []
    for row_offset, row in enumerate(raw_rows[2:]):
        padded = row + [""] * (max(col_map.values()) + 1 - len(row))
        ticker = _extract_ticker(padded[ticker_idx])
        if not ticker:
            continue

        entry = {"_sheet_row": row_offset + 3, "_ticker": ticker}

        # Extract all mapped columns
        for key, idx in col_map.items():
            entry[key] = padded[idx].strip() if idx < len(padded) else ""

        # Detect red/yellow flags (🔴/🟡 markers on any column)
        red_flags = []
        yellow_flags = []
        for col_name, col_idx in col_map.items():
            if col_name in ("ticker", "status", "composite_score"):
                continue
            cell_val = padded[col_idx].strip() if col_idx < len(padded) else ""
            if "🔴" in cell_val:
                red_flags.append(col_name)
            if "🟡" in cell_val:
                yellow_flags.append(col_name)
        entry["_red_flags"] = red_flags
        entry["_yellow_flags"] = yellow_flags

        data.append(entry)

    logger.info("Loaded %d tickers from AI Analysis", len(data))
    return data, col_map


def load_price_sales(service, logger) -> dict[str, dict]:
    """Read Price-Sales sheet, return {ticker: {ps_now, 52w_high, 12m_median}}."""
    try:
        rows = read_sheet(service, PRICE_SALES_SHEET, end_col="K")
    except Exception as e:
        logger.warning("Could not read Price-Sales sheet: %s", e)
        return {}

    if len(rows) < 2:
        return {}

    # Detect headers
    row1_lower = [str(h).strip().lower() for h in rows[0]]
    if "ticker" in row1_lower or "ticker_clean" in row1_lower:
        headers = row1_lower
        data_rows = rows[1:]
    elif len(rows) >= 3:
        headers = [str(h).strip().lower() for h in rows[1]]
        data_rows = rows[2:]
    else:
        headers = row1_lower
        data_rows = rows[1:]

    hmap = {h: i for i, h in enumerate(headers)}
    ticker_idx = hmap.get("ticker")
    if ticker_idx is None:
        ticker_idx = hmap.get("ticker_clean")
    ps_now_idx = hmap.get("ps_now")
    high_idx = hmap.get("52w_high")
    median_idx = hmap.get("12m_median")

    if ticker_idx is None:
        logger.warning("Price-Sales has no ticker column — headers found: %s", headers)
        return {}

    result = {}
    for row in data_rows:
        max_needed = max(c for c in [ticker_idx, ps_now_idx, high_idx, median_idx] if c is not None)
        padded = row + [""] * (max_needed + 1 - len(row))
        ticker = padded[ticker_idx].strip().upper()
        if not ticker:
            continue
        result[ticker] = {
            "ps_now": _safe_float(padded[ps_now_idx]) if ps_now_idx is not None else None,
            "52w_high": _safe_float(padded[high_idx]) if high_idx is not None else None,
            "12m_median": _safe_float(padded[median_idx]) if median_idx is not None else None,
        }

    logger.info("Loaded Price-Sales data for %d tickers", len(result))
    return result


def load_manual_tickers(service, logger) -> set[str]:
    """Read Manual sheet and return set of uppercase tickers."""
    try:
        rows = read_sheet(service, MANUAL_SHEET, end_col="Z")
    except Exception:
        return set()

    if len(rows) < 2:
        return set()

    row1_lower = [str(h).strip().lower() for h in rows[0]]
    if "ticker" in row1_lower or "ticker_clean" in row1_lower:
        headers = row1_lower
        data_rows = rows[1:]
    elif len(rows) >= 3:
        headers = [str(h).strip().lower() for h in rows[1]]
        data_rows = rows[2:]
    else:
        headers = row1_lower
        data_rows = rows[1:]

    ticker_idx = None
    for i, h in enumerate(headers):
        if h in ("ticker", "ticker_clean"):
            ticker_idx = i
            break

    if ticker_idx is None:
        return set()

    tickers = set()
    for row in data_rows:
        if len(row) > ticker_idx and row[ticker_idx].strip():
            tickers.add(row[ticker_idx].strip().upper())

    logger.info("Loaded %d Manual tickers", len(tickers))
    return tickers


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------


def parse_r40_score(r40_str):
    """Parse r40_score like '💎💎💎 R40: 90' → 90."""
    if not r40_str:
        return None
    match = re.search(r"R40:\s*(\d+)", str(r40_str))
    if match:
        return int(match.group(1))
    return None


def parse_rating_numeric(rating_str):
    """Parse rating like '1.8' → 1.8."""
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
    for emoji in ("📌❌", "📌", "🏷️", "🟢", "🆕", "❌"):
        if status_str.startswith(emoji):
            return emoji
    return status_str


def compute_status(entry, ps_data, screened_tickers, manual_tickers):
    """Determine status for a single ticker."""
    ticker = entry["_ticker"]
    red_flags = entry.get("_red_flags", [])
    has_ai = bool(entry.get("ai", "").strip())
    has_eodhd = bool(entry.get("data", "").strip())
    is_manual = ticker in manual_tickers
    is_screened = ticker in screened_tickers
    is_manual_only = is_manual and not is_screened

    if red_flags:
        flag_names = ", ".join(red_flags[:3])
        if is_manual_only:
            return f"📌❌ {flag_names}"
        return f"❌ {flag_names}"

    sector = entry.get("sector", "")
    net_margin = _safe_float(entry.get("net_margin%", ""))
    if sector == "Health Technology" and net_margin is not None and net_margin < 0:
        if is_manual_only:
            return "📌❌ Unprofitable Health Tech"
        return "❌ Unprofitable Health Tech"

    if is_manual_only:
        return "📌 Manual"

    if has_ai and has_eodhd:
        ps_row = ps_data.get(ticker, {})
        ps_now = ps_row.get("ps_now")
        median = ps_row.get("12m_median")
        if ps_now is not None and median is not None and median > 0 and ps_now / median < 0.80:
            pct = round((1 - ps_now / median) * 100)
            return f"🏷️ -{pct}% vs. 52w p/s"
        return "🟢 Eligible"

    return "🆕 New"


def compute_composite_score(row_data, all_rows):
    """Calculate composite score using percentile-based weighting."""
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


# ---------------------------------------------------------------------------
# Sort and write back
# ---------------------------------------------------------------------------


def sort_key(entry):
    """Sort by status priority, then composite score descending."""
    status = str(entry.get("status", "")).strip()
    priority = STATUS_PRIORITY.get(status, 99)
    if priority == 99:
        emoji = _status_base(status)
        for k, v in STATUS_PRIORITY.items():
            if k.startswith(emoji) and emoji:
                priority = v
                break
    return (priority, -entry.get("_composite_score", 0))


def write_sorted_sheet(service, entries: list[dict], col_map: dict, raw_rows: list, logger):
    """
    Rewrite AI Analysis data rows sorted by status priority + composite score.
    Only updates screening columns + reorders rows.
    """
    if not entries:
        logger.warning("No entries to write")
        return

    # We need to rewrite all data rows (row 3+) in the new sorted order.
    # Preserve all existing cell values, only update screening columns.
    max_col = max(col_map.values()) + 1

    sorted_rows = []
    for entry in entries:
        sheet_row = entry["_sheet_row"]
        # Get the original row data from raw_rows
        original_idx = sheet_row - 1  # raw_rows is 0-indexed, sheet_row is 1-indexed
        if original_idx < len(raw_rows):
            row_data = list(raw_rows[original_idx])
            # Pad to max column width
            row_data += [""] * (max_col - len(row_data))
        else:
            row_data = [""] * max_col

        # Update screening columns
        for col_name in SCREENING_COLS:
            if col_name in col_map:
                idx = col_map[col_name]
                val = entry.get(col_name, "")
                if val is None:
                    val = ""
                elif isinstance(val, float):
                    if math.isnan(val) or math.isinf(val):
                        val = ""
                    elif col_name == "composite_score":
                        val = round(val, 1)
                    elif col_name in ("price", "ps_now"):
                        val = round(val, 2)
                    elif col_name == "price_pct_of_52w_high":
                        val = round(val, 4)
                    elif col_name == "perf_52w_vs_spy":
                        val = round(val, 4)
                row_data[idx] = val

        sorted_rows.append(row_data)

    # Write all data rows starting at row 3
    end_col = _col_letter(max_col - 1)
    end_row = len(sorted_rows) + 2  # +2 for the two header rows
    range_str = f"'{AI_ANALYSIS_SHEET}'!A3:{end_col}{end_row}"

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body={"values": sorted_rows},
    ).execute()

    logger.info("Wrote %d sorted rows to %s", len(sorted_rows), range_str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Score AI Analysis — %s", date.today().isoformat())
    logger.info("=" * 60)

    service = get_sheets_service()

    # Step 1: Load all data sources
    logger.info("Step 1: Loading data sources...")

    # Read raw AI Analysis rows (we need them for the sorted rewrite)
    raw_rows = read_sheet(service, AI_ANALYSIS_SHEET)

    ai_entries, col_map = load_ai_analysis(service, logger)
    if not ai_entries:
        logger.warning("No AI Analysis data — nothing to score")
        return

    # Warn about missing screening columns (must be added manually to the sheet)
    missing_cols = [c for c in SCREENING_COLS if c not in col_map]
    if missing_cols:
        logger.warning("Screening columns missing from AI Analysis headers (add them to row 2): %s",
                        missing_cols)

    ps_data = load_price_sales(service, logger)
    manual_tickers = load_manual_tickers(service, logger)

    # Step 2: Run TradingView screen for market data
    logger.info("Step 2: TradingView screen for market data...")
    screened = run_tradingview_screen(logger)
    screened_map = {eq["ticker"].upper(): eq for eq in screened}
    screened_tickers = set(screened_map.keys())
    logger.info("TradingView returned %d equities", len(screened))

    # Step 2b: Fetch market data for tickers NOT in the screen
    all_ai_tickers = {e["_ticker"] for e in ai_entries}
    missing_tickers = [
        (e["_ticker"], e.get("exchange", ""))
        for e in ai_entries
        if e["_ticker"] not in screened_tickers
    ]
    if missing_tickers:
        logger.info("Fetching market data for %d tickers not in TradingView screen...",
                    len(missing_tickers))
        extra_data = fetch_market_data(missing_tickers, logger)
    else:
        extra_data = {}

    # Step 3: Compute status and scoring inputs for each ticker
    logger.info("Step 3: Computing status and scores...")

    for entry in ai_entries:
        ticker = entry["_ticker"]

        # Merge market data — prefer screened data, fall back to direct lookup
        tv = screened_map.get(ticker) or extra_data.get(ticker, {})
        entry["price"] = tv.get("price")
        entry["perf_52w_vs_spy"] = tv.get("perf_52w_vs_spy")
        entry["rating"] = tv.get("rating", "")

        # Merge P/S data
        ps = ps_data.get(ticker, {})
        ps_now = ps.get("ps_now")
        entry["ps_now"] = ps_now
        high_52w = ps.get("52w_high")
        if ps_now is not None and high_52w is not None and high_52w > 0:
            entry["price_pct_of_52w_high"] = ps_now / high_52w
        else:
            entry["price_pct_of_52w_high"] = None

        # Status
        entry["status"] = compute_status(entry, ps_data, screened_tickers, manual_tickers)

        # Scoring inputs
        entry["_ps_now_f"] = ps_now
        entry["_perf_f"] = _safe_float(entry.get("perf_52w_vs_spy"))
        entry["_r40_f"] = parse_r40_score(entry.get("r40_score", ""))
        entry["_rating_f"] = parse_rating_numeric(entry.get("rating", ""))

    # Step 4: Compute composite scores with penalties
    for entry in ai_entries:
        raw = compute_composite_score(entry, ai_entries)

        # Penalty from short_outlook emoji
        outlook = str(entry.get("short_outlook", "")).strip()
        if outlook.startswith("🔴"):
            raw *= 0.25
        elif outlook.startswith("🟡"):
            raw *= 0.50

        # Penalty from 🟡 flags
        if entry.get("_yellow_flags"):
            raw *= 0.50

        entry["_composite_score"] = raw
        entry["composite_score"] = raw

    # Step 5: Sort
    ai_entries.sort(key=sort_key)

    # Log top 10
    logger.info("Top 10 by composite score:")
    for i, entry in enumerate(ai_entries[:10]):
        logger.info("  %2d. %-8s %s  score=%.1f",
                    i + 1, entry["_ticker"], entry["status"], entry["_composite_score"])

    # Step 6: Write sorted results back to AI Analysis
    logger.info("Step 6: Writing sorted results...")
    write_sorted_sheet(service, ai_entries, col_map, raw_rows, logger)

    # Summary
    status_counts = {}
    for entry in ai_entries:
        base = _status_base(entry["status"])
        status_counts[base] = status_counts.get(base, 0) + 1
    logger.info("Status summary: %s", status_counts)
    logger.info("Score AI Analysis complete — %d tickers scored and sorted", len(ai_entries))


if __name__ == "__main__":
    main()
