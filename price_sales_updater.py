#!/usr/bin/env python3
"""
Price-Sales Weekly Updater.

Reads tickers from the 'AI Analysis' sheet, fetches price/financial data from
FMP (Financial Modeling Prep), computes Price-to-Sales ratios, and maintains
a rolling 52-week P/S history table in the 'Price-Sales' sheet tab.

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

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"
FMP_API_KEY = "AzoPanREVvnVtzhfghwGtHyE4I2sf4AU"

DELAY_BETWEEN_CALLS = 0.5  # seconds between FMP API calls (rate limiting)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Price-Sales sheet column order (matches header row)
PS_COLUMNS = [
    "ticker", "ps_current", "ps_ath", "ps_52w_high", "ps_52w_low",
    "ps_12m_median", "pct_of_ath", "ps_history_json", "last_updated",
    "first_recorded",
]

# Logs sheet column order
LOG_COLUMNS = [
    "run_date", "backfilled", "updated", "skipped", "errors", "duration_secs",
]

# ---------------------------------------------------------------------------
# FMP ticker formatting
# ---------------------------------------------------------------------------

US_EXCHANGES = {"NASDAQ", "NYSE", "OTC", "AMEX"}

SUFFIX_MAP = {
    "NSE": ".NS",        # India (NSE)
    "BSE": ".BO",        # India (BSE)
    "GETTEX": ".F",      # Germany (Frankfurt via Gettex)
    "FWB": ".F",         # Germany (Frankfurt)
    "DUS": ".DU",        # Germany (Düsseldorf)
    "MIL": ".MI",        # Italy (Milan)
    "VIE": ".VI",        # Austria (Vienna)
    "BMV": ".MX",        # Mexico
    "DFM": ".AE",        # UAE (Dubai)
    "ASX": ".AX",        # Australia
    "MYX": ".KL",        # Malaysia
    "EURONEXT": ".PA",   # Euronext Paris (default)
    "TRADEGATE": ".TG",  # Germany (Tradegate)
    "NSENG": ".LG",      # Nigeria (Lagos)
}


def build_fmp_ticker(ticker: str, exchange: str) -> str:
    """Convert ticker + exchange to FMP-compatible ticker symbol."""
    if exchange in US_EXCHANGES:
        return ticker
    suffix = SUFFIX_MAP.get(exchange)
    if suffix:
        return ticker + suffix
    return ticker  # fallback: bare ticker


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


def _col_letter(index: int) -> str:
    """Convert 0-based column index to spreadsheet letter (0=A, 25=Z, 26=AA)."""
    result = ""
    while True:
        result = chr(ord("A") + index % 26) + result
        index = index // 26 - 1
        if index < 0:
            break
    return result


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
            range=f"'{PS_SHEET}'!A1:J",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        logger.info("Price-Sales sheet is empty")
        return {}

    # First row is header
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

    # Create the sheet
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {
                    "addSheet": {
                        "properties": {"title": PS_SHEET}
                    }
                }
            ]
        },
    ).execute()

    # Write header row
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{PS_SHEET}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [PS_COLUMNS]},
    ).execute()

    # Freeze row 1
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
                {
                    "addSheet": {
                        "properties": {"title": LOGS_SHEET}
                    }
                }
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
    """Update existing rows by matching ticker in column A.

    Each update dict has keys matching PS_COLUMNS.
    """
    if not updates:
        return

    # Read current sheet to find row numbers for each ticker
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

        # Build row values in column order (skip ticker — column A stays)
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
# FMP API helpers
# ---------------------------------------------------------------------------


def fmp_get(endpoint: str, params: dict | None = None, logger=None) -> dict | list | None:
    """Make a GET request to FMP API. Returns parsed JSON or None on error."""
    url = f"{FMP_BASE_URL}/{endpoint}"
    query = {"apikey": FMP_API_KEY}
    if params:
        query.update(params)

    try:
        resp = requests.get(url, params=query, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # FMP returns empty list for no data, or error dict
        if isinstance(data, dict) and "Error Message" in data:
            if logger:
                logger.warning("FMP error for %s: %s", endpoint, data["Error Message"])
            return None
        return data
    except Exception as e:
        if logger:
            logger.warning("FMP request failed for %s: %s", endpoint, e)
        return None


def fetch_profile(fmp_ticker: str, bare_ticker: str, logger) -> dict | None:
    """Fetch company profile. Retry with bare ticker if formatted one fails."""
    data = fmp_get(f"profile/{fmp_ticker}", logger=logger)
    if data and isinstance(data, list) and len(data) > 0:
        return data[0]

    # Retry with bare ticker if different
    if fmp_ticker != bare_ticker:
        logger.info("Retrying profile with bare ticker: %s", bare_ticker)
        data = fmp_get(f"profile/{bare_ticker}", logger=logger)
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]

    return None


def fetch_income_statements(fmp_ticker: str, bare_ticker: str, logger) -> list | None:
    """Fetch 4 most recent quarterly income statements."""
    data = fmp_get(
        f"income-statement/{fmp_ticker}",
        params={"period": "quarter", "limit": "4"},
        logger=logger,
    )
    if data and isinstance(data, list) and len(data) > 0:
        return data

    if fmp_ticker != bare_ticker:
        logger.info("Retrying income statement with bare ticker: %s", bare_ticker)
        data = fmp_get(
            f"income-statement/{bare_ticker}",
            params={"period": "quarter", "limit": "4"},
            logger=logger,
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data

    return None


def fetch_historical_prices(
    fmp_ticker: str, bare_ticker: str, from_date: str, to_date: str, logger
) -> list | None:
    """Fetch historical daily prices for a date range."""
    data = fmp_get(
        f"historical-price-full/{fmp_ticker}",
        params={"from": from_date, "to": to_date},
        logger=logger,
    )
    if data and isinstance(data, dict) and data.get("historical"):
        return data["historical"]

    if fmp_ticker != bare_ticker:
        logger.info("Retrying historical prices with bare ticker: %s", bare_ticker)
        data = fmp_get(
            f"historical-price-full/{bare_ticker}",
            params={"from": from_date, "to": to_date},
            logger=logger,
        )
        if data and isinstance(data, dict) and data.get("historical"):
            return data["historical"]

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


def is_friday(date_str: str) -> bool:
    """Check if a YYYY-MM-DD date string is a Friday."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.weekday() == 4
    except ValueError:
        return False


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
    """Fetch data from FMP and compute P/S ratio + history for one ticker.

    Returns a dict ready for sheet writing, or None if data is insufficient.
    """
    fmp_ticker = build_fmp_ticker(ticker, exchange)
    bare_ticker = ticker
    last_friday_str = last_friday.isoformat()
    backfill_from_str = backfill_from.isoformat()

    mode = "backfill" if existing is None else "update"

    # --- Fetch profile ---
    time.sleep(DELAY_BETWEEN_CALLS)
    profile = fetch_profile(fmp_ticker, bare_ticker, logger)
    if not profile:
        logger.warning("SKIP %s: no profile data", ticker)
        return None

    shares = profile.get("sharesOutstanding")
    if not shares or float(shares) <= 0:
        logger.warning("SKIP %s: sharesOutstanding=%s", ticker, shares)
        return None
    shares = float(shares)

    # --- Fetch income statements ---
    time.sleep(DELAY_BETWEEN_CALLS)
    statements = fetch_income_statements(fmp_ticker, bare_ticker, logger)
    if not statements:
        logger.warning("SKIP %s: no income statement data", ticker)
        return None

    revenue_ttm = sum(float(q.get("revenue", 0) or 0) for q in statements[:4])
    if revenue_ttm <= 0:
        logger.warning("SKIP %s: revenue_ttm=%s", ticker, revenue_ttm)
        return None

    # --- Fetch historical prices ---
    time.sleep(DELAY_BETWEEN_CALLS)
    if mode == "backfill":
        from_date = backfill_from_str
    else:
        from_date = last_friday_str
    historical = fetch_historical_prices(
        fmp_ticker, bare_ticker, from_date, last_friday_str, logger
    )
    if not historical:
        logger.warning("SKIP %s: no historical price data", ticker)
        return None

    # --- Compute P/S history ---
    new_history = []

    if mode == "backfill":
        # Filter to Fridays, sort oldest first, take up to 52
        fridays = sorted(
            [d for d in historical if is_friday(d["date"])],
            key=lambda d: d["date"],
        )
        fridays = fridays[-52:]  # keep last 52

        for day in fridays:
            price = float(day.get("close", 0) or 0)
            if price <= 0:
                continue
            ps_val = round((price * shares) / revenue_ttm, 2)
            if ps_val <= 0:
                continue
            new_history.append([day["date"], ps_val])

    else:
        # Update: parse existing history, append new Friday entry
        try:
            existing_history = json.loads(existing.get("ps_history_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            existing_history = []

        # Find the Friday entry
        friday_entry = None
        for d in historical:
            if d["date"] == last_friday_str:
                friday_entry = d
                break
        if not friday_entry and historical:
            friday_entry = historical[0]  # fallback to most recent

        if not friday_entry:
            logger.warning("SKIP %s: no Friday price found", ticker)
            return None

        price = float(friday_entry.get("close", 0) or 0)
        if price <= 0:
            logger.warning("SKIP %s: invalid price %s", ticker, price)
            return None

        ps_val = round((price * shares) / revenue_ttm, 2)
        if ps_val <= 0:
            logger.warning("SKIP %s: invalid ps_val %s", ticker, ps_val)
            return None

        existing_history.append([last_friday_str, ps_val])
        # Keep rolling 52-entry window
        if len(existing_history) > 52:
            existing_history = existing_history[-52:]
        new_history = existing_history

    if not new_history:
        logger.warning("SKIP %s: empty history after processing", ticker)
        return None

    # --- Compute stats ---
    values = [h[1] for h in new_history]
    ps_current = values[-1]
    ps_52w_high = round(max(values), 2)
    ps_52w_low = round(min(values), 2)
    ps_12m_median = round(statistics.median(values), 2)

    # ATH: sticky — never goes down
    prev_ath = float(existing.get("ps_ath", 0)) if existing else 0
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
    logger.info("Price-Sales updater starting")
    logger.info("=" * 60)

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
