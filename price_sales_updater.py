#!/usr/bin/env python3
"""
Price-Sales Weekly Updater.

Reads tickers from the 'AI Analysis' sheet, fetches market cap and revenue data
from EODHD, computes Price-to-Sales ratios, and maintains a rolling 52-week P/S
history table in the 'Price-Sales' sheet tab.

For backfill (new tickers), fetches 52 weeks of weekly closing prices and
combines with revenue TTM to build historical P/S.  For weekly updates, fetches
the current fundamentals snapshot for a single new data point.

Runs every Sunday at 02:00 UTC via GitHub Actions.
"""

import argparse
import json
import logging
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SOURCE_SHEET = "AI Analysis"
PS_SHEET = "Price-Sales"
LOGS_SHEET = "Logs"

EODHD_BASE_URL = "https://eodhd.com/api"
EODHD_API_KEY = os.environ.get("EODHD_API_KEY", "")

DELAY_BETWEEN_CALLS = 0.5  # seconds between EODHD API calls
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Price-Sales sheet column order (matches header row)
PS_COLUMNS = [
    "ticker", "company", "ps_current", "ps_ath", "ps_52w_high", "ps_52w_low",
    "ps_12m_median", "pct_of_ath", "ps_history_json", "last_updated",
    "first_recorded",
]

# Logs sheet column order
LOG_COLUMNS = [
    "run_date", "backfilled", "updated", "skipped", "errors", "duration_secs",
]

# ---------------------------------------------------------------------------
# EODHD exchange mapping (reused from eodhd_updater.py)
# ---------------------------------------------------------------------------

EXCHANGE_TO_EODHD = {
    # United States
    "NASDAQ": "US", "NYSE": "US", "NYSEARCA": "US", "NYSEMKT": "US",
    "AMEX": "US", "OTC": "US", "BATS": "US", "US": "US",
    # United Kingdom
    "LSE": "LSE", "LON": "LSE", "LONDON": "LSE",
    # India
    "NSE": "NSE", "BSE": "BSE", "NSEI": "NSE",
    # Japan
    "TSE": "TSE", "TYO": "TSE", "JPX": "TSE",
    # Germany
    "XETRA": "XETRA", "FRA": "F", "ETR": "XETRA",
    "GETTEX": "MU", "MU": "MU", "STU": "STU",
    "BE": "BE", "DU": "DU", "HM": "HM", "HA": "HA", "FWB": "F",
    # Other Europe
    "EPA": "PA", "PAR": "PA", "AMS": "AS", "SWX": "SW",
    "BIT": "MI", "BME": "MC", "MIL": "MI", "VIE": "VI",
    "EURONEXT": "PA",
    # Asia-Pacific
    "HKG": "HK", "HKEX": "HK", "KRX": "KO", "KOSDAQ": "KO",
    "TWSE": "TW", "TPE": "TW", "SGX": "SG", "ASX": "AU", "NZX": "NZ",
    "MYX": "KL", "DFM": "AE",
    # Americas
    "TSX": "TO", "TSXV": "V", "SAO": "SA", "BVMF": "SA", "BMV": "MX",
    # Africa / Middle East
    "JSE": "JSE", "TADAWUL": "SR", "SAU": "SR",
    "NSENG": "NSENG", "NGS": "NSENG", "NGSE": "NSENG",
    "TRADEGATE": "MU",
}

EXCHANGE_FALLBACKS = {
    "MU": ["XETRA", "F", "US"], "STU": ["XETRA", "F", "US"],
    "BE": ["XETRA", "F", "US"], "DU": ["XETRA", "F", "US"],
    "HM": ["XETRA", "F", "US"], "HA": ["XETRA", "F", "US"],
    "XETRA": ["F", "US"], "F": ["XETRA", "US"],
    "BSE": ["NSE"], "NSE": ["BSE"],
    "TSE": ["US"], "LSE": ["US"],
    "TO": ["V", "US"], "V": ["TO", "US"],
    "HK": ["US"], "KO": ["US"], "AU": ["US"],
    "NSENG": ["LSE", "US"],
}

for _exc, _chain in EXCHANGE_FALLBACKS.items():
    if "US" not in _chain:
        _chain.append("US")


def _resolve_exchange(exchange: str) -> str:
    """Convert a spreadsheet exchange name to the EODHD suffix code."""
    key = exchange.strip().upper()
    return EXCHANGE_TO_EODHD.get(key, key)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"price_sales_{today_str}.txt"

    logger = logging.getLogger("price_sales_updater")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
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


def read_ticker_list(service, logger) -> list[dict]:
    """Read ticker + exchange from AI Analysis sheet, starting at row 3."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SOURCE_SHEET}'!A3:B",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    rows = result.get("values", [])
    tickers = []
    for row in rows:
        if len(row) >= 2 and row[0].strip():
            tickers.append({"ticker": row[0].strip(), "exchange": row[1].strip()})
        elif len(row) >= 1 and row[0].strip():
            tickers.append({"ticker": row[0].strip(), "exchange": ""})
    logger.info("Read %d tickers from %s", len(tickers), SOURCE_SHEET)
    return tickers


def read_ps_sheet(service, logger) -> dict[str, dict]:
    """Read all rows from Price-Sales sheet, return dict keyed by ticker."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{PS_SHEET}'!A1:K",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        logger.info("Price-Sales sheet is empty")
        return {}

    headers = rows[0]
    ps_map = {}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        entry = {}
        for i, h in enumerate(headers):
            entry[h] = row[i] if i < len(row) else ""
        ps_map[entry.get("ticker", "")] = entry

    logger.info("Read %d existing rows from %s", len(ps_map), PS_SHEET)
    return ps_map


def ensure_ps_sheet_exists(service, logger):
    """Create the Price-Sales sheet tab if it doesn't exist, with header row."""
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties")
        .execute()
    )
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == PS_SHEET:
            logger.info("Sheet '%s' already exists", PS_SHEET)
            return

    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {"addSheet": {"properties": {"title": PS_SHEET}}}
            ]
        },
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{PS_SHEET}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [PS_COLUMNS]},
    ).execute()

    sheet_id = _get_sheet_id(service, PS_SHEET)
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                }
            ]
        },
    ).execute()
    logger.info("Created sheet '%s' with header row", PS_SHEET)


def ensure_logs_sheet_exists(service, logger):
    """Create the Logs sheet tab if it doesn't exist, with header row."""
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties")
        .execute()
    )
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == LOGS_SHEET:
            return

    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {"addSheet": {"properties": {"title": LOGS_SHEET}}}
            ]
        },
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{LOGS_SHEET}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [LOG_COLUMNS]},
    ).execute()
    logger.info("Created sheet '%s' with header row", LOGS_SHEET)


def _get_sheet_id(service, sheet_name: str) -> int:
    """Return the numeric sheet ID for a given sheet name."""
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties")
        .execute()
    )
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == sheet_name:
            return sheet["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{sheet_name}' not found")


def append_rows(service, sheet_name: str, rows: list[list], logger):
    """Append rows to a sheet."""
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    logger.info("Appended %d rows to %s", len(rows), sheet_name)


def update_rows_by_ticker(service, sheet_name: str, updates: list[dict], logger):
    """Update existing rows by matching ticker in column A."""
    if not updates:
        return

    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!A:A",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    col_a = result.get("values", [])
    ticker_to_row = {}
    for i, row in enumerate(col_a):
        if row and row[0]:
            ticker_to_row[row[0]] = i + 1  # 1-indexed

    batch_data = []
    for upd in updates:
        ticker = upd["ticker"]
        row_num = ticker_to_row.get(ticker)
        if not row_num:
            logger.warning("Ticker %s not found in sheet for update, skipping", ticker)
            continue

        row_values = [upd.get(col, "") for col in PS_COLUMNS[1:]]
        batch_data.append(
            {
                "range": f"'{sheet_name}'!B{row_num}",
                "values": [row_values],
            }
        )

    if batch_data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": batch_data},
        ).execute()
        logger.info("Updated %d rows in %s", len(batch_data), sheet_name)


# ---------------------------------------------------------------------------
# EODHD API helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def eodhd_get(endpoint: str, params: dict | None = None,
              logger=None) -> dict | list | None:
    """Make a GET request to EODHD API."""
    url = f"{EODHD_BASE_URL}/{endpoint}"
    query = {"api_token": EODHD_API_KEY, "fmt": "json"}
    if params:
        query.update(params)

    try:
        resp = requests.get(url, params=query, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if logger:
            logger.warning("EODHD %s → HTTP %s", endpoint, status)
        return None
    except Exception as e:
        if logger:
            logger.warning("EODHD request failed for %s: %s", endpoint, e)
        return None


def fetch_fundamentals(ticker: str, exchange: str, logger) -> dict | None:
    """Fetch EODHD fundamentals with exchange fallback.

    Returns the full JSON response or None.
    """
    eodhd_exchange = _resolve_exchange(exchange)
    symbol = f"{ticker}.{eodhd_exchange}"

    time.sleep(DELAY_BETWEEN_CALLS)
    data = eodhd_get(f"fundamentals/{symbol}", logger=logger)
    if data and isinstance(data, dict) and data.get("Financials"):
        return data

    # Try fallbacks
    fallbacks = EXCHANGE_FALLBACKS.get(eodhd_exchange, ["US"])
    for fb_exchange in fallbacks:
        if fb_exchange == eodhd_exchange:
            continue
        fb_symbol = f"{ticker}.{fb_exchange}"
        logger.info("Retrying fundamentals: %s → %s", symbol, fb_symbol)
        time.sleep(DELAY_BETWEEN_CALLS)
        data = eodhd_get(f"fundamentals/{fb_symbol}", logger=logger)
        if data and isinstance(data, dict) and data.get("Financials"):
            return data

    return None


def fetch_weekly_prices(ticker: str, exchange: str, from_date: str,
                        to_date: str, logger) -> list | None:
    """Fetch weekly end-of-day prices from EODHD for backfill.

    Returns list of {date, close} dicts sorted oldest-first, or None.
    """
    eodhd_exchange = _resolve_exchange(exchange)
    symbol = f"{ticker}.{eodhd_exchange}"

    time.sleep(DELAY_BETWEEN_CALLS)
    data = eodhd_get(
        f"eod/{symbol}",
        params={"from": from_date, "to": to_date, "period": "w", "order": "a"},
        logger=logger,
    )
    if data and isinstance(data, list) and len(data) > 0:
        logger.info("Got %d weekly prices for %s (%s to %s)", len(data), symbol, from_date, to_date)
        return data
    logger.info("No weekly prices for %s (got %s)", symbol, type(data).__name__)

    # Try fallbacks
    fallbacks = EXCHANGE_FALLBACKS.get(eodhd_exchange, ["US"])
    for fb_exchange in fallbacks:
        if fb_exchange == eodhd_exchange:
            continue
        fb_symbol = f"{ticker}.{fb_exchange}"
        logger.info("Retrying eod prices: %s → %s", symbol, fb_symbol)
        time.sleep(DELAY_BETWEEN_CALLS)
        data = eodhd_get(
            f"eod/{fb_symbol}",
            params={"from": from_date, "to": to_date, "period": "w", "order": "a"},
            logger=logger,
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data

    return None


def get_revenue_ttm(fundamentals: dict) -> float | None:
    """Extract trailing-twelve-month revenue from EODHD fundamentals."""
    # Try Highlights.RevenueTTM first
    highlights = fundamentals.get("Highlights", {})
    rev_ttm = _safe_float(highlights.get("RevenueTTM"))
    if rev_ttm and rev_ttm > 0:
        return rev_ttm

    # Fallback: sum last 4 quarterly income statements
    financials = fundamentals.get("Financials", {})
    quarterly = financials.get("Income_Statement", {}).get("quarterly", {})
    if not quarterly:
        return None

    # Sort by date descending
    sorted_q = sorted(quarterly.items(), key=lambda x: x[0], reverse=True)
    total = 0.0
    count = 0
    for _date, entry in sorted_q[:4]:
        rev = _safe_float(entry.get("totalRevenue"))
        if rev is not None:
            total += rev
            count += 1
    return total if count >= 2 else None  # need at least 2 quarters


def get_market_cap(fundamentals: dict) -> float | None:
    """Extract market capitalization from EODHD fundamentals."""
    highlights = fundamentals.get("Highlights", {})
    return _safe_float(highlights.get("MarketCapitalization"))


def get_shares_outstanding(fundamentals: dict) -> float | None:
    """Extract shares outstanding from EODHD fundamentals."""
    shares = fundamentals.get("SharesStats", {})
    val = _safe_float(shares.get("SharesOutstanding"))
    if val and val > 0:
        return val
    # Fallback: MarketCap / price
    highlights = fundamentals.get("Highlights", {})
    mcap = _safe_float(highlights.get("MarketCapitalization"))
    # General.PreviousClose or similar
    general = fundamentals.get("General", {})
    # Try technicals
    technicals = fundamentals.get("Technicals", {})
    price = _safe_float(technicals.get("50DayMA"))
    if mcap and price and price > 0:
        return mcap / price
    return None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def get_last_friday() -> date:
    """Return the most recent Friday (looking back from today)."""
    today = date.today()
    days_since_friday = (today.weekday() - 4) % 7
    if days_since_friday == 0 and today.weekday() != 4:
        days_since_friday = 7
    if today.weekday() == 4:
        days_since_friday = 0
    return today - timedelta(days=days_since_friday)


def get_backfill_from() -> date:
    """Return the date ~52 weeks ago for backfill."""
    return date.today() - timedelta(weeks=52)


# ---------------------------------------------------------------------------
# Core P/S logic
# ---------------------------------------------------------------------------


def compute_ps_for_ticker(
    ticker: str,
    exchange: str,
    existing: dict | None,
    last_friday: date,
    backfill_from: date,
    logger,
) -> dict | None:
    """Fetch data from EODHD and compute P/S ratio + history for one ticker.

    Returns a dict ready for sheet writing, or None if data is insufficient.
    """
    last_friday_str = last_friday.isoformat()
    mode = "backfill" if existing is None else "update"

    # --- Fetch fundamentals (market cap, revenue, shares) ---
    fundamentals = fetch_fundamentals(ticker, exchange, logger)
    if not fundamentals:
        logger.warning("SKIP %s: no fundamentals data", ticker)
        return None

    # Extract company name
    general = fundamentals.get("General", {})
    company_name = general.get("Name", "")

    revenue_ttm = get_revenue_ttm(fundamentals)
    if not revenue_ttm or revenue_ttm <= 0:
        logger.warning("SKIP %s: revenue_ttm=%s", ticker, revenue_ttm)
        return None

    market_cap = get_market_cap(fundamentals)
    shares = get_shares_outstanding(fundamentals)

    # Current P/S from fundamentals
    if market_cap and market_cap > 0:
        ps_current = round(market_cap / revenue_ttm, 2)
    else:
        logger.warning("SKIP %s: no market cap data", ticker)
        return None

    # --- Build history ---
    new_history = []

    if mode == "backfill":
        # Fetch weekly prices for backfill
        backfill_from_str = backfill_from.isoformat()
        weekly_prices = fetch_weekly_prices(
            ticker, exchange, backfill_from_str, last_friday_str, logger
        )

        if weekly_prices and len(weekly_prices) > 1:
            # Use price-ratio method: ps_week = ps_current * (week_close / latest_close)
            # This is equivalent to (price * shares) / revenue but doesn't need shares
            latest_close = _safe_float(
                weekly_prices[-1].get("adjusted_close")
                or weekly_prices[-1].get("close")
            )
            if latest_close and latest_close > 0:
                for day in weekly_prices[-52:]:
                    close = _safe_float(day.get("adjusted_close") or day.get("close"))
                    if not close or close <= 0:
                        continue
                    ps_val = round(ps_current * (close / latest_close), 2)
                    if ps_val > 0:
                        new_history.append([day["date"], ps_val])
                logger.info("%s: backfilled %d weekly data points", ticker, len(new_history))
            else:
                logger.info("%s: latest close invalid, using current P/S only", ticker)
        else:
            logger.info("%s: no weekly prices returned (%s), using current P/S only",
                        ticker, "None" if weekly_prices is None else f"{len(weekly_prices)} pts")

        # Always ensure we have at least the current data point
        if not new_history or new_history[-1][0] != last_friday_str:
            new_history.append([last_friday_str, ps_current])

    else:
        # Update: parse existing history, append new data point
        try:
            existing_history = json.loads(existing.get("ps_history_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            existing_history = []

        existing_history.append([last_friday_str, ps_current])
        # Keep rolling 52-entry window
        if len(existing_history) > 52:
            existing_history = existing_history[-52:]
        new_history = existing_history

    if not new_history:
        logger.warning("SKIP %s: empty history after processing", ticker)
        return None

    # --- Compute stats ---
    values = [h[1] for h in new_history]
    ps_52w_high = round(max(values), 2)
    ps_52w_low = round(min(values), 2)
    ps_12m_median = round(statistics.median(values), 2)

    # ATH: sticky — never goes down
    prev_ath = 0
    if existing:
        prev_ath = _safe_float(existing.get("ps_ath")) or 0
    ps_ath = round(max(prev_ath, ps_current, ps_52w_high), 2)
    pct_of_ath = round(ps_current / ps_ath, 2) if ps_ath > 0 else 0

    first_recorded = (
        new_history[0][0]
        if mode == "backfill"
        else (existing.get("first_recorded") or new_history[0][0])
    )

    logger.info(
        "OK %s [%s]: ps=%.2f ath=%.2f 52wH=%.2f 52wL=%.2f med=%.2f (%d pts)",
        ticker, mode, ps_current, ps_ath, ps_52w_high, ps_52w_low,
        ps_12m_median, len(new_history),
    )

    return {
        "ticker": ticker,
        "company": company_name,
        "ps_current": ps_current,
        "ps_ath": ps_ath,
        "ps_52w_high": ps_52w_high,
        "ps_52w_low": ps_52w_low,
        "ps_12m_median": ps_12m_median,
        "pct_of_ath": pct_of_ath,
        "ps_history_json": json.dumps(new_history),
        "last_updated": last_friday_str,
        "first_recorded": first_recorded,
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()
    logger = setup_logging()
    start_time = time.time()

    parser = argparse.ArgumentParser(description="Price-Sales weekly updater")
    parser.add_argument(
        "--tickers",
        nargs="*",
        help="Process only these tickers (for testing). E.g. --tickers DDOG CHOLAFIN CGD",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force update all tickers (ignore staleness check)",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Price-Sales updater starting (EODHD)")
    logger.info("=" * 60)

    if not EODHD_API_KEY:
        logger.error("EODHD_API_KEY env var is not set")
        sys.exit(1)

    service = get_sheets_service()

    # Ensure sheets exist
    ensure_ps_sheet_exists(service, logger)
    ensure_logs_sheet_exists(service, logger)

    # Read inputs
    ticker_list = read_ticker_list(service, logger)
    ps_map = read_ps_sheet(service, logger)

    # Filter to specific tickers if requested
    if args.tickers:
        filter_set = set(t.upper() for t in args.tickers)
        ticker_list = [t for t in ticker_list if t["ticker"].upper() in filter_set]
        logger.info("Filtered to %d tickers: %s", len(ticker_list), args.tickers)

    # Compute dates
    last_friday = get_last_friday()
    backfill_from = get_backfill_from()
    last_friday_str = last_friday.isoformat()
    logger.info("Last Friday: %s | Backfill from: %s", last_friday, backfill_from)

    # Classify and process tickers
    new_rows = []
    update_rows = []
    skipped = 0
    errors = 0

    for item in ticker_list:
        ticker = item["ticker"]
        exchange = item["exchange"]
        existing = ps_map.get(ticker)

        # Classify
        if existing is None:
            mode = "backfill"
        elif args.force:
            mode = "update"
        elif not existing.get("last_updated") or existing["last_updated"] < last_friday_str:
            mode = "update"
        else:
            skipped += 1
            continue

        logger.info("Processing %s (%s) — mode=%s", ticker, exchange, mode)

        try:
            result = compute_ps_for_ticker(
                ticker, exchange,
                existing if mode == "update" else None,
                last_friday, backfill_from, logger,
            )
        except Exception as e:
            logger.error("ERROR processing %s: %s", ticker, e)
            errors += 1
            continue

        if result is None:
            errors += 1
            continue

        if result["mode"] == "backfill":
            new_rows.append(result)
        else:
            update_rows.append(result)

    # Write results to sheet
    logger.info("Writing results: %d new, %d updates, %d skipped, %d errors",
                len(new_rows), len(update_rows), skipped, errors)

    # Append new rows
    if new_rows:
        sheet_rows = []
        for r in new_rows:
            sheet_rows.append([r.get(col, "") for col in PS_COLUMNS])
        append_rows(service, PS_SHEET, sheet_rows, logger)

    # Update existing rows
    if update_rows:
        update_rows_by_ticker(service, PS_SHEET, update_rows, logger)

    # Write log entry
    duration = round(time.time() - start_time, 1)
    log_row = [
        date.today().isoformat(),
        len(new_rows),
        len(update_rows),
        skipped,
        errors,
        duration,
    ]
    append_rows(service, LOGS_SHEET, [log_row], logger)

    logger.info("=" * 60)
    logger.info(
        "Done in %.1fs — backfilled=%d updated=%d skipped=%d errors=%d",
        duration, len(new_rows), len(update_rows), skipped, errors,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
