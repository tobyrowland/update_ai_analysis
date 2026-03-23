#!/usr/bin/env python3
"""
Sync new companies from the 'CURRENT' sheet to the 'AI Analysis' sheet.

Reads tickers from both sheets, identifies companies present in CURRENT
but missing from AI Analysis, and appends them to AI Analysis.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SOURCE_SHEET = "CURRENT"
MANUAL_SHEET = "Manual"
TARGET_SHEET = "AI Analysis"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"sync_companies_{today_str}.txt"

    logger = logging.getLogger("sync_companies")
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


def read_sheet_rows(service, sheet_name: str) -> list[list[str]]:
    """Return all rows from a sheet."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!A1:ZZ",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s)."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def find_column(headers: list[str], *names: str) -> int | None:
    """Find a column index by trying multiple header names (case-insensitive)."""
    for idx, header in enumerate(headers):
        normalized = header.strip().lower()
        for name in names:
            if normalized == name.lower():
                return idx
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sync companies from CURRENT to AI Analysis")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without writing")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== Sync Companies started (dry_run=%s) ===", args.dry_run)

    service = get_sheets_service()

    # --- Read SOURCE (CURRENT) sheet ---
    source_rows = read_sheet_rows(service, SOURCE_SHEET)
    if len(source_rows) < 1:
        logger.error("Source sheet '%s' is empty", SOURCE_SHEET)
        sys.exit(1)

    # Row 1 contains merged group headers (STATUS, IDENTITY, etc.);
    # the actual column headers (ticker, company_name, …) are in row 2.
    if len(source_rows) < 2:
        logger.error("Source sheet '%s' has fewer than 2 rows", SOURCE_SHEET)
        sys.exit(1)
    source_headers = source_rows[1]
    src_ticker_col = find_column(source_headers, "ticker")
    src_company_col = find_column(source_headers, "company_name")
    src_exchange_col = find_column(source_headers, "exchange")

    if src_ticker_col is None:
        logger.error("Could not find 'ticker' column in %s headers: %s",
                      SOURCE_SHEET, source_headers)
        sys.exit(1)
    if src_company_col is None:
        logger.error("Could not find 'company_name' column in %s headers: %s",
                      SOURCE_SHEET, source_headers)
        sys.exit(1)
    if src_exchange_col is None:
        logger.error("Could not find 'exchange' column in %s headers: %s",
                      SOURCE_SHEET, source_headers)
        sys.exit(1)

    logger.info("Source '%s': ticker=col %d, company_name=col %d, exchange=col %d",
                SOURCE_SHEET, src_ticker_col, src_company_col, src_exchange_col)

    # Extract source companies (data starts at row 3, index 2)
    source_companies = {}  # ticker → {company_name, exchange}
    for row in source_rows[2:]:
        max_col = max(src_ticker_col, src_company_col, src_exchange_col)
        padded = row + [""] * (max_col + 1 - len(row))
        ticker = padded[src_ticker_col].strip().upper()
        company = padded[src_company_col].strip()
        exchange = padded[src_exchange_col].strip()
        if ticker:
            source_companies[ticker] = {"company_name": company, "exchange": exchange}

    logger.info("Found %d companies in '%s'", len(source_companies), SOURCE_SHEET)

    # --- Read MANUAL sheet ---
    try:
        manual_rows = read_sheet_rows(service, MANUAL_SHEET)
        if len(manual_rows) >= 2:
            manual_headers = [str(h).strip().lower() for h in manual_rows[0]]
            man_ticker_col = find_column(manual_headers, "ticker")
            man_company_col = find_column(manual_headers, "company_name", "company", "company name")
            man_exchange_col = find_column(manual_headers, "exchange")

            if man_ticker_col is not None:
                for row in manual_rows[1:]:
                    max_col = max(c for c in [man_ticker_col, man_company_col, man_exchange_col] if c is not None)
                    padded = row + [""] * (max_col + 1 - len(row))
                    ticker = padded[man_ticker_col].strip().upper()
                    if ticker and ticker not in source_companies:
                        source_companies[ticker] = {
                            "company_name": padded[man_company_col].strip() if man_company_col is not None else "",
                            "exchange": padded[man_exchange_col].strip() if man_exchange_col is not None else "",
                        }
                logger.info("After Manual sheet: %d total companies", len(source_companies))
            else:
                logger.info("Manual sheet has no 'ticker' column — skipping")
        else:
            logger.info("Manual sheet has fewer than 2 rows — skipping")
    except Exception as e:
        logger.info("Manual sheet not readable (may not exist): %s", e)

    # --- Read TARGET (AI Analysis) sheet ---
    target_rows = read_sheet_rows(service, TARGET_SHEET)
    if len(target_rows) < 2:
        logger.error("Target sheet '%s' has fewer than 2 rows", TARGET_SHEET)
        sys.exit(1)

    # Headers in row 2 (index 1)
    target_headers = target_rows[1] if len(target_rows) >= 2 else target_rows[0]
    tgt_ticker_col = find_column(target_headers, "ticker")
    tgt_company_col = find_column(target_headers, "company_name", "company", "company name")
    tgt_exchange_col = find_column(target_headers, "exchange")

    if tgt_ticker_col is None:
        logger.error("Could not find 'Ticker' column in %s headers: %s",
                      TARGET_SHEET, target_headers)
        sys.exit(1)
    if tgt_company_col is None:
        logger.error("Could not find 'Company' / 'Company Name' column in %s headers: %s",
                      TARGET_SHEET, target_headers)
        sys.exit(1)
    if tgt_exchange_col is None:
        logger.error("Could not find 'Exchange' column in %s headers: %s",
                      TARGET_SHEET, target_headers)
        sys.exit(1)

    logger.info("Target '%s': Ticker=col %d, Company=col %d, Exchange=col %d",
                TARGET_SHEET, tgt_ticker_col, tgt_company_col, tgt_exchange_col)

    # Extract existing tickers in AI Analysis
    existing_tickers = set()
    for row in target_rows[2:]:
        padded = row + [""] * (tgt_ticker_col + 1 - len(row))
        ticker = padded[tgt_ticker_col].strip().upper()
        if ticker:
            existing_tickers.add(ticker)

    logger.info("Found %d existing companies in '%s'", len(existing_tickers), TARGET_SHEET)

    # --- Find new companies ---
    new_companies = {
        ticker: info
        for ticker, info in source_companies.items()
        if ticker not in existing_tickers
    }

    if not new_companies:
        logger.info("No new companies to add — all CURRENT tickers already exist in AI Analysis.")
        return

    logger.info("Found %d new companies to add:", len(new_companies))
    for ticker, info in sorted(new_companies.items()):
        logger.info("  %s — %s (%s)", ticker, info["company_name"], info["exchange"])

    if args.dry_run:
        logger.info("=== DRY RUN complete — no changes made ===")
        return

    # --- Append new rows to AI Analysis ---
    # Build rows with ticker, company, and exchange in the correct columns
    num_cols = len(target_headers)
    append_rows = []
    for ticker, info in sorted(new_companies.items()):
        new_row = [""] * num_cols
        new_row[tgt_ticker_col] = ticker
        new_row[tgt_company_col] = info["company_name"]
        new_row[tgt_exchange_col] = info["exchange"]
        append_rows.append(new_row)

    # Append after the last row
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TARGET_SHEET}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": append_rows},
    ).execute()

    logger.info("Appended %d new companies to '%s'", len(append_rows), TARGET_SHEET)

    # Clear background colors on data rows (new rows may inherit header colors)
    from eodhd_updater import clear_data_row_formatting
    clear_data_row_formatting(service, logger)

    logger.info("=== Sync complete ===")


if __name__ == "__main__":
    main()
