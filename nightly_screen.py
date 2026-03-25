#!/usr/bin/env python3
"""
Nightly TradingView Screen → AI Analysis Ingest.

Runs the TradingView screener, loads Manual sheet tickers, and adds any new
tickers to the AI Analysis sheet. Also updates country/sector for existing
tickers if missing.

Schedule: 04:30 UTC daily (before eodhd_updater at 05:00).
"""

import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from tv_screen import run_tradingview_screen

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
AI_ANALYSIS_SHEET = "AI Analysis"
MANUAL_SHEET = "Manual"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Map alternative/legacy header names → canonical lowercase keys.
HEADER_ALIASES = {
    "Ticker": "ticker",
    "ticker_clean": "ticker",
    "Company": "company_name",
    "Company Name": "company_name",
    "Exchange": "exchange",
    "Country": "country",
    "Sector": "sector",
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"nightly_screen_{date.today().isoformat()}.txt"

    logger = logging.getLogger("nightly_screen")
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
# Load existing AI Analysis tickers
# ---------------------------------------------------------------------------


def load_ai_analysis_tickers(service, logger) -> tuple[set[str], dict[str, int], dict[str, dict]]:
    """
    Read AI Analysis sheet.
    Returns:
        existing_tickers: set of uppercase ticker strings
        col_map: {header_name: col_index}
        ticker_rows: {ticker: {"row": 1-indexed, "country": str, "sector": str, ...}}
    """
    rows = read_sheet(service, AI_ANALYSIS_SHEET)
    if len(rows) < 2:
        logger.warning("AI Analysis has fewer than 2 rows")
        return set(), {}, {}

    # Row 1 = group headers, Row 2 = column headers, Row 3+ = data
    raw_headers = [str(h).strip() for h in rows[1]]
    col_map = {}
    for idx, h in enumerate(raw_headers):
        key = HEADER_ALIASES.get(h, h.lower())
        col_map[key] = idx

    ticker_idx = col_map.get("ticker")
    if ticker_idx is None:
        logger.error("Cannot find 'ticker' column in AI Analysis headers: %s", raw_headers)
        return set(), col_map, {}

    existing = set()
    ticker_rows = {}
    for row_offset, row in enumerate(rows[2:]):
        padded = row + [""] * (max(col_map.values()) + 1 - len(row))
        ticker = _extract_ticker(padded[ticker_idx])
        if not ticker:
            continue
        existing.add(ticker)
        sheet_row = row_offset + 3  # 1-indexed (row 1=groups, 2=headers, 3+=data)
        ticker_rows[ticker] = {
            "row": sheet_row,
            "country": padded[col_map["country"]].strip() if "country" in col_map else "",
            "sector": padded[col_map["sector"]].strip() if "sector" in col_map else "",
            "exchange": padded[col_map["exchange"]].strip() if "exchange" in col_map else "",
        }

    logger.info("AI Analysis has %d existing tickers", len(existing))
    return existing, col_map, ticker_rows


# ---------------------------------------------------------------------------
# Load Manual sheet tickers
# ---------------------------------------------------------------------------


def load_manual_tickers(service, logger) -> list[dict]:
    """Read the Manual sheet and return equity dicts."""
    try:
        rows = read_sheet(service, MANUAL_SHEET, end_col="Z")
    except Exception as e:
        logger.info("Manual sheet not readable (may not exist): %s", e)
        return []

    if len(rows) < 2:
        logger.info("Manual sheet has fewer than 2 rows")
        return []

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

    # Normalize headers
    headers = [HEADER_ALIASES.get(h, h) if h[0:1].isupper() else
               HEADER_ALIASES.get(h, h) for h in headers]

    hmap = {h: i for i, h in enumerate(headers)}
    ticker_idx = hmap.get("ticker")
    if ticker_idx is None:
        logger.warning("Manual sheet has no 'ticker' column — headers: %s", headers)
        return []

    company_idx = hmap.get("company_name") or hmap.get("company")
    exchange_idx = hmap.get("exchange")
    country_idx = hmap.get("country")
    sector_idx = hmap.get("sector")

    manual = []
    for row in data_rows:
        padded = row + [""] * (max(hmap.values()) + 1 - len(row))
        ticker = padded[ticker_idx].strip().upper()
        if not ticker:
            continue
        manual.append({
            "ticker": ticker,
            "exchange": padded[exchange_idx].strip() if exchange_idx is not None else "",
            "company_name": padded[company_idx].strip() if company_idx is not None else "",
            "country": padded[country_idx].strip() if country_idx is not None else "",
            "sector": padded[sector_idx].strip() if sector_idx is not None else "",
        })

    logger.info("Loaded %d Manual tickers", len(manual))
    return manual


# ---------------------------------------------------------------------------
# Add new tickers + update missing fields
# ---------------------------------------------------------------------------


def add_new_tickers(service, new_tickers: list[dict], col_map: dict, logger):
    """Append new ticker rows to AI Analysis sheet."""
    if not new_tickers:
        logger.info("No new tickers to add")
        return

    # Build rows matching the column layout
    max_col = max(col_map.values()) + 1
    append_rows = []
    for eq in new_tickers:
        row = [""] * max_col
        if "ticker" in col_map:
            row[col_map["ticker"]] = eq["ticker"]
        if "exchange" in col_map:
            row[col_map["exchange"]] = eq.get("exchange", "")
        if "company_name" in col_map:
            row[col_map["company_name"]] = eq.get("company_name", "")
        if "country" in col_map:
            row[col_map["country"]] = eq.get("country", "")
        if "sector" in col_map:
            row[col_map["sector"]] = eq.get("sector", "")
        append_rows.append(row)

    end_col = _col_letter(max_col - 1)
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{AI_ANALYSIS_SHEET}'!A3:{end_col}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": append_rows},
    ).execute()

    logger.info("Appended %d new tickers to AI Analysis", len(append_rows))


def update_missing_fields(service, updates: list[dict], col_map: dict, logger):
    """Fill in country/sector for existing tickers where those fields are empty."""
    if not updates:
        return

    data = []
    for upd in updates:
        row_num = upd["row"]
        for field, value in upd["fields"].items():
            if field in col_map and value:
                col_letter = _col_letter(col_map[field])
                data.append({
                    "range": f"'{AI_ANALYSIS_SHEET}'!{col_letter}{row_num}",
                    "values": [[value]],
                })

    if not data:
        return

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    logger.info("Updated missing fields for %d existing tickers", len(updates))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Nightly Screen — %s", date.today().isoformat())
    logger.info("=" * 60)

    # Step 1: TradingView screen
    screened = run_tradingview_screen(logger)
    logger.info("TradingView returned %d equities", len(screened))

    # Step 2: Load Manual tickers
    service = get_sheets_service()
    manual = load_manual_tickers(service, logger)

    # Merge screened + manual (screened takes priority for duplicates)
    all_equities = {}
    for eq in screened:
        all_equities[eq["ticker"].upper()] = eq
    for eq in manual:
        t = eq["ticker"].upper()
        if t not in all_equities:
            all_equities[t] = eq

    logger.info("Combined universe: %d equities (%d screened + %d manual, deduplicated)",
                len(all_equities), len(screened), len(manual))

    # Step 3: Read existing AI Analysis
    existing_tickers, col_map, ticker_rows = load_ai_analysis_tickers(service, logger)

    if not col_map:
        logger.error("Could not read AI Analysis column map — aborting")
        return

    # Step 4: Identify new tickers and missing fields
    new_tickers = []
    field_updates = []

    for ticker, eq in all_equities.items():
        if ticker not in existing_tickers:
            new_tickers.append(eq)
        else:
            # Check if country/sector are missing and we have them
            existing = ticker_rows.get(ticker, {})
            missing_fields = {}
            if not existing.get("country") and eq.get("country"):
                missing_fields["country"] = eq["country"]
            if not existing.get("sector") and eq.get("sector"):
                missing_fields["sector"] = eq["sector"]
            if not existing.get("exchange") and eq.get("exchange"):
                missing_fields["exchange"] = eq["exchange"]
            if missing_fields:
                field_updates.append({
                    "row": existing["row"],
                    "fields": missing_fields,
                })

    logger.info("New tickers to add: %d", len(new_tickers))
    logger.info("Existing tickers with missing fields to update: %d", len(field_updates))

    # Step 5: Write changes
    add_new_tickers(service, new_tickers, col_map, logger)
    update_missing_fields(service, field_updates, col_map, logger)

    # Log new tickers
    if new_tickers:
        for eq in sorted(new_tickers, key=lambda e: e["ticker"]):
            logger.info("  NEW: %s (%s) — %s / %s",
                        eq["ticker"], eq.get("exchange", ""),
                        eq.get("sector", ""), eq.get("country", ""))

    logger.info("Nightly screen complete")


if __name__ == "__main__":
    main()
