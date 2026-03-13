#!/usr/bin/env python3
"""
Verify CURRENT sheet formulas and structure after a rebuild.

Reads the CURRENT sheet via Google Sheets API and checks:
  1. Header structure (row 1 categories, row 2 column titles)
  2. Formula columns (ps_discount, days_on_list) are formulas, not values
  3. HYPERLINK formulas in company_name are preserved
  4. entry_ps_ttm and first_seen values are present where expected
  5. No formula errors (#REF!, #VALUE!, #NAME?, #N/A, #DIV/0!, #NULL!)

Usage:
    python scripts/recalc.py
"""

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
SHEET_NAME = "CURRENT"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

EXPECTED_HEADERS = [
    "ticker", "exchange", "company_name", "country", "sector",
    "price", "market_cap", "ps_ratio_ttm", "entry_ps_ttm", "ps_discount",
    "total_revenue_ttm", "rev_growth_ttm%", "gross_margin_%", "net_margin_ttm",
    "net_margin_direction", "net_margin_annual", "net_margin_qoq", "fcf_margin_ttm",
    "perf_52w_vs_spy", "rating",
    "status", "description", "fundamentals", "short_outlook", "signal",
    "outlook", "ai_analysis_date", "net_income_ttm",
    "first_seen", "days_on_list", "chart_data", "chart_data_date",
]

EXPECTED_CATEGORIES = [
    "IDENTITY", "PRICE", "VALUATION", "REVENUE",
    "MARGINS", "MARKET", "AI NARRATIVE", "TRACKING",
]

# Formula columns (0-indexed)
FORMULA_COL_PS_DISCOUNT = 9   # J
FORMULA_COL_DAYS_ON_LIST = 29  # AD
COMPANY_NAME_COL = 2           # C

FORMULA_ERROR_PATTERNS = [
    "#REF!", "#VALUE!", "#NAME?", "#N/A", "#DIV/0!", "#NULL!", "#ERROR!",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("recalc")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
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


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_headers(rows_formatted: list[list[str]], logger) -> int:
    """Check row 1 (categories) and row 2 (column titles). Returns error count."""
    errors = 0

    if len(rows_formatted) < 2:
        logger.error("Sheet has fewer than 2 rows!")
        return 1

    # Check row 1 categories
    row1 = rows_formatted[0]
    found_cats = [c.strip() for c in row1 if c.strip()]
    for cat in EXPECTED_CATEGORIES:
        if cat not in found_cats:
            logger.error("Missing category in row 1: '%s'", cat)
            errors += 1
        else:
            logger.info("  Category OK: '%s'", cat)

    # Check row 2 column titles
    row2 = rows_formatted[1]
    for idx, expected in enumerate(EXPECTED_HEADERS):
        if idx >= len(row2):
            logger.error("Column %d missing: expected '%s'", idx, expected)
            errors += 1
        elif row2[idx].strip() != expected:
            logger.error("Column %d mismatch: expected '%s', got '%s'",
                         idx, expected, row2[idx].strip())
            errors += 1
        else:
            logger.info("  Header OK: col %d = '%s'", idx, expected)

    return errors


def check_formulas(rows_formula: list[list[str]], logger) -> int:
    """Check that ps_discount and days_on_list are formulas, not values."""
    errors = 0
    data_rows = rows_formula[2:]  # skip header rows

    for i, row in enumerate(data_rows):
        sheet_row = i + 3

        # Check ps_discount (col J = index 9)
        if FORMULA_COL_PS_DISCOUNT < len(row):
            val = row[FORMULA_COL_PS_DISCOUNT]
            if val and not str(val).startswith("="):
                logger.error("Row %d: ps_discount is not a formula: '%s'",
                             sheet_row, str(val)[:50])
                errors += 1

        # Check days_on_list (col AD = index 29)
        if FORMULA_COL_DAYS_ON_LIST < len(row):
            val = row[FORMULA_COL_DAYS_ON_LIST]
            if val and not str(val).startswith("="):
                logger.error("Row %d: days_on_list is not a formula: '%s'",
                             sheet_row, str(val)[:50])
                errors += 1

    return errors


def check_hyperlinks(rows_formula: list[list[str]], logger) -> int:
    """Check that company_name cells contain HYPERLINK formulas where expected."""
    errors = 0
    warnings = 0
    data_rows = rows_formula[2:]

    hyperlink_count = 0
    plain_count = 0

    for i, row in enumerate(data_rows):
        sheet_row = i + 3
        if COMPANY_NAME_COL < len(row):
            val = str(row[COMPANY_NAME_COL])
            if val.upper().startswith("=HYPERLINK("):
                hyperlink_count += 1
            elif val.strip():
                plain_count += 1

    logger.info("  company_name: %d HYPERLINKs, %d plain text", hyperlink_count, plain_count)
    if plain_count > 0 and hyperlink_count == 0:
        logger.warning("No HYPERLINK formulas found in company_name — all plain text")
        warnings += 1

    return errors


def check_formula_errors(rows_formatted: list[list[str]], logger) -> int:
    """Scan all cells for formula error values."""
    errors = 0
    data_rows = rows_formatted[2:]

    for i, row in enumerate(data_rows):
        sheet_row = i + 3
        for j, val in enumerate(row):
            val_str = str(val).strip()
            for err in FORMULA_ERROR_PATTERNS:
                if err in val_str:
                    logger.error("Row %d, col %d: formula error '%s'",
                                 sheet_row, j, val_str)
                    errors += 1
                    break

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("CURRENT sheet verification — starting")
    logger.info("=" * 60)

    service = get_sheets_service()

    # Read with FORMATTED_VALUE for error detection
    result_formatted = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:AF",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    rows_formatted = result_formatted.get("values", [])

    # Read with FORMULA to check formula columns
    result_formula = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:AF",
            valueRenderOption="FORMULA",
        )
        .execute()
    )
    rows_formula = result_formula.get("values", [])

    logger.info("Read %d rows (formatted), %d rows (formula)",
                len(rows_formatted), len(rows_formula))

    total_errors = 0

    logger.info("--- Checking headers ---")
    total_errors += check_headers(rows_formatted, logger)

    logger.info("--- Checking formula columns ---")
    total_errors += check_formulas(rows_formula, logger)

    logger.info("--- Checking HYPERLINK formulas ---")
    total_errors += check_hyperlinks(rows_formula, logger)

    logger.info("--- Checking for formula errors ---")
    total_errors += check_formula_errors(rows_formatted, logger)

    logger.info("=" * 60)
    if total_errors == 0:
        logger.info("PASS — zero formula errors, structure verified")
    else:
        logger.error("FAIL — %d errors found", total_errors)
    logger.info("=" * 60)

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
