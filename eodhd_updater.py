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
        "ticker", "exchange", "company", "description",
    ]),
    ("OVERVIEW", "2E4057", [
        "r40_score", "fundamentals_snapshot", "short_outlook",
    ]),
    ("REVENUE", "1B3A4B", [
        "annual_revenue_5y", "quarterly_revenue",
        "rev_growth_ttm", "rev_growth_qoq", "rev_cagr_3y", "rev_consistency",
    ]),
    ("MARGINS", "1B3A4B", [
        "gross_margin_ttm", "gross_margin_trend",
        "operating_margin_ttm", "net_margin_ttm", "net_margin_yoy_delta",
        "fcf_margin_ttm",
    ]),
    ("EFFICIENCY", "1B3A4B", [
        "opex_pct_revenue", "sm_rd_pct_revenue",
        "rule_of_40", "qtrs_to_profitability",
    ]),
    ("EARNINGS", "1B3A4B", [
        "eps_quarterly", "eps_yoy_pct",
    ]),
    ("AI NARRATIVE", "2E4057", [
        "outlook", "risks",
    ]),
    ("LAST ANALYSIS", "1B3A4B", [
        "ai_analysis_date",
    ]),
    ("DATA DATE", "2E4057", [
        "data",
    ]),
]

# Maps internal column keys to the header text shown in the sheet.
# The sheet now uses lowercase underscore names as headers.
DISPLAY_NAMES = {
    "ticker":               "ticker",
    "exchange":             "exchange",
    "company":              "company",
    "description":          "description",
    "annual_revenue_5y":    "annual_revenue_5y",
    "quarterly_revenue":    "quarterly_revenue",
    "rev_growth_ttm":       "rev_growth_ttm",
    "rev_growth_qoq":       "rev_growth_qoq",
    "rev_cagr_3y":          "rev_cagr_3y",
    "rev_consistency":      "rev_consistency",
    "gross_margin_ttm":     "gross_margin_ttm",
    "gross_margin_trend":   "gross_margin_trend",
    "operating_margin_ttm": "operating_margin_ttm",
    "net_margin_ttm":       "net_margin_ttm",
    "net_margin_yoy_delta": "net_margin_yoy_delta",
    "fcf_margin_ttm":       "fcf_margin_ttm",
    "opex_pct_revenue":     "opex_pct_revenue",
    "sm_rd_pct_revenue":    "sm_rd_pct_revenue",
    "rule_of_40":           "rule_of_40",
    "qtrs_to_profitability": "qtrs_to_profitability",
    "eps_quarterly":        "eps_quarterly",
    "eps_yoy_pct":          "eps_yoy_pct",
    "r40_score":            "r40_score",
    "fundamentals_snapshot": "fundamentals_snapshot",
    "short_outlook":        "short_outlook",
    "outlook":              "outlook",
    "risks":                "risks",
    "ai_analysis_date":     "ai_analysis_date",
    "data":                 "data",
}

# Map legacy/alternative sheet headers → current header names.
HEADER_ALIASES = {
    # Legacy display-name headers → new lowercase keys
    "Ticker":               "ticker",
    "Exchange":             "exchange",
    "Company":              "company",
    "Company Name":         "company",
    "Description":          "description",
    "Annual Revenue (5Y)":  "annual_revenue_5y",
    "Quarterly Revenue":    "quarterly_revenue",
    "Rev Growth TTM %":     "rev_growth_ttm",
    "Rev Growth QoQ %":     "rev_growth_qoq",
    "Rev CAGR 3Y %":       "rev_cagr_3y",
    "Rev Consistency Score": "rev_consistency",
    "Gross Margin %":       "gross_margin_ttm",
    "GM Trend (Qtly)":      "gross_margin_trend",
    "Operating Margin %":   "operating_margin_ttm",
    "Net Margin %":         "net_margin_ttm",
    "Net Margin YoY Δ":     "net_margin_yoy_delta",
    "FCF Margin %":         "fcf_margin_ttm",
    "Opex % of Revenue":    "opex_pct_revenue",
    "S&M+R&D % of Revenue": "sm_rd_pct_revenue",
    "Rule of 40":           "rule_of_40",
    "Qtrs to Profitability": "qtrs_to_profitability",
    "EPS Qtrly":            "eps_quarterly",
    "EPS YoY %":            "eps_yoy_pct",
    "R40 Score":            "r40_score",
    "Fundamentals Snapshot": "fundamentals_snapshot",
    "Short Outlook":        "short_outlook",
    "Outlook":              "outlook",
    "Full Outlook":         "outlook",
    "Key Risks":            "risks",
    "AI":                   "ai_analysis_date",
    "Analyzed":             "ai_analysis_date",
    "AI Analyzed":          "ai_analysis_date",
    "Data":                 "data",
    "Data As Of":           "data",
    "Fundamentals Date":    "data",
    "data":                 "data",
    # Current sheet headers (lowercase with %, &, special chars)
    "rev_growth_ttm%":          "rev_growth_ttm",
    "rev_growth_qoq%":          "rev_growth_qoq",
    "rev_cagr%":                "rev_cagr_3y",
    "rev_consistency_score":    "rev_consistency",
    "gross_margin%":            "gross_margin_ttm",
    "gm_trend%":                "gross_margin_trend",
    "operating_margin%":        "operating_margin_ttm",
    "net_margin%":              "net_margin_ttm",
    "net_margin_yoy%":          "net_margin_yoy_delta",
    "fcf_margin%":              "fcf_margin_ttm",
    "opex_%_of_revenue":        "opex_pct_revenue",
    "s&m+r&d_%_of_revenue":     "sm_rd_pct_revenue",
    "qrtrs_to_profitability":   "qtrs_to_profitability",
    "eps_only":                 "eps_quarterly",
    "eps_yoy%":                 "eps_yoy_pct",
}

COL_WIDTHS = {
    "ticker": 10,
    "exchange": 10,
    "company": 25,
    "description": 40,
    "annual_revenue_5y": 45,
    "quarterly_revenue": 70,
    "rev_growth_ttm": 18,
    "rev_growth_qoq": 18,
    "rev_cagr_3y": 18,
    "rev_consistency": 18,
    "gross_margin_ttm": 18,
    "gross_margin_trend": 28,
    "operating_margin_ttm": 18,
    "net_margin_ttm": 18,
    "net_margin_yoy_delta": 18,
    "fcf_margin_ttm": 18,
    "opex_pct_revenue": 18,
    "sm_rd_pct_revenue": 22,
    "rule_of_40": 14,
    "qtrs_to_profitability": 18,
    "eps_quarterly": 14,
    "eps_yoy_pct": 14,
    "r40_score": 22,
    "fundamentals_snapshot": 55,
    "short_outlook": 50,
    "outlook": 80,
    "risks": 50,
    "ai_analysis_date": 16,
    "data": 16,
}

# Columns that should be formatted as percentages
PCT_COLS = {
    "rev_growth_ttm", "rev_growth_qoq", "rev_cagr_3y",
    "gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm",
    "net_margin_yoy_delta", "fcf_margin_ttm",
    "opex_pct_revenue", "sm_rd_pct_revenue", "eps_yoy_pct",
}

# Columns that should be formatted as decimals
DECIMAL_COLS = set()  # no decimal-formatted columns currently

# Columns formatted as plain integers (no decimal, no % sign)
INTEGER_COLS = {"rule_of_40"}

# Columns that should be formatted as dollars
DOLLAR_COLS = {"eps_quarterly"}

# Columns populated by EODHD (as opposed to AI narrative columns)
EODHD_COLUMNS = [
    "annual_revenue_5y", "quarterly_revenue",
    "rev_growth_ttm", "rev_growth_qoq", "rev_cagr_3y", "rev_consistency",
    "gross_margin_ttm", "gross_margin_trend",
    "operating_margin_ttm", "net_margin_ttm", "net_margin_yoy_delta",
    "fcf_margin_ttm",
    "opex_pct_revenue", "sm_rd_pct_revenue",
    "rule_of_40", "qtrs_to_profitability",
    "eps_quarterly", "eps_yoy_pct",
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
    # Read enough columns to cover all defined columns
    end_col = _col_letter(len(ALL_COLUMNS) - 1)
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:{end_col}",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


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
    "SWX":      "SW",
    "BIT":      "MI",
    "BME":      "MC",
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
    # Americas
    "TSX":      "TO",
    "TSXV":     "V",
    "SAO":      "SA",
    "BVMF":     "SA",
    # Africa / Middle East
    "JSE":      "JSE",
    "TADAWUL":  "SR",
    "SAU":      "SR",
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
    "BSE":   ["NSE"],
    "NSE":   ["BSE"],
    # Japan
    "TSE":   ["US"],
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
}

# Always append US as a last-ditch fallback (many international companies
# have US-listed ADRs or cross-listings that carry fundamentals data).
for _exc, _chain in EXCHANGE_FALLBACKS.items():
    if "US" not in _chain:
        _chain.append("US")


def _try_fetch_one(ticker: str, exchange: str, api_key: str,
                   timeout: int = 30) -> tuple[dict | None, int]:
    """Attempt a single EODHD fundamentals fetch.

    Returns (json_data, http_status).  json_data is None on any failure.
    http_status is 0 for non-HTTP errors.
    """
    symbol = f"{ticker}.{exchange}"
    url = f"{EODHD_BASE_URL}/{symbol}"
    params = {"api_token": api_key, "fmt": "json"}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), resp.status_code
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        return None, status
    except Exception:
        return None, 0


def _search_eodhd(query: str, api_key: str, logger: logging.Logger) -> str | None:
    """Use the EODHD search API to find the best exchange for a ticker.

    Returns the EODHD exchange code (e.g. 'US') or None.
    """
    url = "https://eodhd.com/api/search/" + query
    params = {"api_token": api_key, "fmt": "json", "limit": 5}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        # Prefer results whose Code matches the ticker exactly
        for r in results:
            if r.get("Code", "").upper() == query.upper():
                ex = r.get("Exchange", "")
                if ex:
                    logger.info("Search API matched %s → %s.%s", query, r["Code"], ex)
                    return ex
        # Otherwise take the first result
        ex = results[0].get("Exchange", "")
        if ex:
            logger.info("Search API best guess for %s → %s.%s",
                        query, results[0].get("Code", "?"), ex)
            return ex
    except Exception as exc:
        logger.debug("EODHD search failed for '%s': %s", query, exc)
    return None


def _fetch_fundamentals_raw(ticker: str, api_key: str, logger: logging.Logger,
                            exchange: str = "US") -> dict | None:
    """Fetch full fundamental data from EODHD for a ticker.

    Tries the primary exchange first, then walks through
    EXCHANGE_FALLBACKS, and finally falls back to the EODHD search API.
    """
    primary = _resolve_exchange(exchange)
    logger.info("Fetching fundamentals for %s (exchange: %s → %s)", ticker, exchange, primary)

    # 1. Try primary exchange
    data, status = _try_fetch_one(ticker, primary, api_key)
    if data is not None:
        return data
    if status not in (404, 0):
        # Non-404 HTTP error (auth, rate-limit, server error) — don't retry
        logger.warning("EODHD HTTP %d for %s.%s, not retrying", status, ticker, primary)
        return None

    # 2. Try fallback exchanges
    fallbacks = EXCHANGE_FALLBACKS.get(primary, [])
    for alt in fallbacks:
        if alt == primary:
            continue
        logger.info("  Trying fallback %s.%s", ticker, alt)
        time.sleep(0.3)  # light rate-limit courtesy
        data, status = _try_fetch_one(ticker, alt, api_key)
        if data is not None:
            logger.info("  Found %s on %s (fallback from %s)", ticker, alt, primary)
            return data
        if status not in (404, 0):
            logger.warning("EODHD HTTP %d for %s.%s, stopping fallback chain",
                           status, ticker, alt)
            return None

    # 3. Last resort: EODHD search API
    logger.info("  All fallbacks exhausted for %s, trying search API", ticker)
    search_exchange = _search_eodhd(ticker, api_key, logger)
    if search_exchange and search_exchange != primary and search_exchange not in fallbacks:
        time.sleep(0.3)
        data, status = _try_fetch_one(ticker, search_exchange, api_key)
        if data is not None:
            logger.info("  Found %s on %s via search API", ticker, search_exchange)
            return data

    logger.warning("Could not find fundamentals for %s on any exchange "
                   "(tried %s + %s + search)", ticker, primary, fallbacks)
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
# Core: fetch_eodhd_data — calculates all metrics for one ticker
# ---------------------------------------------------------------------------


def fetch_eodhd_data(ticker: str, api_key: str, logger: logging.Logger,
                     exchange: str = "US") -> dict | None:
    """Fetch EODHD fundamentals and compute all financial metrics.

    Returns a dict keyed by EODHD_COLUMNS column keys, or None on failure.
    """
    raw = _fetch_fundamentals_raw(ticker, api_key, logger, exchange=exchange)
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

    # ── Rev CAGR 3Y % ────────────────────────────────────────────────
    if len(yearly) >= 4:
        recent_rev = safe_float(yearly[0][1].get("totalRevenue"))
        base_rev = safe_float(yearly[3][1].get("totalRevenue"))
        if recent_rev and base_rev and base_rev > 0 and recent_rev > 0:
            cagr = ((recent_rev / base_rev) ** (1 / 3) - 1) * 100
            result["rev_cagr_3y"] = round(cagr, 1)
        else:
            result["rev_cagr_3y"] = None
    else:
        result["rev_cagr_3y"] = None

    # ── Rev Consistency Score (0–10) ──────────────────────────────────
    # Always scored out of 10 (need 11 quarters). Show as "X/10".
    if len(quarterly) >= 11:
        growth_count = 0
        for i in range(10):
            c = safe_float(quarterly[i][1].get("totalRevenue"))
            p = safe_float(quarterly[i + 1][1].get("totalRevenue"))
            if c is not None and p is not None and c > p:
                growth_count += 1
        result["rev_consistency"] = f"{growth_count}/10"
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
        result["rev_consistency"] = f"{scaled}/10"
    else:
        result["rev_consistency"] = None

    # ── Gross Margin TTM % ────────────────────────────────────────────
    gross_margin_ttm = None
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
            gross_margin_ttm = (gp_sum / rev_sum) * 100
    elif quarterly:
        gp = safe_float(quarterly[0][1].get("grossProfit"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if gp is None and rev is not None:
            cor = safe_float(quarterly[0][1].get("costOfRevenue"))
            if cor is not None:
                gp = rev - cor
        if gp is not None and rev and rev > 0:
            gross_margin_ttm = (gp / rev) * 100
    result["gross_margin_ttm"] = round(gross_margin_ttm, 1) if gross_margin_ttm is not None else None

    # ── GM Trend (Qtly) ──────────────────────────────────────────────
    # Shows per-quarter gross margins (newest→oldest) plus the overall
    # pp change with a directional arrow, e.g. "52%→49%→47%→45% ↑7pp"
    gross_margin_trend = None
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
            gross_margin_trend = margins[0] - margins[-1]  # pp change newest vs oldest
            arrow = "↑" if gross_margin_trend > 1 else ("↓" if gross_margin_trend < -1 else "→")
            margin_strs = [f"{m:.0f}%" for m in margins]
            pp = f"{abs(gross_margin_trend):.0f}pp"
            result["gross_margin_trend"] = f"{'→'.join(margin_strs)} {arrow}{pp}"
        else:
            result["gross_margin_trend"] = None
    else:
        result["gross_margin_trend"] = None

    # ── Operating Margin TTM % ────────────────────────────────────────
    if len(quarterly) >= 4:
        oi_sum = sum(safe_float(e[1].get("operatingIncome")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            result["operating_margin_ttm"] = round((oi_sum / rev_sum) * 100, 1)
        else:
            result["operating_margin_ttm"] = None
    else:
        result["operating_margin_ttm"] = None

    # ── Net Margin TTM % ──────────────────────────────────────────────
    net_margin_ttm = None
    if len(quarterly) >= 4:
        ni_sum = sum(safe_float(e[1].get("netIncome")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            net_margin_ttm = (ni_sum / rev_sum) * 100
    result["net_margin_ttm"] = round(net_margin_ttm, 1) if net_margin_ttm is not None else None

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
            result["net_margin_yoy_delta"] = round(curr_nm - yoy_nm, 1)
        else:
            result["net_margin_yoy_delta"] = None
    else:
        result["net_margin_yoy_delta"] = None

    # ── FCF Margin TTM % ──────────────────────────────────────────────
    fcf_margin_ttm = None
    if len(cf_quarterly) >= 4 and len(quarterly) >= 4:
        fcf_sum = sum(safe_float(e[1].get("freeCashFlow")) or 0 for e in cf_quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            fcf_margin_ttm = (fcf_sum / rev_sum) * 100
    elif cf_quarterly and quarterly:
        fcf = safe_float(cf_quarterly[0][1].get("freeCashFlow"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if fcf is not None and rev and rev > 0:
            fcf_margin_ttm = (fcf / rev) * 100
    result["fcf_margin_ttm"] = round(fcf_margin_ttm, 1) if fcf_margin_ttm is not None else None

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
    if rev_growth_ttm is not None and net_margin_ttm is not None:
        r40 = rev_growth_ttm + net_margin_ttm
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
            result["qtrs_to_profitability"] = "Profitable"
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
                        result["qtrs_to_profitability"] = ">20"
                    else:
                        result["qtrs_to_profitability"] = str(qtrs_to_prof)
                else:
                    result["qtrs_to_profitability"] = "Not converging"
            else:
                result["qtrs_to_profitability"] = None
        else:
            result["qtrs_to_profitability"] = None
    elif quarterly:
        # Fewer than 4 quarters — check latest only
        latest_ni = safe_float(quarterly[0][1].get("netIncome"))
        if latest_ni is not None and latest_ni >= 0:
            result["qtrs_to_profitability"] = "Profitable"
            qtrs_to_prof = 0
        else:
            result["qtrs_to_profitability"] = None
    else:
        result["qtrs_to_profitability"] = None

    # ── EPS Quarterly ─────────────────────────────────────────────────
    if eps_entries:
        eps_val = safe_float(eps_entries[0][1].get("epsActual"))
        result["eps_quarterly"] = eps_val
    else:
        result["eps_quarterly"] = None

    # ── EPS YoY % ─────────────────────────────────────────────────────
    if len(eps_entries) >= 5:
        curr_eps = safe_float(eps_entries[0][1].get("epsActual"))
        yoy_eps = safe_float(eps_entries[4][1].get("epsActual"))
        if curr_eps is not None and yoy_eps is not None and yoy_eps != 0:
            result["eps_yoy_pct"] = round(((curr_eps - yoy_eps) / abs(yoy_eps)) * 100, 1)
        else:
            result["eps_yoy_pct"] = None
    else:
        result["eps_yoy_pct"] = None

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
        fcf = result.get("fcf_margin_ttm")
        fcf_str = f"FCF {'+' if fcf >= 0 else ''}{fcf:.0f}%" if fcf is not None else None

        # Profitability status segment
        nm = result.get("net_margin_ttm")
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
    # - GM trend arrow: ↑ if gross_margin_trend > 1pp, ↓ if < -1pp, → otherwise
    # - ⚡ appears between Net and FCF only when FCF exceeds Net margin by >15pp
    #   (signals: "accounting loss but cash-generative")
    # - Profitability segment reuses same logic as r40_score above
    snapshot_parts = []

    # Rev YoY
    if rev_growth_ttm is not None:
        sign = "+" if rev_growth_ttm >= 0 else ""
        snapshot_parts.append(f"Rev {sign}{rev_growth_ttm:.0f}% YoY")

    # 3Y CAGR
    cagr = result.get("rev_cagr_3y")
    if cagr is not None:
        sign = "+" if cagr >= 0 else ""
        snapshot_parts.append(f"3Y CAGR {sign}{cagr:.0f}%")

    # GM + trend arrow
    gm = result.get("gross_margin_ttm")
    gm_trend = gross_margin_trend  # raw pp change
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
    nm = result.get("net_margin_ttm")
    fcf = result.get("fcf_margin_ttm")
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

    # ── Read sheet headers and build column mapping ─────────────────
    # The sheet now uses lowercase underscore column keys as headers.
    # We also support legacy display-name headers via HEADER_ALIASES.
    all_keys = set(DISPLAY_NAMES.keys())

    key_col = {}  # column_key → 0-based column index
    if len(all_rows) >= 2:
        for idx, header in enumerate(all_rows[1]):
            name = header.strip()
            # Apply aliases first (maps legacy names → current keys)
            name = HEADER_ALIASES.get(name, name)
            # Check if the header matches a known column key directly
            if name in all_keys:
                col_key = name
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
    company_col = key_col.get("company", 1)
    data_date_col = key_col.get("data")

    # Determine the max column index we need to pad to
    pad_cols = [ticker_col, company_col, data_date_col or 0]
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

        # Skip if data is recent unless --force
        if not args.force and data_date_col is not None:
            fund_date_str = padded[data_date_col].strip() if data_date_col < len(padded) else ""
            if fund_date_str:
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
            eodhd_data = fetch_eodhd_data(ticker, eodhd_key, logger, exchange=exchange)
            if eodhd_data is None:
                errors += 1
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
                    values[col_letter] = f"{val:.1f}%"
                elif key in INTEGER_COLS and isinstance(val, (int, float)):
                    values[col_letter] = f"{val:.0f}"
                elif key in DOLLAR_COLS and isinstance(val, (int, float)):
                    values[col_letter] = f"${val:.2f}"
                else:
                    values[col_letter] = str(val)

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
        logger.info(
            "=== Updated %d tickers. Skipped %d due to errors. (%.1fs) ===",
            total_written, errors, elapsed,
        )


if __name__ == "__main__":
    main()
