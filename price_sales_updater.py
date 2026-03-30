#!/usr/bin/env python3
"""
Price-Sales Updater.

Reads tickers from the 'AI Analysis' sheet, fetches market cap and revenue data
from EODHD, computes Price-to-Sales ratios, and writes the results directly back
to the price-sales columns in the 'AI Analysis' sheet.

For backfill (new tickers), fetches 52 weeks of weekly closing prices and
combines with revenue TTM to build historical P/S.  For weekly updates, fetches
the current fundamentals snapshot for a single new data point.

Runs daily at 06:00 UTC via GitHub Actions.
"""

import argparse
import json
import logging
import os
import ssl
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
SHEET_NAME = "AI Analysis"
LOGS_SHEET = "Logs"

EODHD_BASE_URL = "https://eodhd.com/api"
EODHD_API_KEY = os.environ.get("EODHD_API_KEY", "")

DELAY_BETWEEN_CALLS = 0.5  # seconds between EODHD API calls
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Price-sales columns written to AI Analysis (result dict key → sheet header)
PS_SYNC_COLUMNS = {
    "ps_now":       "ps_now",
    "52w_high":     "52w_high",
    "52w_low":      "52w_low",
    "12m_median":   "12m_median",
    "ath":          "ath",
    "%_of_ath":     "%_of_ath",
    "history_json": "history_json",
    "last_updated": "price_data",
}

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


def sheets_execute(request, max_retries=4):
    """Execute a Google Sheets API request with retry on transient errors."""
    from googleapiclient.errors import HttpError

    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status == 429 and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logging.getLogger("price_sales_updater").warning(
                    "Sheets API rate limited (attempt %d/%d), retrying in %ds",
                    attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue
            raise
        except (ssl.SSLEOFError, ConnectionError, OSError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            logging.getLogger("price_sales_updater").warning(
                "Sheets API transient error (attempt %d/%d): %s, retrying in %ds",
                attempt + 1, max_retries, e, wait,
            )
            time.sleep(wait)


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
    """Convert a 0-based column index to a spreadsheet column letter (A, B, ..., Z, AA, ...)."""
    letters = ""
    while True:
        letters = chr(65 + idx % 26) + letters
        idx = idx // 26 - 1
        if idx < 0:
            break
    return letters


def _read_ai_analysis_headers(service, logger) -> tuple[list[str], dict[str, int]]:
    """Read AI Analysis row 2 headers, return (headers_list, {normalised_name: col_index})."""
    result = sheets_execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A2:AZ2",
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    headers = result.get("values", [[]])[0]

    def normalize(h):
        return h.strip().lower().replace(" ", "_")

    header_map = {normalize(h): i for i, h in enumerate(headers)}
    return headers, header_map


def read_ticker_list(service, logger) -> list[dict]:
    """Read ticker + exchange from AI Analysis sheet, starting at row 3."""
    result = sheets_execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A3:B",
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    rows = result.get("values", [])
    tickers = []
    for row in rows:
        if len(row) >= 2 and row[0].strip():
            tickers.append({"ticker": row[0].strip(), "exchange": row[1].strip()})
        elif len(row) >= 1 and row[0].strip():
            tickers.append({"ticker": row[0].strip(), "exchange": ""})
    logger.info("Read %d tickers from %s", len(tickers), SHEET_NAME)
    return tickers


def read_existing_ps_data(service, header_map, logger) -> dict[str, dict]:
    """Read existing price-sales data from AI Analysis columns, keyed by ticker.

    Reads the PS-related columns (ps_now, 52w_high, etc.) plus the price_data
    date column directly from AI Analysis, so no separate sheet is needed.
    """
    # Find column indices for the PS fields we need to read back
    read_keys = {
        "ps_now": "ps_now", "52w_high": "52w_high", "52w_low": "52w_low",
        "12m_median": "12m_median", "ath": "ath", "%_of_ath": "%_of_ath",
        "history_json": "history_json", "last_updated": "price_data",
    }
    col_indices = {}
    for key, header_name in read_keys.items():
        norm = header_name.strip().lower().replace(" ", "_")
        idx = header_map.get(norm)
        if idx is not None:
            col_indices[key] = idx

    if not col_indices:
        logger.warning("No price-sales columns found in AI Analysis headers")
        return {}

    # Read all data rows (row 3 onward) up to the last PS column
    max_col = max(col_indices.values())
    end_col = _col_letter(max_col)
    result = sheets_execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A3:{end_col}",
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    rows = result.get("values", [])

    ps_map = {}
    for row in rows:
        if not row or not row[0].strip():
            continue
        ticker = row[0].strip()
        entry = {}
        for key, col_idx in col_indices.items():
            entry[key] = row[col_idx] if col_idx < len(row) else ""
        ps_map[ticker] = entry

    logger.info("Read existing price-sales data for %d tickers from %s",
                len(ps_map), SHEET_NAME)
    return ps_map


def ensure_logs_sheet_exists(service, logger):
    """Create the Logs sheet tab if it doesn't exist, with header row."""
    meta = sheets_execute(
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties")
    )
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == LOGS_SHEET:
            return

    sheets_execute(service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {"addSheet": {"properties": {"title": LOGS_SHEET}}}
            ]
        },
    ))

    sheets_execute(service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{LOGS_SHEET}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [LOG_COLUMNS]},
    ))
    logger.info("Created sheet '%s' with header row", LOGS_SHEET)


def append_rows(service, sheet_name: str, rows: list[list], logger):
    """Append rows to a sheet."""
    if not rows:
        return
    sheets_execute(service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ))
    logger.info("Appended %d rows to %s", len(rows), sheet_name)


def write_ps_to_ai_analysis(service, all_results: list[dict], header_map, logger):
    """Write price-sales results directly to AI Analysis columns.

    Uses PS_SYNC_COLUMNS to map result dict keys to the correct sheet columns.
    """
    if not all_results:
        return

    # Resolve each sync column to its sheet column index
    col_indices = {}
    for result_key, header_name in PS_SYNC_COLUMNS.items():
        norm = header_name.strip().lower().replace(" ", "_")
        idx = header_map.get(norm)
        if idx is not None:
            col_indices[result_key] = idx
        else:
            logger.warning("Column '%s' not found in AI Analysis headers", header_name)

    if not col_indices:
        logger.warning("No price-sales columns found in AI Analysis — skipping write")
        return

    # Read ticker column (A) starting at data row 3 to build ticker→row map
    result = sheets_execute(
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A3:A",
            valueRenderOption="FORMATTED_VALUE",
        )
    )
    ticker_rows = result.get("values", [])
    ticker_to_row = {}
    for i, row in enumerate(ticker_rows):
        if row and row[0].strip():
            ticker_to_row[row[0].strip()] = i + 3  # data starts at row 3

    # Build batch update — one cell per column per ticker
    batch_data = []
    written = 0
    for r in all_results:
        ticker = r.get("ticker", "")
        row_num = ticker_to_row.get(ticker)
        if not row_num:
            continue

        for result_key, col_idx in col_indices.items():
            value = r.get(result_key, "")
            if value == "" or value is None:
                continue
            batch_data.append({
                "range": f"'{SHEET_NAME}'!{_col_letter(col_idx)}{row_num}",
                "values": [[value]],
            })
        written += 1

    if batch_data:
        sheets_execute(service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": batch_data},
        ))
        logger.info("Wrote price-sales data for %d tickers (%d cells) to %s",
                     written, len(batch_data), SHEET_NAME)
    else:
        logger.info("No price-sales data to write to %s", SHEET_NAME)



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


def _normalize_date(val: str) -> str:
    """Normalize a date string to ISO format (YYYY-MM-DD).

    Google Sheets FORMATTED_VALUE returns dates as M/D/YYYY (e.g. '3/23/2026'),
    but the code writes and compares ISO dates ('2026-03-23').
    """
    if not val:
        return ""
    # Already ISO
    if len(val) == 10 and val[4] == "-":
        return val
    # Try M/D/YYYY (Sheets display format)
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(val, fmt).date().isoformat()
        except ValueError:
            continue
    return val


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


# Yahoo Finance ticker suffix mapping (for historical prices)
YAHOO_SUFFIX = {
    "US": "", "NSE": ".NS", "BSE": ".BO",
    "XETRA": ".DE", "F": ".F", "MU": ".MU", "STU": ".SG",
    "BE": ".BE", "DU": ".DU", "HM": ".HM", "HA": ".HA",
    "PA": ".PA", "AS": ".AS", "SW": ".SW", "MI": ".MI", "MC": ".MC",
    "VI": ".VI",
    "LSE": ".L", "HK": ".HK", "KO": ".KS", "TW": ".TW",
    "SG": ".SI", "AU": ".AX", "NZ": ".NZ", "KL": ".KL", "AE": ".AE",
    "TO": ".TO", "V": ".V", "SA": ".SA", "MX": ".MX",
    "TSE": ".T", "JSE": ".JO", "SR": ".SR",
    "NSENG": ".LG",
}


def _yahoo_chart_request(yahoo_ticker: str, logger) -> list | None:
    """Make a single Yahoo Finance chart request with retry on SSL/connection errors."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}"
    params = {"range": "1y", "interval": "1wk"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            timestamps = result[0].get("timestamp", [])
            adj_closes = (
                result[0].get("indicators", {})
                .get("adjclose", [{}])[0]
                .get("adjclose", [])
            )
            if not timestamps or not adj_closes:
                return None

            prices = []
            for ts, close in zip(timestamps, adj_closes):
                if close is None:
                    continue
                dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                prices.append({"date": dt, "close": close})
            return prices if prices else None

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            wait = 2 ** (attempt + 1)
            logger.warning("Yahoo %s attempt %d/%d failed (%s), retrying in %ds",
                           yahoo_ticker, attempt + 1, max_retries, type(e).__name__, wait)
            time.sleep(wait)
        except Exception as e:
            logger.warning("Yahoo Finance failed for %s: %s", yahoo_ticker, e)
            return None

    return None


def fetch_weekly_prices(ticker: str, exchange: str, logger) -> list | None:
    """Fetch ~52 weeks of weekly closing prices from Yahoo Finance.

    Returns list of {"date": "YYYY-MM-DD", "close": float} dicts, or None.
    """
    eodhd_exchange = _resolve_exchange(exchange)
    suffix = YAHOO_SUFFIX.get(eodhd_exchange, "")
    yahoo_ticker = ticker + suffix

    time.sleep(DELAY_BETWEEN_CALLS)  # rate limit
    prices = _yahoo_chart_request(yahoo_ticker, logger)
    if prices:
        logger.info("Yahoo Finance: got %d weekly prices for %s", len(prices), yahoo_ticker)
        return prices

    # Fallback: try bare ticker (for US-listed ADRs)
    if suffix:
        time.sleep(DELAY_BETWEEN_CALLS)
        prices = _yahoo_chart_request(ticker, logger)
        if prices:
            logger.info("Yahoo Finance: got %d weekly prices for %s (bare fallback)",
                        len(prices), ticker)
            return prices

    logger.warning("Yahoo Finance: no prices for %s (tried %s%s)",
                   ticker, yahoo_ticker, f" and {ticker}" if suffix else "")
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
    History entries are only added on Fridays (weekly granularity),
    but ps_now and last_updated refresh daily.
    """
    today = date.today()
    today_str = today.isoformat()
    last_friday_str = last_friday.isoformat()
    is_friday_run = today.weekday() == 4  # Friday = 4
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

    # Current P/S from fundamentals
    if market_cap and market_cap > 0:
        ps_current = round(market_cap / revenue_ttm, 2)
    else:
        logger.warning("SKIP %s: no market cap data", ticker)
        return None

    # --- Build history ---
    new_history = []

    if mode == "backfill":
        # Fetch weekly prices for backfill via Yahoo Finance
        weekly_prices = fetch_weekly_prices(ticker, exchange, logger)

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
        # Update: parse existing history
        try:
            existing_history = json.loads(existing.get("history_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            existing_history = []

        # Only add a new history entry on Fridays (weekly granularity)
        if is_friday_run:
            existing_history.append([today_str, ps_current])
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
        prev_ath = _safe_float(existing.get("ath")) or 0
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
        "company_name": company_name,
        "revenue_ttm": round(revenue_ttm, 0),
        "ps_now": ps_current,
        "52w_high": ps_52w_high,
        "52w_low": ps_52w_low,
        "12m_median": ps_12m_median,
        "ath": ps_ath,
        "%_of_ath": pct_of_ath,
        "history_json": json.dumps(new_history),
        "last_updated": today_str,
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

    # Ensure logs sheet exists
    ensure_logs_sheet_exists(service, logger)

    # Read AI Analysis headers and existing price-sales data
    _headers, header_map = _read_ai_analysis_headers(service, logger)
    ticker_list = read_ticker_list(service, logger)
    ps_map = read_existing_ps_data(service, header_map, logger)

    # Filter to specific tickers if requested
    if args.tickers:
        filter_set = set(t.upper() for t in args.tickers)
        ticker_list = [t for t in ticker_list if t["ticker"].upper() in filter_set]
        logger.info("Filtered to %d tickers: %s", len(ticker_list), args.tickers)

    # Compute dates
    today = date.today()
    today_str = today.isoformat()
    last_friday = get_last_friday()
    backfill_from = get_backfill_from()
    logger.info("Today: %s | Last Friday: %s | Backfill from: %s",
                today, last_friday, backfill_from)

    # Classify and process tickers
    new_rows = []
    update_rows = []
    skipped = 0
    errors = 0

    for item in ticker_list:
        ticker = item["ticker"]
        exchange = item["exchange"]
        existing = ps_map.get(ticker)

        # Classify — run daily; skip only if already updated today
        if existing is None:
            mode = "backfill"
        elif args.force:
            mode = "update"
        elif not existing.get("last_updated") or _normalize_date(existing["last_updated"]) < today_str:
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

    # Write results directly to AI Analysis
    all_processed = new_rows + update_rows
    logger.info("Writing results: %d new, %d updates, %d skipped, %d errors",
                len(new_rows), len(update_rows), skipped, errors)
    write_ps_to_ai_analysis(service, all_processed, header_map, logger)

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
