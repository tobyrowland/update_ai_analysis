#!/usr/bin/env python3
"""
EODHD Financial Data Updater for EJ2N Spreadsheet.

Reads tickers from the 'AI Analysis' sheet, fetches fundamental financial data
from the EODHD API, calculates key metrics (including r40_score and
fundamentals_snapshot), and writes results back to the spreadsheet.
"""

import argparse
import json
import logging
import os
import statistics as _stats
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dateutil import parser as dateparser
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SHEET_NAME = "AI Analysis"
CRITERIA_SHEET = "Criteria"
EODHD_BASE_URL = "https://eodhd.com/api/fundamentals"
DELAY_BETWEEN_CALLS = 1  # seconds between EODHD API calls
BATCH_SIZE = 5
STALENESS_DAYS = 7  # re-fetch if data older than this

NULL_VALUE = "—"  # consistent placeholder for missing/unavailable data

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# Sheet layout — column groups, display names, widths
# ---------------------------------------------------------------------------

# Each group: (group_name, header_bg_hex, [column_keys...])
GROUPS = [
    ("COMPANY", "1B3A4B", [
        "ticker", "exchange", "company_name", "description",
    ]),
    ("OVERVIEW", "2E4057", [
        "r40_score", "fundamentals_snapshot", "short_outlook",
    ]),
    ("REVENUE", "1B3A4B", [
        "annual_revenue_5y", "quarterly_revenue",
        "rev_growth_ttm", "rev_growth_qoq", "rev_cagr", "rev_consistency_score",
    ]),
    ("MARGINS", "1B3A4B", [
        "gross_margin", "gm_trend",
        "operating_margin", "net_margin", "net_margin_yoy",
        "fcf_margin",
    ]),
    ("EFFICIENCY", "1B3A4B", [
        "opex_pct_revenue", "sm_rd_pct_revenue",
        "rule_of_40", "qrtrs_to_profitability",
    ]),
    ("EARNINGS", "1B3A4B", [
        "eps_only", "eps_yoy",
    ]),
    ("DATA QUALITY", "5B3A1B", [
        "one_time_events",
    ]),
    ("AI NARRATIVE", "2E4057", [
        "full_outlook", "key_risks",
    ]),
    ("LAST ANALYSIS", "1B3A4B", [
        "ai", "data",
    ]),
]

# Maps internal column keys to the header text shown in the sheet.
# The sheet now uses lowercase underscore names as headers.
DISPLAY_NAMES = {
    "ticker":               "ticker",
    "exchange":             "exchange",
    "company_name":         "company_name",
    "description":          "description",
    "annual_revenue_5y":    "annual_revenue_5y",
    "quarterly_revenue":    "quarterly_revenue",
    "rev_growth_ttm":       "rev_growth_ttm%",
    "rev_growth_qoq":       "rev_growth_qoq%",
    "rev_cagr":             "rev_cagr%",
    "rev_consistency_score": "rev_consistency_score",
    "gross_margin":         "gross_margin%",
    "gm_trend":             "gm_trend%",
    "operating_margin":     "operating_margin%",
    "net_margin":           "net_margin%",
    "net_margin_yoy":       "net_margin_yoy%",
    "fcf_margin":           "fcf_margin%",
    "opex_pct_revenue":     "opex_%_of_revenue",
    "sm_rd_pct_revenue":    "s&m+r&d_%_of_revenue",
    "rule_of_40":           "rule_of_40",
    "qrtrs_to_profitability": "qrtrs_to_profitability",
    "eps_only":             "eps_only",
    "eps_yoy":              "eps_yoy%",
    "r40_score":            "r40_score",
    "fundamentals_snapshot": "fundamentals_snapshot",
    "short_outlook":        "short_outlook",
    "full_outlook":         "full_outlook",
    "key_risks":            "key_risks",
    "one_time_events":      "one_time_events",
    "ai":                   "ai",
    "data":                 "data",
}

# Map legacy/alternative sheet headers → current header names.
HEADER_ALIASES = {
    "Company":              "company_name",
    "Company Name":         "company_name",
    "Ticker":               "ticker",
    "Exchange":             "exchange",
    "Description":          "description",
    "Annual Revenue (5Y)":  "annual_revenue_5y",
    "Quarterly Revenue":    "quarterly_revenue",
    "Rev Growth TTM %":     "rev_growth_ttm",
    "Rev Growth QoQ %":     "rev_growth_qoq",
    "Rev CAGR 3Y %":       "rev_cagr",
    "Rev Consistency Score": "rev_consistency_score",
    "Gross Margin %":       "gross_margin",
    "GM Trend (Qtly)":      "gm_trend",
    "Operating Margin %":   "operating_margin",
    "Net Margin %":         "net_margin",
    "Net Margin YoY Δ":     "net_margin_yoy",
    "FCF Margin %":         "fcf_margin",
    "Opex % of Revenue":    "opex_pct_revenue",
    "S&M+R&D % of Revenue": "sm_rd_pct_revenue",
    "Rule of 40":           "rule_of_40",
    "Qtrs to Profitability": "qrtrs_to_profitability",
    "EPS Qtrly":            "eps_only",
    "EPS YoY %":            "eps_yoy",
    "R40 Score":            "r40_score",
    "Fundamentals Snapshot": "fundamentals_snapshot",
    "Short Outlook":        "short_outlook",
    "Outlook":              "full_outlook",
    "Full Outlook":         "full_outlook",
    "Key Risks":            "key_risks",
    "AI":                   "ai",
    "Analyzed":             "ai",
    "AI Analyzed":          "ai",
    "Data":                 "data",
    "One-Time Events":      "one_time_events",
    "One Time Events":      "one_time_events",
    "Data As Of":           "data",
    "Fundamentals Date":    "data",
}

COL_WIDTHS = {
    "ticker": 10,
    "exchange": 12,
    "company_name": 25,
    "description": 40,
    "annual_revenue_5y": 45,
    "quarterly_revenue": 70,
    "rev_growth_ttm": 18,
    "rev_growth_qoq": 18,
    "rev_cagr": 18,
    "rev_consistency_score": 18,
    "gross_margin": 18,
    "gm_trend": 28,
    "operating_margin": 18,
    "net_margin": 18,
    "net_margin_yoy": 18,
    "fcf_margin": 18,
    "opex_pct_revenue": 18,
    "sm_rd_pct_revenue": 22,
    "rule_of_40": 14,
    "qrtrs_to_profitability": 18,
    "eps_only": 14,
    "eps_yoy": 14,
    "r40_score": 22,
    "fundamentals_snapshot": 55,
    "short_outlook": 50,
    "full_outlook": 80,
    "key_risks": 50,
    "one_time_events": 50,
    "ai": 16,
    "data": 16,
}

# Columns that should be formatted as percentages
PCT_COLS = {
    "rev_growth_ttm", "rev_growth_qoq", "rev_cagr",
    "gross_margin", "operating_margin", "net_margin",
    "net_margin_yoy", "fcf_margin",
    "opex_pct_revenue", "sm_rd_pct_revenue", "eps_yoy",
}

# Columns that should be formatted as decimals
DECIMAL_COLS = set()  # no decimal-formatted columns currently

# Columns formatted as plain integers (no decimal, no % sign)
INTEGER_COLS = {"rule_of_40"}

# Columns that should be formatted as dollars
DOLLAR_COLS = {"eps_only"}

# Columns populated by EODHD (as opposed to AI narrative columns)
EODHD_COLUMNS = [
    "annual_revenue_5y", "quarterly_revenue",
    "rev_growth_ttm", "rev_growth_qoq", "rev_cagr", "rev_consistency_score",
    "gross_margin", "gm_trend",
    "operating_margin", "net_margin", "net_margin_yoy",
    "fcf_margin",
    "opex_pct_revenue", "sm_rd_pct_revenue",
    "rule_of_40", "qrtrs_to_profitability",
    "eps_only", "eps_yoy",
    "one_time_events",
    "r40_score", "fundamentals_snapshot", "data",
]

# ---------------------------------------------------------------------------
# Flat column list derived from GROUPS
# ---------------------------------------------------------------------------

ALL_COLUMNS = []
for _, _, cols in GROUPS:
    ALL_COLUMNS.extend(cols)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"eodhd_update_{today_str}.txt"

    logger = logging.getLogger("eodhd_updater")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

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


def read_all_rows(service) -> list[list[str]]:
    """Return all rows from the AI Analysis sheet."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


def _get_sheet_id(service) -> int:
    """Return the numeric sheet ID for SHEET_NAME."""
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == SHEET_NAME:
            return sheet["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{SHEET_NAME}' not found")


def clear_data_row_formatting(service, logger: logging.Logger):
    """Reset background color and text color on all data rows (row 3+)."""
    sheet_id = _get_sheet_id(service)
    num_cols = len(ALL_COLUMNS)
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,  # row 3 (0-indexed)
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                        "textFormat": {
                            "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                            "bold": False,
                        },
                    }
                },
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat",
            }
        }]},
    ).execute()
    logger.info("Reset formatting on data rows (row 3+)")


def ensure_sheet_columns(service, needed: int, logger):
    """Expand sheet column count if it currently has fewer than `needed` columns."""
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet["properties"]
        if props["title"] == SHEET_NAME:
            current = props["gridProperties"]["columnCount"]
            if current >= needed:
                return
            logger.info("Expanding sheet from %d to %d columns", current, needed)
            service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": props["sheetId"],
                            "gridProperties": {"columnCount": needed},
                        },
                        "fields": "gridProperties.columnCount",
                    }
                }]},
            ).execute()
            return


def write_row_updates(service, updates: list[dict], logger=None):
    """Batch-write updates. Each entry: {"row": 1-indexed, "values": {col_letter: value}}."""
    if not updates:
        return

    data = []
    max_col_idx = 0
    for upd in updates:
        row = upd["row"]
        for col_letter, value in upd["values"].items():
            # Track the highest column index referenced
            idx = 0
            for ch in col_letter:
                idx = idx * 26 + (ord(ch) - ord('A') + 1)
            if idx > max_col_idx:
                max_col_idx = idx
            data.append({
                "range": f"'{SHEET_NAME}'!{col_letter}{row}",
                "values": [[value]],
            })

    # Ensure the sheet has enough columns before writing
    ensure_sheet_columns(service, max_col_idx, logger or logging.getLogger(__name__))

    body = {"valueInputOption": "USER_ENTERED", "data": data}
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body=body
    ).execute()


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s). 0→A, 25→Z, 26→AA."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def _col_index(key: str) -> int:
    """Return the 0-based column index for a column key."""
    return ALL_COLUMNS.index(key)


def _col_letter_for_key(key: str) -> str:
    """Return the Excel column letter for a column key."""
    return _col_letter(_col_index(key))


# ---------------------------------------------------------------------------
# Sheet writing — headers, formatting, data
# ---------------------------------------------------------------------------


def write_ai_analysis_sheet(service, rows: list[dict], logger: logging.Logger):
    """Write the full AI Analysis sheet: group headers, column headers, data rows.

    `rows` is a list of dicts keyed by column keys from ALL_COLUMNS.
    """
    sheet_data = []

    # Row 1: Group headers
    group_header = []
    for group_name, _, cols in GROUPS:
        group_header.append(group_name)
        group_header.extend([""] * (len(cols) - 1))
    sheet_data.append(group_header)

    # Row 2: Column headers (display names)
    col_header = [DISPLAY_NAMES.get(c, c) for c in ALL_COLUMNS]
    sheet_data.append(col_header)

    # Data rows
    for row in rows:
        data_row = []
        for col_key in ALL_COLUMNS:
            val = row.get(col_key, "")
            if val is None:
                val = ""
            # Format percentage columns
            if col_key in PCT_COLS and isinstance(val, (int, float)):
                val = f"{val:.1f}%"
            elif col_key in INTEGER_COLS and isinstance(val, (int, float)):
                val = f"{val:.0f}"
            elif col_key in DOLLAR_COLS and isinstance(val, (int, float)):
                val = f"${val:.2f}"
            data_row.append(val)
        sheet_data.append(data_row)

    # Write all at once
    end_col = _col_letter(len(ALL_COLUMNS) - 1)
    end_row = len(sheet_data)
    range_str = f"'{SHEET_NAME}'!A1:{end_col}{end_row}"

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body={"values": sheet_data},
    ).execute()

    logger.info("Wrote %d rows (incl. 2 header rows) to %s", len(sheet_data), range_str)

    # Ensure data rows have no background color (only header rows should be colored)
    clear_data_row_formatting(service, logger)


# ---------------------------------------------------------------------------
# EODHD API
# ---------------------------------------------------------------------------

# Map common exchange names (as they appear in the spreadsheet) to the
# suffix codes that EODHD expects.  Keys are compared case-insensitively.
EXCHANGE_TO_EODHD = {
    # United States
    "NASDAQ":   "US",
    "NYSE":     "US",
    "NYSEARCA": "US",
    "NYSEMKT":  "US",
    "AMEX":     "US",
    "OTC":      "US",
    "BATS":     "US",
    "US":       "US",
    # United Kingdom
    "LSE":      "LSE",
    "LON":      "LSE",
    "LONDON":   "LSE",
    # India
    "NSE":      "NSE",
    "BSE":      "BSE",
    "NSEI":     "NSE",
    # Japan
    "TSE":      "TSE",
    "TYO":      "TSE",
    "JPX":      "TSE",
    # Germany
    "XETRA":    "XETRA",
    "FRA":      "F",
    "ETR":      "XETRA",
    "GETTEX":   "MU",
    "MU":       "MU",
    "STU":      "STU",
    "BE":       "BE",
    "DU":       "DU",
    "HM":       "HM",
    "HA":       "HA",
    # Other Europe
    "EPA":      "PA",
    "PAR":      "PA",
    "AMS":      "AS",
    "EURONEXT": "AS",      # Generic Euronext → Amsterdam (fallback tries PA)
    "SWX":      "SW",
    "BIT":      "MI",
    "BME":      "MC",
    "VIE":      "VIE",     # Vienna Stock Exchange
    "WBAG":     "VIE",
    "OMXCOP":   "CO",      # Copenhagen (Nasdaq Nordic)
    "CPH":      "CO",
    "OMXSTO":   "ST",      # Stockholm (Nasdaq Nordic)
    "STO":      "ST",
    "OMXHEX":   "HE",      # Helsinki (Nasdaq Nordic)
    "OMXICE":   "IC",      # Iceland (Nasdaq Nordic)
    "OMXTAL":   "TL",      # Tallinn
    "OMXVIL":   "VS",      # Vilnius
    "OMXRIG":   "RG",      # Riga
    # Germany — Lang & Schwarz / Tradegate / misc
    "LS":       "F",       # Lang & Schwarz → Frankfurt (EODHD has no LS)
    "LSX":      "F",       # Lang & Schwarz Exchange
    "LSIN":     "F",       # Lang & Schwarz Indicationen
    "TRADEGATE":"F",       # Tradegate → Frankfurt
    # Asia-Pacific
    "HKG":      "HK",
    "HKEX":     "HK",
    "KRX":      "KO",
    "KOSDAQ":   "KO",
    "TWSE":     "TW",
    "TPE":      "TW",
    "SGX":      "SG",
    "ASX":      "AU",
    "NZX":      "NZ",
    # Malaysia
    "MYX":      "KLSE",
    "KLSE":     "KLSE",
    # Vietnam
    "HOSE":     "VN",
    "HNX":      "VN",
    # Americas
    "TSX":      "TO",
    "TSXV":     "V",
    "SAO":      "SA",
    "BVMF":     "SA",
    "BMV":      "MX",      # Mexico (Bolsa Mexicana de Valores)
    "MEX":      "MX",
    # Africa / Middle East
    "JSE":      "JSE",
    "TADAWUL":  "SR",
    "SAU":      "SR",
    "DFM":      "DFM",     # Dubai Financial Market
    "ADX":      "ADX",     # Abu Dhabi Securities Exchange
    "NSENG":    "NSENG",   # Nigerian Stock Exchange (via EODHD code)
    "NGS":      "NSENG",
    "NGSE":     "NSENG",
}


def _resolve_exchange(exchange: str) -> str:
    """Convert a spreadsheet exchange name to the EODHD suffix code."""
    key = exchange.strip().upper()
    return EXCHANGE_TO_EODHD.get(key, key)


# Fallback exchange chains — when the primary exchange returns 404,
# try these alternatives in order.  The idea is to cover cases where
# the sheet lists a regional exchange but EODHD only has the ticker on
# a major exchange (or vice-versa).
EXCHANGE_FALLBACKS = {
    # German regional exchanges → try XETRA, Frankfurt, then US
    "MU":    ["XETRA", "F", "US"],
    "STU":   ["XETRA", "F", "US"],
    "BE":    ["XETRA", "F", "US"],
    "DU":    ["XETRA", "F", "US"],
    "HM":    ["XETRA", "F", "US"],
    "HA":    ["XETRA", "F", "US"],
    "XETRA": ["F", "US"],
    "F":     ["XETRA", "US"],
    # India — BSE symbols are often numeric; try NSE which uses names
    "BSE":   ["NSE", "US"],
    "NSE":   ["BSE", "US"],
    # Japan — many TSE tickers have no US ADR, so search is key
    "TSE":   ["US"],
    # Switzerland
    "SW":    ["US"],
    # Netherlands / Euronext
    "AS":    ["US", "PA"],
    # Other Europe
    "PA":    ["US", "AS"],
    "MI":    ["US"],
    "MC":    ["US"],
    # UK
    "LSE":   ["US"],
    # Canada
    "TO":    ["V", "US"],
    "V":     ["TO", "US"],
    # Hong Kong
    "HK":    ["US"],
    # Korea
    "KO":    ["US"],
    # Australia
    "AU":    ["US"],
    # Nordics
    "CO":    ["XETRA", "F", "US"],  # Copenhagen
    "ST":    ["XETRA", "F", "US"],  # Stockholm
    "HE":    ["XETRA", "F", "US"],  # Helsinki
    "IC":    ["US"],                  # Iceland
    # Vienna
    "VIE":   ["XETRA", "F", "SW", "US"],
    # Malaysia
    "KLSE":  ["SG", "US"],
    # Vietnam
    "VN":    ["US"],
    # Mexico
    "MX":    ["US"],
    # Middle East
    "DFM":   ["US"],  # Dubai
    "ADX":   ["US"],  # Abu Dhabi
    # Nigeria
    "NSENG": ["LSE", "US"],
}

# Always append US as a last-ditch fallback (many international companies
# have US-listed ADRs or cross-listings that carry fundamentals data).
for _exc, _chain in EXCHANGE_FALLBACKS.items():
    if "US" not in _chain:
        _chain.append("US")


def _has_financials(data: dict) -> bool:
    """Check whether an EODHD response contains usable financial data.

    Some tickers return 200 OK but have empty Financials / Earnings
    sections, which produces all-None metrics.  We consider the data
    usable if there is at least one yearly or quarterly income statement
    entry, or at least one earnings history entry.
    """
    financials = data.get("Financials") or {}
    income = financials.get("Income_Statement") or {}
    yearly = income.get("yearly") or {}
    quarterly = income.get("quarterly") or {}
    earnings = (data.get("Earnings") or {}).get("History") or {}
    return bool(yearly or quarterly or earnings)


def _try_fetch_one(ticker: str, exchange: str, api_key: str,
                   timeout: int = 30) -> tuple[dict | None, int]:
    """Attempt a single EODHD fundamentals fetch.

    Returns (json_data, http_status).  json_data is None on any failure
    or if the response lacks usable financial data.
    http_status is 0 for non-HTTP errors, -1 if fetched OK but empty.
    """
    symbol = f"{ticker}.{exchange}"
    url = f"{EODHD_BASE_URL}/{symbol}"
    params = {"api_token": api_key, "fmt": "json"}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if not _has_financials(data):
            return None, -1  # 200 OK but no usable data
        return data, resp.status_code
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        return None, status
    except Exception:
        return None, 0


def _search_eodhd(query: str, api_key: str, logger: logging.Logger,
                  original_ticker: str = "") -> tuple[str, str] | None:
    """Use the EODHD search API to find the best (ticker, exchange) for a query.

    *query* can be a ticker code or a company name.
    Returns (ticker_code, exchange_code) or None.
    """
    url = "https://eodhd.com/api/search/" + query
    params = {"api_token": api_key, "fmt": "json", "limit": 10}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        # Prefer results whose Code matches the original ticker exactly
        check_code = original_ticker.upper() or query.upper()
        for r in results:
            if r.get("Code", "").upper() == check_code:
                ex = r.get("Exchange", "")
                if ex:
                    logger.info("Search API exact match %s → %s.%s",
                                query, r["Code"], ex)
                    return (r["Code"], ex)
        # Otherwise take the first result that has fundamentals-friendly
        # exchange (prefer US, then major exchanges)
        preferred = ["US", "LSE", "NSE", "BSE", "TSE", "XETRA", "TO",
                     "AS", "PA", "SW", "HK", "F", "MI", "MC"]
        for pref in preferred:
            for r in results:
                if r.get("Exchange", "") == pref:
                    logger.info("Search API preferred match %s → %s.%s",
                                query, r["Code"], r["Exchange"])
                    return (r["Code"], r["Exchange"])
        # Fall back to first result
        r = results[0]
        ex = r.get("Exchange", "")
        code = r.get("Code", "")
        if ex and code:
            logger.info("Search API best guess for %s → %s.%s", query, code, ex)
            return (code, ex)
    except Exception as exc:
        logger.debug("EODHD search failed for '%s': %s", query, exc)
    return None


def _fetch_fundamentals_raw(ticker: str, api_key: str, logger: logging.Logger,
                            exchange: str = "US",
                            company: str = "") -> dict | None:
    """Fetch full fundamental data from EODHD for a ticker.

    Tries the primary exchange first, then walks through
    EXCHANGE_FALLBACKS, and finally falls back to the EODHD search API
    (searching by both ticker code and company name).
    """
    # Strip share-class suffixes used on some exchanges (e.g. BMV: VISTA/A,
    # ARGX/N; Copenhagen: NSIS_B).  These aren't part of the EODHD ticker.
    clean_ticker = ticker.split("/")[0]       # VISTA/A → VISTA
    clean_ticker = clean_ticker.split("_")[0] # NSIS_B  → NSIS
    if clean_ticker != ticker:
        logger.info("Stripped ticker suffix: %s → %s", ticker, clean_ticker)
    ticker = clean_ticker

    primary = _resolve_exchange(exchange)
    logger.info("Fetching fundamentals for %s (exchange: %s → %s)", ticker, exchange, primary)

    # Retryable status codes: 404 (not found), 0 (network error),
    # -1 (200 OK but no usable financial data)
    RETRYABLE = (404, 0, -1)
    tried = {primary}

    # 1. Try primary exchange
    data, status = _try_fetch_one(ticker, primary, api_key)
    if data is not None:
        return data
    if status == -1:
        logger.info("  %s.%s returned data but no financials — trying fallbacks", ticker, primary)
    if status not in RETRYABLE:
        logger.warning("EODHD HTTP %d for %s.%s, not retrying", status, ticker, primary)
        return None

    # 2. Try fallback exchanges (same ticker code, different exchange)
    fallbacks = EXCHANGE_FALLBACKS.get(primary, [])
    for alt in fallbacks:
        if alt in tried:
            continue
        tried.add(alt)
        logger.info("  Trying fallback %s.%s", ticker, alt)
        time.sleep(0.3)
        data, status = _try_fetch_one(ticker, alt, api_key)
        if data is not None:
            logger.info("  Found %s on %s (fallback from %s)", ticker, alt, primary)
            return data
        if status not in RETRYABLE:
            logger.warning("EODHD HTTP %d for %s.%s, stopping fallback chain",
                           status, ticker, alt)
            return None

    # 3. Search API — try by ticker code first
    logger.info("  All fallbacks exhausted for %s, trying search API", ticker)
    result = _search_eodhd(ticker, api_key, logger, original_ticker=ticker)
    if result:
        search_ticker, search_exchange = result
        if search_exchange not in tried or search_ticker.upper() != ticker.upper():
            time.sleep(0.3)
            data, status = _try_fetch_one(search_ticker, search_exchange, api_key)
            if data is not None:
                logger.info("  Found %s.%s via search (ticker query)",
                            search_ticker, search_exchange)
                return data
            tried.add(search_exchange)

    # 3b. For OTC foreign tickers (ending in F or Y), strip suffix and
    #     search for the underlying ticker — e.g. ADYYF → ADYY → Adyen
    if primary == "US" and len(ticker) > 2 and ticker[-1] in ("F", "Y"):
        base_ticker = ticker[:-1]
        if base_ticker.upper() != ticker.upper():
            logger.info("  Trying OTC base ticker search: %s", base_ticker)
            time.sleep(0.3)
            result = _search_eodhd(base_ticker, api_key, logger, original_ticker=ticker)
            if result:
                search_ticker, search_exchange = result
                if search_exchange not in tried or search_ticker.upper() != ticker.upper():
                    time.sleep(0.3)
                    data, status = _try_fetch_one(search_ticker, search_exchange, api_key)
                    if data is not None:
                        logger.info("  Found %s.%s via OTC base ticker search",
                                    search_ticker, search_exchange)
                        return data
                    tried.add(search_exchange)

    # 4. Search API — try by company name (handles cases like
    #    TSE code 7974 → US ADR "NTDOY" for Nintendo,
    #    GETTEX 49G → ONON.US for On Holding)
    if company:
        # Use first few words to avoid overly specific queries
        search_name = " ".join(company.split()[:3])
        logger.info("  Searching by company name: '%s'", search_name)
        time.sleep(0.3)
        result = _search_eodhd(search_name, api_key, logger, original_ticker=ticker)
        if result:
            search_ticker, search_exchange = result
            if search_exchange not in tried or search_ticker.upper() != ticker.upper():
                time.sleep(0.3)
                data, status = _try_fetch_one(search_ticker, search_exchange, api_key)
                if data is not None:
                    logger.info("  Found %s.%s via company name search '%s'",
                                search_ticker, search_exchange, search_name)
                    return data

    logger.warning("Could not find fundamentals for %s on any exchange "
                   "(tried %s + search)", ticker, tried)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None
    except (ValueError, TypeError):
        return None


def fmt_revenue(value: float | None) -> str:
    if value is None:
        return ""
    v = abs(value)
    sign = "-" if value < 0 else ""
    if v >= 1e9:
        return f"{sign}${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.0f}M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.0f}K"
    return f"{sign}${v:.0f}"


def _sorted_entries(section: dict) -> list[tuple[str, dict]]:
    """Sort financial entries by date descending."""
    if not section:
        return []
    entries = [(k, v) for k, v in section.items() if isinstance(v, dict)]
    entries.sort(key=lambda x: x[0], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Screening criteria — read from "Criteria" sheet
# ---------------------------------------------------------------------------

# Dot characters for screening flags
DOT_RED = "\U0001f534"     # 🔴
DOT_YELLOW = "\U0001f7e1"  # 🟡
DOT_GREEN = "\U0001f7e2"   # 🟢

# Supported operators for criteria rules
_CRITERIA_OPS = {
    "<":  lambda val, thresh: val < thresh,
    "<=": lambda val, thresh: val <= thresh,
    ">":  lambda val, thresh: val > thresh,
    ">=": lambda val, thresh: val >= thresh,
}


def read_criteria(service, logger: logging.Logger) -> list[dict]:
    """Read screening rules from the Criteria sheet.

    Returns a list of rule dicts:
        {"metric": str, "operator": str, "red": float|None,
         "yellow": float|None, "green": float|None, "description": str}
    """
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{CRITERIA_SHEET}'!A1:ZZ",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
        rows = result.get("values", [])
    except Exception as exc:
        logger.warning("Could not read Criteria sheet: %s", exc)
        return []

    if len(rows) < 2:
        logger.info("Criteria sheet is empty or has no data rows")
        return []

    # Find the header row (first row containing "metric" in column A)
    header_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip().lower() == "metric":
            header_idx = i
            break

    if header_idx is None:
        logger.warning("Criteria sheet: could not find header row with 'metric'")
        return []

    headers = [h.strip().lower() for h in rows[header_idx]]

    def col(name):
        try:
            return headers.index(name)
        except ValueError:
            return None

    metric_col = col("metric")
    op_col = col("operator")
    red_col = col("red")
    yellow_col = col("yellow")
    green_col = col("green")
    desc_col = col("description")

    if metric_col is None or op_col is None:
        logger.warning("Criteria sheet missing required 'metric' or 'operator' column")
        return []

    # Build lookup tables for normalising metric names.
    # Users may type display names (fcf_margin%) or legacy headers.
    _display_to_key = {v.lower(): k for k, v in DISPLAY_NAMES.items()}
    _alias_lower = {k.lower(): v for k, v in HEADER_ALIASES.items()}

    rules = []
    for row in rows[header_idx + 1:]:
        if not row or not row[0].strip():
            continue  # skip empty / section header rows
        padded = row + [""] * (len(headers) - len(row))
        raw_metric = padded[metric_col].strip()
        operator = padded[op_col].strip()

        if not raw_metric or operator not in _CRITERIA_OPS:
            continue

        # Normalise metric name to internal key
        metric = raw_metric.lower()
        if metric in _display_to_key:
            metric = _display_to_key[metric]
        elif metric in _alias_lower:
            metric = _alias_lower[metric]

        rules.append({
            "metric": metric,
            "operator": operator,
            "red": safe_float(padded[red_col]) if red_col is not None else None,
            "yellow": safe_float(padded[yellow_col]) if yellow_col is not None else None,
            "green": safe_float(padded[green_col]) if green_col is not None else None,
            "description": padded[desc_col].strip() if desc_col is not None else "",
        })

    logger.info("Loaded %d screening criteria from '%s' sheet", len(rules), CRITERIA_SHEET)
    for r in rules:
        logger.info("  %s %s red=%s yellow=%s green=%s — %s",
                     r["metric"], r["operator"], r["red"], r["yellow"],
                     r["green"], r["description"])
    return rules


def evaluate_criteria(value: float | None, metric: str,
                      criteria: list[dict]) -> str | None:
    """Evaluate a metric value against screening criteria.

    Returns DOT_RED, DOT_YELLOW, DOT_GREEN, or None (no matching rule).
    """
    if value is None:
        return None

    for rule in criteria:
        if rule["metric"] != metric:
            continue

        op = _CRITERIA_OPS[rule["operator"]]

        # Check thresholds in order: red (worst), yellow, green (best)
        if rule["red"] is not None and op(value, rule["red"]):
            return DOT_RED
        if rule["yellow"] is not None and op(value, rule["yellow"]):
            return DOT_YELLOW
        # If green threshold is defined and we pass it, show green dot
        if rule["green"] is not None and op(value, rule["green"]):
            return DOT_GREEN
        # Value exists and a rule was found but didn't trigger any threshold
        return DOT_GREEN

    return None  # no rule for this metric


# ---------------------------------------------------------------------------
# Core: fetch_eodhd_data — calculates all metrics for one ticker
# ---------------------------------------------------------------------------


def fetch_eodhd_data(ticker: str, api_key: str, logger: logging.Logger,
                     exchange: str = "US", company: str = "") -> dict | None:
    """Fetch EODHD fundamentals and compute all financial metrics.

    Returns a dict keyed by EODHD_COLUMNS column keys, or None on failure.
    """
    raw = _fetch_fundamentals_raw(ticker, api_key, logger, exchange=exchange,
                                  company=company)
    if raw is None:
        return None

    financials = raw.get("Financials", {})
    income_yearly = financials.get("Income_Statement", {}).get("yearly", {})
    income_quarterly = financials.get("Income_Statement", {}).get("quarterly", {})
    cashflow_quarterly = financials.get("Cash_Flow", {}).get("quarterly", {})
    earnings = raw.get("Earnings", {})

    yearly = _sorted_entries(income_yearly)
    quarterly = _sorted_entries(income_quarterly)
    cf_quarterly = _sorted_entries(cashflow_quarterly)
    eps_entries = _sorted_entries(earnings.get("History", {}))

    result = {}

    # ── Annual Revenue (5Y) ───────────────────────────────────────────
    annual_revs = []
    for date_str, entry in yearly[:5]:
        rev = safe_float(entry.get("totalRevenue"))
        if rev is not None:
            year = date_str[:4]
            annual_revs.append(f"{year}: {fmt_revenue(rev)}")
    result["annual_revenue_5y"] = " | ".join(annual_revs) if annual_revs else None

    # ── Quarterly Revenue (last 5 quarters) ────────────────────────────
    if quarterly:
        parts = []
        for entry in quarterly[:5]:
            rev = safe_float(entry[1].get("totalRevenue"))
            q_date = entry[0]
            if rev is not None:
                parts.append(f"{fmt_revenue(rev)} ({q_date})")
        result["quarterly_revenue"] = " | ".join(parts) if parts else None
    else:
        result["quarterly_revenue"] = None

    # ── Rev Growth TTM % ──────────────────────────────────────────────
    rev_growth_ttm = None
    if len(quarterly) >= 8:
        recent_4 = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        prior_4 = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[4:8])
        if prior_4 > 0:
            rev_growth_ttm = ((recent_4 - prior_4) / prior_4) * 100
    result["rev_growth_ttm"] = round(rev_growth_ttm, 1) if rev_growth_ttm is not None else None

    # ── Rev Growth QoQ % ──────────────────────────────────────────────
    if len(quarterly) >= 2:
        curr = safe_float(quarterly[0][1].get("totalRevenue"))
        prev = safe_float(quarterly[1][1].get("totalRevenue"))
        if curr is not None and prev and prev > 0:
            result["rev_growth_qoq"] = round(((curr - prev) / prev) * 100, 1)
        else:
            result["rev_growth_qoq"] = None
    else:
        result["rev_growth_qoq"] = None

    # ── Rev CAGR % ─────────────────────────────────────────────────
    if len(yearly) >= 4:
        recent_rev = safe_float(yearly[0][1].get("totalRevenue"))
        base_rev = safe_float(yearly[3][1].get("totalRevenue"))
        if recent_rev and base_rev and base_rev > 0 and recent_rev > 0:
            cagr = ((recent_rev / base_rev) ** (1 / 3) - 1) * 100
            result["rev_cagr"] = round(cagr, 1)
        else:
            result["rev_cagr"] = None
    else:
        result["rev_cagr"] = None

    # ── Rev Consistency Score (0–10) ──────────────────────────────────
    # Always scored out of 10 (need 11 quarters). Show as "X/10".
    if len(quarterly) >= 11:
        growth_count = 0
        for i in range(10):
            c = safe_float(quarterly[i][1].get("totalRevenue"))
            p = safe_float(quarterly[i + 1][1].get("totalRevenue"))
            if c is not None and p is not None and c > p:
                growth_count += 1
        result["rev_consistency_score"] = f"{growth_count}/10"
    elif len(quarterly) >= 2:
        # Fewer than 11 quarters — normalize to /10 scale
        n = len(quarterly) - 1
        growth_count = 0
        for i in range(n):
            c = safe_float(quarterly[i][1].get("totalRevenue"))
            p = safe_float(quarterly[i + 1][1].get("totalRevenue"))
            if c is not None and p is not None and c > p:
                growth_count += 1
        scaled = round(growth_count / n * 10)
        result["rev_consistency_score"] = f"{scaled}/10"
    else:
        result["rev_consistency_score"] = None

    # ── Gross Margin TTM % ────────────────────────────────────────────
    gross_margin_val = None
    if len(quarterly) >= 4:
        gp_sum = 0
        rev_sum = 0
        for e in quarterly[:4]:
            gp = safe_float(e[1].get("grossProfit"))
            rev = safe_float(e[1].get("totalRevenue"))
            # Fallback: derive grossProfit from totalRevenue - costOfRevenue
            if gp is None and rev is not None:
                cor = safe_float(e[1].get("costOfRevenue"))
                if cor is not None:
                    gp = rev - cor
            gp_sum += gp or 0
            rev_sum += rev or 0
        if rev_sum > 0 and gp_sum != 0:
            gross_margin_val = (gp_sum / rev_sum) * 100
    elif quarterly:
        gp = safe_float(quarterly[0][1].get("grossProfit"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if gp is None and rev is not None:
            cor = safe_float(quarterly[0][1].get("costOfRevenue"))
            if cor is not None:
                gp = rev - cor
        if gp is not None and rev and rev > 0:
            gross_margin_val = (gp / rev) * 100
    result["gross_margin"] = round(gross_margin_val, 1) if gross_margin_val is not None else None

    # ── GM Trend (Qtly) ──────────────────────────────────────────────
    # Shows per-quarter gross margins (oldest→newest) plus the overall
    # pp change with a directional arrow, e.g. "45%→47%→49%→52% ↑7pp"
    gm_trend_val = None
    if len(quarterly) >= 4:
        margins = []
        for entry in quarterly[:4]:
            gp = safe_float(entry[1].get("grossProfit"))
            rev = safe_float(entry[1].get("totalRevenue"))
            # Fallback: derive grossProfit from totalRevenue - costOfRevenue
            if gp is None and rev is not None:
                cor = safe_float(entry[1].get("costOfRevenue"))
                if cor is not None:
                    gp = rev - cor
            if gp is not None and rev and rev > 0:
                margins.append((gp / rev) * 100)
        if len(margins) >= 2:
            gm_trend_val = margins[0] - margins[-1]  # pp change newest vs oldest
            arrow = "↑" if gm_trend_val > 1 else ("↓" if gm_trend_val < -1 else "→")
            margin_strs = [f"{m:.0f}%" for m in reversed(margins)]
            pp = f"{abs(gm_trend_val):.0f}pp"
            result["gm_trend"] = f"{'→'.join(margin_strs)} {arrow}{pp}"
        else:
            result["gm_trend"] = None
    else:
        result["gm_trend"] = None

    # ── Operating Margin TTM % ────────────────────────────────────────
    if len(quarterly) >= 4:
        oi_sum = sum(safe_float(e[1].get("operatingIncome")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            result["operating_margin"] = round((oi_sum / rev_sum) * 100, 1)
        else:
            result["operating_margin"] = None
    else:
        result["operating_margin"] = None

    # ── Net Margin TTM % ──────────────────────────────────────────────
    net_margin_val = None
    if len(quarterly) >= 4:
        ni_sum = sum(safe_float(e[1].get("netIncome")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            net_margin_val = (ni_sum / rev_sum) * 100
    result["net_margin"] = round(net_margin_val, 1) if net_margin_val is not None else None

    # ── Net Margin YoY Δ ──────────────────────────────────────────────
    if len(quarterly) >= 5:
        def _nm(entry):
            ni = safe_float(entry.get("netIncome"))
            rev = safe_float(entry.get("totalRevenue"))
            if ni is not None and rev and rev > 0:
                return (ni / rev) * 100
            return None
        curr_nm = _nm(quarterly[0][1])
        yoy_nm = _nm(quarterly[4][1])
        if curr_nm is not None and yoy_nm is not None:
            result["net_margin_yoy"] = round(curr_nm - yoy_nm, 1)
        else:
            result["net_margin_yoy"] = None
    else:
        result["net_margin_yoy"] = None

    # ── FCF Margin TTM % ──────────────────────────────────────────────
    fcf_margin_val = None
    if len(cf_quarterly) >= 4 and len(quarterly) >= 4:
        fcf_sum = sum(safe_float(e[1].get("freeCashFlow")) or 0 for e in cf_quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            fcf_margin_val = (fcf_sum / rev_sum) * 100
    elif cf_quarterly and quarterly:
        fcf = safe_float(cf_quarterly[0][1].get("freeCashFlow"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if fcf is not None and rev and rev > 0:
            fcf_margin_val = (fcf / rev) * 100
    result["fcf_margin"] = round(fcf_margin_val, 1) if fcf_margin_val is not None else None

    # ── Opex % of Revenue ─────────────────────────────────────────────
    if quarterly:
        opex = safe_float(quarterly[0][1].get("totalOperatingExpenses"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if opex is not None and rev and rev > 0:
            result["opex_pct_revenue"] = round((opex / rev) * 100, 1)
        else:
            result["opex_pct_revenue"] = None
    else:
        result["opex_pct_revenue"] = None

    # ── S&M+R&D % of Revenue ─────────────────────────────────────────
    if quarterly:
        latest = quarterly[0][1]
        sm = safe_float(latest.get("sellingAndMarketingExpenses")) or 0
        sga = safe_float(latest.get("sellingGeneralAdministrative")) or 0
        rd = safe_float(latest.get("researchDevelopment")) or 0
        sm_total = sm if sm else sga
        combined = sm_total + rd
        rev = safe_float(latest.get("totalRevenue"))
        if combined > 0 and rev and rev > 0:
            result["sm_rd_pct_revenue"] = round((combined / rev) * 100, 1)
        else:
            result["sm_rd_pct_revenue"] = None
    else:
        result["sm_rd_pct_revenue"] = None

    # ── Rule of 40 ────────────────────────────────────────────────────
    r40 = None
    if rev_growth_ttm is not None and net_margin_val is not None:
        r40 = rev_growth_ttm + net_margin_val
    result["rule_of_40"] = round(r40, 1) if r40 is not None else None

    # ── Qtrs to Profitability ─────────────────────────────────────────
    # Uses TTM (4-quarter) net income to judge profitability, then
    # projects quarters to breakeven from the net-margin improvement
    # trend over the last 8 quarters.
    qtrs_to_prof = None
    if len(quarterly) >= 4:
        # Check TTM profitability (sum of last 4 quarters)
        ttm_ni = sum(safe_float(e[1].get("netIncome")) or 0 for e in quarterly[:4])
        ttm_rev = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])

        if ttm_rev > 0 and ttm_ni >= 0:
            result["qrtrs_to_profitability"] = "Profitable"
            qtrs_to_prof = 0
        elif ttm_rev > 0:
            # Unprofitable — estimate quarters to breakeven
            ttm_margin = (ttm_ni / ttm_rev) * 100
            # Build per-quarter net margin series (newest first)
            margins = []
            for entry in quarterly[:8]:
                ni = safe_float(entry[1].get("netIncome"))
                rev = safe_float(entry[1].get("totalRevenue"))
                if ni is not None and rev and rev > 0:
                    margins.append((ni / rev) * 100)
            if len(margins) >= 4:
                # Average per-quarter improvement (positive = getting better)
                improvements = [margins[i] - margins[i + 1] for i in range(len(margins) - 1)]
                avg_improvement = sum(improvements) / len(improvements)
                if avg_improvement > 0.5:
                    # Project from current TTM margin to zero
                    qtrs_to_prof = max(1, int(-ttm_margin / avg_improvement) + 1)
                    if qtrs_to_prof > 20:
                        result["qrtrs_to_profitability"] = ">20"
                    else:
                        result["qrtrs_to_profitability"] = str(qtrs_to_prof)
                else:
                    result["qrtrs_to_profitability"] = "Not converging"
            else:
                result["qrtrs_to_profitability"] = None
        else:
            result["qrtrs_to_profitability"] = None
    elif quarterly:
        # Fewer than 4 quarters — check latest only
        latest_ni = safe_float(quarterly[0][1].get("netIncome"))
        if latest_ni is not None and latest_ni >= 0:
            result["qrtrs_to_profitability"] = "Profitable"
            qtrs_to_prof = 0
        else:
            result["qrtrs_to_profitability"] = None
    else:
        result["qrtrs_to_profitability"] = None

    # ── EPS Quarterly ─────────────────────────────────────────────────
    if eps_entries:
        eps_val = safe_float(eps_entries[0][1].get("epsActual"))
        result["eps_only"] = eps_val
    else:
        result["eps_only"] = None

    # ── EPS YoY % ─────────────────────────────────────────────────────
    if len(eps_entries) >= 5:
        curr_eps = safe_float(eps_entries[0][1].get("epsActual"))
        yoy_eps = safe_float(eps_entries[4][1].get("epsActual"))
        if curr_eps is not None and yoy_eps is not None and yoy_eps != 0:
            result["eps_yoy"] = round(((curr_eps - yoy_eps) / abs(yoy_eps)) * 100, 1)
        else:
            result["eps_yoy"] = None
    else:
        result["eps_yoy"] = None

    # ── One-Time Events Detection ──────────────────────────────────────
    # Compare recent 4 quarters against a baseline (Q-4 to Q-11) to detect
    # anomalous items.  Only flag when a metric is BOTH >2× the company's
    # own historical norm AND exceeds an absolute floor (15% of revenue or
    # 15pp margin deviation).  This avoids false positives on large caps
    # with structurally high investment income (GOOG, MSFT) and companies
    # with consistent margin trends (PLTR transitioning to profit).
    #
    # Direction: ⬆ = gain flatters results, ⬇ = charge depresses, ⬆⬇ = mixed.
    one_time_items = []  # list of (signed_pct, description)

    if len(quarterly) >= 5:
        # Build baseline from quarters 4–11 (prior 2 years)
        bl_oi_ni_gaps = []
        bl_toi_pcts = []
        bl_margins = []
        for _, entry in quarterly[4:12]:
            rev = safe_float(entry.get("totalRevenue"))
            oi = safe_float(entry.get("operatingIncome"))
            ni = safe_float(entry.get("netIncome"))
            toi = safe_float(entry.get("totalOtherIncomeExpenseNet"))
            if rev and rev > 0:
                if oi is not None and ni is not None:
                    bl_oi_ni_gaps.append(abs(ni - oi) / rev * 100)
                if toi is not None:
                    bl_toi_pcts.append(abs(toi) / rev * 100)
                if ni is not None:
                    bl_margins.append(ni / rev * 100)

        avg_gap = _stats.mean(bl_oi_ni_gaps) if bl_oi_ni_gaps else 0
        avg_toi = _stats.mean(bl_toi_pcts) if bl_toi_pcts else 0
        avg_margin = _stats.mean(bl_margins) if bl_margins else 0
        std_margin = (_stats.stdev(bl_margins)
                      if len(bl_margins) >= 2 else 0)

        # Detect consistent margin trend (all recent quarters deviate in
        # the same direction) — this is growth/decline, not one-time.
        margin_devs = []
        for q_date, entry in quarterly[:4]:
            rev = safe_float(entry.get("totalRevenue"))
            ni = safe_float(entry.get("netIncome"))
            if rev and rev > 0 and ni is not None:
                margin_devs.append(ni / rev * 100 - avg_margin)
        is_margin_trend = (len(margin_devs) >= 3
                           and (all(d > 0 for d in margin_devs)
                                or all(d < 0 for d in margin_devs)))

        for i, (q_date, entry) in enumerate(quarterly[:4]):
            rev = safe_float(entry.get("totalRevenue"))
            oi = safe_float(entry.get("operatingIncome"))
            ni = safe_float(entry.get("netIncome"))
            nr = safe_float(entry.get("nonRecurring"))
            ei = safe_float(entry.get("extraordinaryItems"))
            dc = safe_float(entry.get("discontinuedOperations"))
            toi = safe_float(entry.get("totalOtherIncomeExpenseNet"))
            if not rev or rev <= 0:
                continue
            q_label = q_date[:7]  # YYYY-MM

            # Explicit non-recurring items (>5% of revenue — always flag)
            if nr and abs(nr) / rev > 0.05:
                pct = nr / rev * 100
                sign = "+" if pct > 0 else ""
                one_time_items.append(
                    (pct, f"nonRecurring {sign}{pct:.0f}% of rev ({q_label})"))

            # Extraordinary items (>3% of revenue — always flag)
            if ei and abs(ei) / rev > 0.03:
                pct = ei / rev * 100
                sign = "+" if pct > 0 else ""
                one_time_items.append(
                    (pct, f"extraordinary {sign}{pct:.0f}% of rev ({q_label})"))

            # Discontinued operations (>3% of revenue — always flag)
            if dc and abs(dc) / rev > 0.03:
                pct = dc / rev * 100
                sign = "+" if pct > 0 else ""
                one_time_items.append(
                    (pct, f"discontinued ops {sign}{pct:.0f}% of rev ({q_label})"))

            # OI→NI gap: >2× baseline AND >15% absolute
            if oi is not None and ni is not None:
                gap_pct = (ni - oi) / rev * 100
                if abs(gap_pct) > 15 and (avg_gap == 0
                                           or abs(gap_pct) > avg_gap * 2):
                    sign = "+" if gap_pct > 0 else ""
                    one_time_items.append(
                        (gap_pct,
                         f"OI\u2192NI gap {sign}{gap_pct:.0f}% of rev ({q_label})"))

            # Other inc/exp: >2× baseline AND >15% absolute
            if toi:
                toi_pct = toi / rev * 100
                if (abs(toi_pct) > 15
                        and (avg_toi == 0 or abs(toi_pct) > avg_toi * 2)):
                    sign = "+" if toi_pct > 0 else ""
                    one_time_items.append(
                        (toi_pct,
                         f"other inc/exp {sign}{toi_pct:.0f}% of rev ({q_label})"))

            # Margin swing: >2× stdev AND >15pp — skip if consistent trend
            if ni is not None and std_margin > 0 and not is_margin_trend:
                margin = ni / rev * 100
                margin_dev = margin - avg_margin
                if abs(margin_dev) > max(std_margin * 2, 15):
                    sign = "+" if margin_dev > 0 else ""
                    one_time_items.append(
                        (margin_dev,
                         f"margin swing {sign}{margin_dev:.0f}pp vs norm ({q_label})"))

    if one_time_items:
        has_gain = any(v > 0 for v, _ in one_time_items)
        has_charge = any(v < 0 for v, _ in one_time_items)
        if has_gain and has_charge:
            arrow = "\u2b06\u2b07"  # ⬆⬇
        elif has_gain:
            arrow = "\u2b06"        # ⬆
        else:
            arrow = "\u2b07"        # ⬇
        details = " | ".join(desc for _, desc in one_time_items)
        result["one_time_events"] = f"{arrow} {details}"
    else:
        result["one_time_events"] = None

    # ── R40 Score ─────────────────────────────────────────────────────
    # Format: 💎💎 R40: 54 | FCF +27% | ⏳ ~12 qtrs GAAP
    #
    # Gem scale calibrated to screened universe (GM>45%, RevGr>20%):
    #   no gems  = R40 < 20  (weakest in this universe)
    #   💎       = R40 20–39
    #   💎💎     = R40 40–59
    #   💎💎💎   = R40 60+
    r40 = result.get("rule_of_40")
    if r40 is not None:
        if r40 >= 60:
            gem_str = "\U0001f48e\U0001f48e\U0001f48e"   # 💎💎💎
        elif r40 >= 40:
            gem_str = "\U0001f48e\U0001f48e"              # 💎💎
        elif r40 >= 20:
            gem_str = "\U0001f48e"                        # 💎
        else:
            gem_str = ""                                  # no gems

        # FCF segment
        fcf = result.get("fcf_margin")
        fcf_str = f"FCF {'+' if fcf >= 0 else ''}{fcf:.0f}%" if fcf is not None else None

        # Profitability status segment
        nm = result.get("net_margin")
        qtrs = qtrs_to_prof
        if nm is not None and nm >= 0:
            profit_str = "\u2705"                          # ✅
        elif qtrs is not None:
            profit_str = f"\u23f3 ~{qtrs:.0f} qtrs GAAP"  # ⏳
        else:
            profit_str = "\u2753 path unclear"             # ❓

        r40_parts = [f"{gem_str} R40: {r40:.0f}".strip()]
        if fcf_str:
            r40_parts.append(fcf_str)
        r40_parts.append(profit_str)
        result["r40_score"] = " | ".join(r40_parts)

    # ── Fundamentals Snapshot ─────────────────────────────────────────
    # Format: Rev +27% YoY | 3Y CAGR +44% | GM 88% ↑ | Net -5% ⚡ FCF +27% | ⏳ ~12 qtrs GAAP
    #
    # Rules:
    # - All % values show explicit + or - sign
    # - GM trend arrow: ↑ if gm_trend_val > 1pp, ↓ if < -1pp, → otherwise
    # - ⚡ appears between Net and FCF only when FCF exceeds Net margin by >15pp
    #   (signals: "accounting loss but cash-generative")
    # - Profitability segment reuses same logic as r40_score above
    snapshot_parts = []

    # Rev YoY
    if rev_growth_ttm is not None:
        sign = "+" if rev_growth_ttm >= 0 else ""
        snapshot_parts.append(f"Rev {sign}{rev_growth_ttm:.0f}% YoY")

    # 3Y CAGR
    cagr = result.get("rev_cagr")
    if cagr is not None:
        sign = "+" if cagr >= 0 else ""
        snapshot_parts.append(f"3Y CAGR {sign}{cagr:.0f}%")

    # GM + trend arrow
    gm = result.get("gross_margin")
    gm_trend = gm_trend_val  # raw pp change
    if gm is not None:
        if gm_trend is not None:
            if gm_trend > 1:
                arrow = " \u2191"    # ↑
            elif gm_trend < -1:
                arrow = " \u2193"    # ↓
            else:
                arrow = " \u2192"    # →
        else:
            arrow = ""
        snapshot_parts.append(f"GM {gm:.0f}%{arrow}")

    # Net margin + optional ⚡ + FCF margin
    nm = result.get("net_margin")
    fcf = result.get("fcf_margin")
    if nm is not None and fcf is not None:
        nm_sign = "+" if nm >= 0 else ""
        fcf_sign = "+" if fcf >= 0 else ""
        gap = fcf - nm
        if gap > 15:
            # FCF materially better than net margin — flag it
            snapshot_parts.append(
                f"Net {nm_sign}{nm:.0f}% \u26a1 FCF {fcf_sign}{fcf:.0f}%"  # ⚡
            )
        else:
            snapshot_parts.append(f"Net {nm_sign}{nm:.0f}% | FCF {fcf_sign}{fcf:.0f}%")
    elif nm is not None:
        nm_sign = "+" if nm >= 0 else ""
        snapshot_parts.append(f"Net {nm_sign}{nm:.0f}%")
    elif fcf is not None:
        fcf_sign = "+" if fcf >= 0 else ""
        snapshot_parts.append(f"FCF {fcf_sign}{fcf:.0f}%")

    # Profitability status (same logic as r40_score)
    if nm is not None and nm >= 0:
        snapshot_parts.append("\u2705")                           # ✅
    elif qtrs_to_prof is not None:
        snapshot_parts.append(f"\u23f3 ~{qtrs_to_prof:.0f} qtrs GAAP")  # ⏳
    else:
        snapshot_parts.append("\u2753 path unclear")              # ❓

    if snapshot_parts:
        result["fundamentals_snapshot"] = " | ".join(snapshot_parts)

    result["data"] = datetime.now().strftime("%Y-%m-%d")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="EODHD Financial Data Updater")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and calculate but don't write to sheet")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Process only this ticker (for testing)")
    parser.add_argument("--force", action="store_true",
                        help="Update all tickers even if data is recent")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N tickers (useful with --dry-run)")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== EODHD Updater started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    # Validate EODHD key
    eodhd_key = os.environ.get("EODHD_API_KEY")
    if not eodhd_key:
        logger.error("EODHD_API_KEY env var is not set")
        sys.exit(1)

    # Read current sheet data
    service = get_sheets_service()
    ensure_sheet_columns(service, len(ALL_COLUMNS), logger)
    all_rows = read_all_rows(service)
    logger.info("Read %d rows from sheet (including headers)", len(all_rows))

    # Load screening criteria from the Criteria sheet
    criteria = read_criteria(service, logger)

    # ── Read sheet headers and build column mapping ─────────────────
    # The sheet now uses lowercase underscore column keys as headers.
    # We also support legacy display-name headers via HEADER_ALIASES.
    all_keys = set(DISPLAY_NAMES.keys())
    # Reverse map: display name → internal key (e.g. "gross_margin%" → "gross_margin")
    display_to_key = {v: k for k, v in DISPLAY_NAMES.items()}

    key_col = {}  # column_key → 0-based column index
    if len(all_rows) >= 2:
        for idx, header in enumerate(all_rows[1]):
            name = header.strip()
            # Apply aliases first (maps legacy names → current keys)
            name = HEADER_ALIASES.get(name, name)
            # Check if the header matches a known column key directly
            if name in all_keys:
                col_key = name
            # Check if it's a display name (e.g. "gross_margin%" → "gross_margin")
            elif name in display_to_key:
                col_key = display_to_key[name]
            else:
                col_key = None
            if col_key and col_key not in key_col:
                key_col[col_key] = idx

    missing = [k for k in EODHD_COLUMNS if k not in key_col]
    if missing:
        logger.warning("Columns not found in sheet (will skip): %s",
                        [DISPLAY_NAMES.get(k, k) for k in missing])

    # Data starts at row 3 (index 2)
    data_rows = all_rows[2:] if len(all_rows) > 2 else []

    # Build list of tickers to process
    ticker_col = key_col.get("ticker", 0)
    exchange_col = key_col.get("exchange")
    company_col = key_col.get("company_name", 2)
    fund_date_col = key_col.get("data")

    # Determine the max column index we need to pad to
    pad_cols = [ticker_col, company_col, fund_date_col or 0]
    if exchange_col is not None:
        pad_cols.append(exchange_col)

    tickers_to_process = []
    for i, row in enumerate(data_rows):
        row_number = i + 3  # 1-indexed sheet row
        padded = row + [""] * (max(pad_cols) + 1 - len(row))
        ticker = padded[ticker_col].strip()
        company = padded[company_col].strip()
        exchange = padded[exchange_col].strip() if exchange_col is not None else ""

        if not ticker:
            continue

        if args.ticker and ticker.upper() != args.ticker.upper():
            continue

        # Default exchange to US if not specified
        if not exchange:
            exchange = "US"

        # Skip if data is recent or previously flagged as unavailable
        if not args.force and fund_date_col is not None:
            fund_date_str = padded[fund_date_col].strip() if fund_date_col < len(padded) else ""
            if fund_date_str:
                if fund_date_str == "No EODHD data":
                    logger.info("Skipping %s — previously flagged as no EODHD data", ticker)
                    continue
                try:
                    last_date = dateparser.parse(fund_date_str).date()
                    if (date.today() - last_date) <= timedelta(days=STALENESS_DAYS):
                        logger.info("Skipping %s — data is recent (%s)", ticker, fund_date_str)
                        continue
                except (ValueError, TypeError):
                    pass

        tickers_to_process.append((row_number, ticker, company, exchange, row))

    if args.limit:
        tickers_to_process = tickers_to_process[:args.limit]

    logger.info("Will process %d tickers", len(tickers_to_process))

    if not tickers_to_process:
        logger.info("Nothing to do.")
        return

    # Process each ticker
    updates = []
    total_written = 0
    errors = 0

    for idx, (row_number, ticker, company, exchange, existing_row) in enumerate(tickers_to_process):
        try:
            eodhd_data = fetch_eodhd_data(ticker, eodhd_key, logger,
                                            exchange=exchange, company=company)
            if eodhd_data is None:
                errors += 1
                # Flag the ticker so it's skipped on future runs and
                # visibly marked in the sheet.
                if not args.dry_run:
                    data_col = key_col.get("data")
                    snapshot_col = key_col.get("fundamentals_snapshot")
                    flag_values = {}
                    if data_col is not None:
                        flag_values[_col_letter(data_col)] = "No EODHD data"
                    if snapshot_col is not None:
                        flag_values[_col_letter(snapshot_col)] = "No EODHD data"
                    if flag_values:
                        updates.append({"row": row_number, "values": flag_values})
                        logger.info("Flagged %s as 'No EODHD data'", ticker)
                continue

            if args.dry_run:
                logger.info("[DRY RUN] %s.%s (%s):", ticker, exchange, company)
                logger.info("  r40_score:             %s", eodhd_data.get("r40_score", ""))
                logger.info("  fundamentals_snapshot: %s", eodhd_data.get("fundamentals_snapshot", ""))
                for key in EODHD_COLUMNS:
                    if key not in ("r40_score", "fundamentals_snapshot"):
                        val = eodhd_data.get(key)
                        if val is not None:
                            display = DISPLAY_NAMES.get(key, key)
                            # Format for display
                            if key in PCT_COLS and isinstance(val, (int, float)):
                                logger.info("  %-25s %s%%", display, val)
                            elif key in INTEGER_COLS and isinstance(val, (int, float)):
                                logger.info("  %-25s %s", display, int(val))
                            elif key in DOLLAR_COLS and isinstance(val, (int, float)):
                                logger.info("  %-25s $%s", display, val)
                            else:
                                logger.info("  %-25s %s", display, val)
                continue

            # Build update values using actual sheet column positions
            values = {}
            for key in EODHD_COLUMNS:
                col_idx = key_col.get(key)
                if col_idx is None:
                    logger.warning("Column '%s' not found in sheet headers, skipping", key)
                    continue
                col_letter = _col_letter(col_idx)
                val = eodhd_data.get(key)
                # Use consistent null for missing data
                if val is None:
                    values[col_letter] = NULL_VALUE
                elif key in PCT_COLS and isinstance(val, (int, float)):
                    formatted = f"{val:.1f}%"
                    dot = evaluate_criteria(val, key, criteria)
                    values[col_letter] = f"{dot} {formatted}" if dot else formatted
                elif key in INTEGER_COLS and isinstance(val, (int, float)):
                    formatted = f"{val:.0f}"
                    dot = evaluate_criteria(val, key, criteria)
                    values[col_letter] = f"{dot} {formatted}" if dot else formatted
                elif key in DOLLAR_COLS and isinstance(val, (int, float)):
                    formatted = f"${val:.2f}"
                    dot = evaluate_criteria(val, key, criteria)
                    values[col_letter] = f"{dot} {formatted}" if dot else formatted
                else:
                    formatted = str(val)
                    # For string values like "8/10", try to extract numeric
                    # part for criteria evaluation
                    numeric_val = safe_float(formatted.split("/")[0]) if "/" in formatted else safe_float(formatted)
                    dot = evaluate_criteria(numeric_val, key, criteria)
                    values[col_letter] = f"{dot} {formatted}" if dot else formatted

            if values:
                updates.append({"row": row_number, "values": values})
                logger.info("Calculated %d metrics for %s", len(values), ticker)

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc, exc_info=True)
            errors += 1

        # Write batch
        if updates and not args.dry_run and (len(updates) >= BATCH_SIZE or idx == len(tickers_to_process) - 1):
            logger.info("Writing batch of %d updates to sheet...", len(updates))
            write_row_updates(service, updates, logger)
            total_written += len(updates)
            updates = []

        # Delay between API calls
        if idx < len(tickers_to_process) - 1:
            time.sleep(DELAY_BETWEEN_CALLS)

    elapsed = time.time() - start_time
    if args.dry_run:
        logger.info("=== DRY RUN complete. %d tickers processed in %.1fs ===",
                     len(tickers_to_process), elapsed)
    else:
        # Clear any background colors that may have leaked to data rows
        clear_data_row_formatting(service, logger)
        logger.info(
            "=== Updated %d tickers. Skipped %d due to errors. (%.1fs) ===",
            total_written, errors, elapsed,
        )


if __name__ == "__main__":
    main()
