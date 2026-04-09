#!/usr/bin/env python3
"""
Build Portfolio — Populate Portfolio sheet with dual-positive equities.

Reads AI Analysis sheet, finds equities where both Bear and Bull columns
have a ✅, deduplicates by company (favouring ADRs/US listings), and writes
them to the Portfolio sheet with relevant data.

ps_now is written as a live GOOGLEFINANCE formula:
  =GOOGLEFINANCE("EXCHANGE:TICKER","marketcap") / TTM_REVENUE

Schedule: Sundays 08:00 UTC (after bear + bull evaluations).
"""

import argparse
import json
import logging
import math
import os
import re
import sys
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
AI_ANALYSIS_SHEET = "AI Analysis"
PORTFOLIO_SHEET = "Portfolio"
NULL_VALUE = "\u2014"
FUZZY_THRESHOLD = 0.80

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# US exchanges — ADRs and primary US listings live here
US_EXCHANGES = {
    "NYSE", "NASDAQ", "AMEX", "NYSEARCA", "BATS", "ARCA",
}

# TradingView exchange → Google Finance exchange code for GOOGLEFINANCE()
TV_TO_GOOGLE_FINANCE = {
    # United States
    "NASDAQ": "NASDAQ", "NYSE": "NYSE", "NYSEARCA": "NYSEARCA",
    "NYSEMKT": "NYSEAMERICAN", "AMEX": "NYSEAMERICAN", "OTC": "OTCMKTS",
    "BATS": "BATS",
    # Canada
    "TSX": "TSE", "TSXV": "CVE",
    # United Kingdom
    "LSE": "LON", "LON": "LON", "LSIN": "LON",
    # Germany
    "XETRA": "ETR", "XETR": "ETR", "FRA": "FRA", "ETR": "ETR",
    "FWB": "FRA", "GETTEX": "ETR", "TRADEGATE": "ETR",
    "MU": "ETR", "STU": "ETR", "BE": "ETR", "DU": "ETR",
    "DUS": "ETR", "HM": "ETR", "HA": "ETR",
    # France
    "EPA": "EPA", "PAR": "EPA",
    # Netherlands
    "AMS": "AMS",
    # Switzerland
    "SWX": "SWX",
    # Italy
    "BIT": "BIT", "MIL": "BIT", "EUROTLX": "BIT",
    # Spain
    "BME": "BME",
    # Sweden
    "STO": "STO",
    # Norway
    "OSL": "OSL",
    # Denmark
    "CSE": "CPH",
    # Finland
    "HEL": "HEL",
    # Japan
    "TSE": "TYO", "JPX": "TYO", "TYO": "TYO",
    # India
    "NSE": "NSE", "BSE": "BOM", "NSEI": "NSE",
    # South Korea
    "KRX": "KRX", "KOSDAQ": "KRX",
    # Australia
    "ASX": "ASX",
    # New Zealand
    "NZX": "NZE",
    # Singapore
    "SGX": "SGX",
    # Hong Kong
    "HKG": "HKG", "HKEX": "HKG",
    # Brazil
    "SAO": "BVMF", "BVMF": "BVMF",
    # South Africa
    "JSE": "JSE",
    # Saudi Arabia
    "TADAWUL": "TADAWUL", "SAU": "TADAWUL",
    # Israel
    "TASE": "TLV",
    # Turkey
    "BIST": "IST",
    # Indonesia
    "IDX": "IDX",
    # Thailand
    "SET": "BKK",
    # Malaysia
    "MYX": "KLSE",
    # Philippines
    "PSE": "PSE",
    # Mexico
    "BMV": "BMV",
    # Poland
    "GPW": "WSE",
}

# Corporate suffixes to strip when normalising company names
CORPORATE_SUFFIXES = re.compile(
    r'\b('
    r'inc|incorporated|corp|corporation|ltd|limited|llc|plc|'
    r'sa|s\.a\.|se|s\.e\.|nv|n\.v\.|ag|a\.g\.|'
    r'co|company|group|holdings|holding|enterprises|'
    r'international|intl|technologies|technology|tech|'
    r'systems|solutions|therapeutics|pharmaceuticals|pharma|'
    r'biosciences|biopharma|medical|healthcare|'
    r'class\s*[a-z]|cl\s*[a-z]|adr'
    r')\b',
    re.IGNORECASE,
)

# Map AI Analysis headers to canonical keys
HEADER_ALIASES = {
    "Ticker":               "ticker",
    "ticker_clean":         "ticker",
    "Company":              "company_name",
    "Company Name":         "company_name",
    "Exchange":             "exchange",
    "Country":              "country",
    "Sector":               "sector",
    "Status":               "status",
    "Composite Score":      "composite_score",
    "composite_score":      "composite_score",
    "Price":                "price",
    "PS Now":               "ps_now",
    "ps_now":               "ps_now",
    "price_%_of_52w_high":  "price_pct_of_52w_high",
    "perf_52w_vs_spy":      "perf_52w_vs_spy",
    "Perf 52W vs SPY":      "perf_52w_vs_spy",
    "Rating":               "rating",
    "Short Outlook":        "short_outlook",
    "R40 Score":            "r40_score",
    "AI":                   "ai",
    "Analyzed":             "ai",
    "AI Analyzed":          "ai",
    "Data":                 "data",
    "Data As Of":           "data",
    "Fundamentals Date":    "data",
    "Scoring":              "scoring",
    "scoring":              "scoring",
    "Bear":                 "bear",
    "Bear Eval":            "bear",
    "Bull":                 "bull",
    "Bull Eval":            "bull",
    "12m_median":           "12m_median",
    "Quarterly Revenue":    "quarterly_revenue",
    "quarterly_revenue":    "quarterly_revenue",
}

# Columns to copy from AI Analysis -> Portfolio
PORTFOLIO_COLUMNS = [
    "ticker",
    "exchange",
    "company_name",
    "sector",
    "description",
    "composite_score",
    "perf_52w_vs_spy",
    "price_pct_of_52w_high",
    "ps_now",       # will be replaced with GOOGLEFINANCE formula
    "12m_median",
    "bear",
    "bull",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"build_portfolio_{date.today().isoformat()}.txt"

    logger = logging.getLogger("build_portfolio")
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


def read_sheet(service, sheet_name, end_col="AZ"):
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


def _is_us_exchange(exchange_val):
    """Check if exchange is a US exchange (ADR-friendly)."""
    return exchange_val.strip().upper() in US_EXCHANGES


# ---------------------------------------------------------------------------
# Revenue parsing & GOOGLEFINANCE formula
# ---------------------------------------------------------------------------


def _parse_revenue_amount(s):
    """
    Parse a formatted revenue string like '$5.2B', '$480M', '$1.5K' into a float.
    Returns the value in dollars or None.
    """
    s = s.strip()
    match = re.match(r'^-?\$?([\d.]+)\s*([BMK])?$', s, re.IGNORECASE)
    if not match:
        return None
    num = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    if suffix == "B":
        return num * 1e9
    elif suffix == "M":
        return num * 1e6
    elif suffix == "K":
        return num * 1e3
    return num


def _compute_ttm_revenue(quarterly_revenue_str):
    """
    Parse quarterly_revenue column and sum last 4 quarters for TTM revenue.

    Format: "$5.2B (2026-03-31) | $4.8B (2025-12-31) | $4.5B (2025-09-30) | ..."
    Returns TTM revenue in dollars or None.
    """
    if not quarterly_revenue_str or quarterly_revenue_str == NULL_VALUE:
        return None

    parts = quarterly_revenue_str.split("|")
    revenues = []
    for part in parts[:4]:  # take first 4 (most recent)
        # Extract the dollar amount before the parenthesised date
        part = part.strip()
        amount_match = re.match(r'^(-?\$?[\d.]+[BMK]?)', part, re.IGNORECASE)
        if amount_match:
            rev = _parse_revenue_amount(amount_match.group(1))
            if rev is not None:
                revenues.append(rev)

    if len(revenues) < 4:
        return None  # not enough quarters for TTM

    return sum(revenues)


def _build_ps_formula(ticker, exchange, ttm_revenue):
    """
    Build a GOOGLEFINANCE formula for live P/S ratio.

    =GOOGLEFINANCE("EXCHANGE:TICKER","marketcap") / TTM_REVENUE
    """
    gf_exchange = TV_TO_GOOGLE_FINANCE.get(exchange.upper(), exchange.upper())
    gf_ticker = f"{gf_exchange}:{ticker}"
    # Round TTM revenue to avoid floating point noise in formula
    ttm_int = round(ttm_revenue)
    return f'=GOOGLEFINANCE("{gf_ticker}","marketcap")/{ttm_int}'


def _google_finance_url(ticker: str, exchange: str) -> str:
    """Build a Google Finance URL for a ticker."""
    gf_exchange = TV_TO_GOOGLE_FINANCE.get(exchange.upper(), exchange)
    return f"https://www.google.com/finance/quote/{ticker}:{gf_exchange}"


def _ticker_hyperlink(ticker: str, exchange: str) -> str:
    """Build a =HYPERLINK() formula pointing to Google Finance."""
    url = _google_finance_url(ticker, exchange)
    return f'=HYPERLINK("{url}", "{ticker}")'


def _get_sheet_gid(service, sheet_name: str) -> int:
    """Return the numeric sheetId (gid) for a named sheet tab."""
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == sheet_name:
            return sheet["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{sheet_name}' not found")


def _company_name_hyperlink(company_name: str, gid: int, sheet_row: int) -> str:
    """Build a =HYPERLINK() formula linking to the AI Analysis row."""
    safe_name = company_name.replace('"', '""')
    return f'=HYPERLINK("#gid={gid}&range=A{sheet_row}", "{safe_name}")'


# ---------------------------------------------------------------------------
# Company name normalisation & fuzzy matching
# ---------------------------------------------------------------------------


def _normalise_company(name):
    """
    Normalise a company name for dedup comparison.
    """
    s = name.strip().upper()
    s = CORPORATE_SUFFIXES.sub("", s)
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _names_match(name_a, name_b):
    """Check if two company names refer to the same company."""
    norm_a = _normalise_company(name_a)
    norm_b = _normalise_company(name_b)

    if norm_a == norm_b:
        return True

    if norm_a and norm_b:
        shorter, longer = sorted([norm_a, norm_b], key=len)
        if longer.startswith(shorter) and len(shorter) >= 3:
            return True

    ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
    return ratio >= FUZZY_THRESHOLD


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _pick_best(candidates, exchange_idx, score_idx, logger):
    """From a list of candidates for the same company, pick the best one."""
    if len(candidates) == 1:
        return candidates[0]

    tickers = [t for t, _, _ in candidates]
    names = [p[2].strip() if len(p) > 2 else "?" for _, p, _ in candidates]
    logger.info("  Dedup: [%s] tickers: %s", names[0], tickers)

    us_candidates = [
        (t, p, sr) for t, p, sr in candidates
        if exchange_idx is not None and _is_us_exchange(p[exchange_idx])
    ]

    pool = us_candidates if us_candidates else candidates
    best = max(
        pool,
        key=lambda x: _safe_float(x[1][score_idx]) or 0.0 if score_idx is not None else 0.0,
    )
    label = "ADR" if us_candidates else "best score"
    logger.info("    -> Picked %s: %s (%s)", label, best[0],
                best[1][exchange_idx].strip() if exchange_idx is not None else "?")
    return best


def deduplicate_by_company(entries, exchange_idx, company_idx, score_idx, logger):
    """
    Deduplicate entries by company name using fuzzy matching.
    """
    if company_idx is None:
        return entries

    items = []
    for ticker, padded, sheet_row in entries:
        raw_name = padded[company_idx].strip() if company_idx < len(padded) else ""
        norm = _normalise_company(raw_name) if raw_name else ticker
        items.append((ticker, padded, sheet_row, raw_name, norm))

    groups = []
    assigned = [False] * len(items)

    for i in range(len(items)):
        if assigned[i]:
            continue
        group = [items[i]]
        assigned[i] = True
        for j in range(i + 1, len(items)):
            if assigned[j]:
                continue
            if _names_match(items[i][3], items[j][3]):
                group.append(items[j])
                assigned[j] = True
        groups.append(group)

    deduped = []
    for group in groups:
        candidates = [(t, p, sr) for t, p, sr, _, _ in group]
        best = _pick_best(candidates, exchange_idx, score_idx, logger)
        deduped.append(best)

    return deduped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Build Portfolio from dual-positive equities")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without writing to the sheet")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== Build Portfolio started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    service = get_sheets_service()

    # ---------------------------------------------------------------
    # Read AI Analysis
    # ---------------------------------------------------------------
    all_rows = read_sheet(service, AI_ANALYSIS_SHEET)
    logger.info("Read %d rows from AI Analysis (including headers)", len(all_rows))

    if len(all_rows) < 3:
        logger.error("AI Analysis has fewer than 3 rows")
        sys.exit(1)

    # Build column map from row 2 headers
    col_map = {}
    for idx, header in enumerate(all_rows[1]):
        name = header.strip()
        name = HEADER_ALIASES.get(name, name.lower())
        col_map[name] = idx
    logger.info("AI Analysis column map: %s", {k: v for k, v in col_map.items()})

    # Verify required columns exist
    bear_idx = col_map.get("bear")
    bull_idx = col_map.get("bull")
    ticker_idx = col_map.get("ticker")
    exchange_idx = col_map.get("exchange")
    company_idx = col_map.get("company_name")
    score_idx = col_map.get("composite_score")
    qrev_idx = col_map.get("quarterly_revenue")

    if bear_idx is None:
        logger.error("'bear' column not found in AI Analysis")
        sys.exit(1)
    if bull_idx is None:
        logger.error("'bull' column not found in AI Analysis")
        sys.exit(1)
    if ticker_idx is None:
        logger.error("'ticker' column not found in AI Analysis")
        sys.exit(1)
    if qrev_idx is not None:
        logger.info("quarterly_revenue column found at index %d", qrev_idx)
    else:
        logger.warning("quarterly_revenue column not found — ps_now will use static values")

    # ---------------------------------------------------------------
    # Find dual-positive equities (both bear and bull have ✅)
    # ---------------------------------------------------------------
    max_idx = max(col_map.values())
    dual_positive = []

    for row_offset, row in enumerate(all_rows[2:]):
        padded = row + [""] * (max_idx + 1 - len(row))
        bear_val = padded[bear_idx].strip()
        bull_val = padded[bull_idx].strip()

        if "\u2705" in bear_val and "\u2705" in bull_val:
            ticker = _extract_ticker(padded[ticker_idx])
            if not ticker:
                continue
            sheet_row = row_offset + 3  # 2 header rows, 1-indexed
            dual_positive.append((ticker, padded, sheet_row))

    logger.info("Found %d equities with both Bear \u2705 and Bull \u2705", len(dual_positive))

    if not dual_positive:
        logger.warning("No dual-positive equities found. Portfolio will be cleared.")

    # ---------------------------------------------------------------
    # Deduplicate by company name (fuzzy match, favour ADR / US listing)
    # ---------------------------------------------------------------
    if dual_positive and company_idx is not None:
        before_count = len(dual_positive)
        dual_positive = deduplicate_by_company(
            dual_positive, exchange_idx, company_idx, score_idx, logger,
        )
        after_count = len(dual_positive)
        if before_count != after_count:
            logger.info("Deduplicated: %d -> %d (removed %d duplicates)",
                        before_count, after_count, before_count - after_count)

    # Sort by composite_score descending
    if score_idx is not None:
        dual_positive.sort(
            key=lambda x: _safe_float(x[1][score_idx]) or 0.0,
            reverse=True,
        )

    # ---------------------------------------------------------------
    # Build Portfolio rows
    # ---------------------------------------------------------------
    ai_gid = _get_sheet_gid(service, AI_ANALYSIS_SHEET)

    portfolio_rows = []
    for ticker, padded, sheet_row in dual_positive:
        exchange = padded[exchange_idx].strip() if exchange_idx is not None else ""

        # Compute TTM revenue for live P/S formula
        ttm_revenue = None
        if qrev_idx is not None:
            qrev_str = padded[qrev_idx].strip() if qrev_idx < len(padded) else ""
            ttm_revenue = _compute_ttm_revenue(qrev_str)

        row_data = []
        for col_key in PORTFOLIO_COLUMNS:
            if col_key == "ticker":
                row_data.append(
                    _ticker_hyperlink(ticker, exchange) if exchange else ticker
                )
            elif col_key == "company_name":
                company_name = ""
                src_idx = col_map.get(col_key)
                if src_idx is not None and src_idx < len(padded):
                    company_name = padded[src_idx].strip()
                row_data.append(
                    _company_name_hyperlink(company_name, ai_gid, sheet_row)
                    if company_name else ""
                )
            elif col_key == "ps_now" and ttm_revenue is not None and ttm_revenue > 0:
                # Write live GOOGLEFINANCE formula instead of static value
                formula = _build_ps_formula(ticker, exchange, ttm_revenue)
                row_data.append(formula)
                logger.info("    %s: live P/S formula (TTM rev: $%.0f)", ticker, ttm_revenue)
            else:
                src_idx = col_map.get(col_key)
                if src_idx is not None and src_idx < len(padded):
                    row_data.append(padded[src_idx].strip())
                else:
                    row_data.append("")
        portfolio_rows.append(row_data)
        logger.info("  %s (%s) score: %s", ticker, exchange,
                    padded[score_idx].strip() if score_idx is not None else "?")

    if args.dry_run:
        logger.info("[DRY RUN] Would write %d rows to Portfolio sheet", len(portfolio_rows))
        for row in portfolio_rows:
            logger.info("  %s", row[:3] + [row[8]] if len(row) > 8 else row)
        logger.info("[DRY RUN] Complete. No writes performed.")
        return

    # ---------------------------------------------------------------
    # Write to Portfolio sheet
    # ---------------------------------------------------------------
    portfolio_data = read_sheet(service, PORTFOLIO_SHEET, end_col="L")
    existing_rows = len(portfolio_data)
    logger.info("Portfolio sheet currently has %d rows (including header)", existing_rows)

    # Clear old data (rows 2+)
    if existing_rows > 1:
        clear_range = f"'{PORTFOLIO_SHEET}'!A2:L{existing_rows}"
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range=clear_range,
        ).execute()
        logger.info("Cleared %d existing data rows", existing_rows - 1)

    # Write new data starting at row 2
    if portfolio_rows:
        write_range = f"'{PORTFOLIO_SHEET}'!A2:L{len(portfolio_rows) + 1}"
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=write_range,
            valueInputOption="USER_ENTERED",
            body={"values": portfolio_rows},
        ).execute()
        logger.info("Wrote %d rows to Portfolio sheet", len(portfolio_rows))
    else:
        logger.info("No rows to write (portfolio is empty)")

    elapsed = time.time() - start_time
    logger.info(
        "=== Build Portfolio complete. %d equities written. (%.1fs) ===",
        len(portfolio_rows), elapsed,
    )


if __name__ == "__main__":
    main()
