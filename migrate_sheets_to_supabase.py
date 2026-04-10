#!/usr/bin/env python3
"""
One-time migration: Google Sheets → Supabase.

Reads the AI Analysis and Price-Sales sheets, transforms the data, and inserts
it into Supabase tables.  Run once, then delete this script.

Usage:
    python migrate_sheets_to_supabase.py
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

from db import SupabaseDB, NULL_VALUE

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Map sheet internal column keys → DB column names.
# Keys not listed here are assumed to match directly.
SHEET_TO_DB = {
    "rev_growth_ttm":       "rev_growth_ttm_pct",
    "rev_growth_qoq":       "rev_growth_qoq_pct",
    "rev_cagr":             "rev_cagr_pct",
    "gross_margin":         "gross_margin_pct",
    "operating_margin":     "operating_margin_pct",
    "net_margin":           "net_margin_pct",
    "net_margin_yoy":       "net_margin_yoy_pct",
    "fcf_margin":           "fcf_margin_pct",
    "eps_yoy":              "eps_yoy_pct",
    "ai":                   "ai_analyzed_at",
    "data":                 "data_updated_at",
    "price_pct_of_52w_high": "price_pct_of_52w_high",
}

# Header aliases — maps display names to internal keys (merged from all scripts).
HEADER_ALIASES = {
    "Ticker": "ticker", "ticker_clean": "ticker",
    "Company": "company_name", "Company Name": "company_name",
    "Exchange": "exchange", "Country": "country", "Sector": "sector",
    "Description": "description",
    "Status": "status", "Composite Score": "composite_score",
    "Price": "price", "PS Now": "ps_now",
    "price_%_of_52w_high": "price_pct_of_52w_high",
    "Perf 52W vs SPY": "perf_52w_vs_spy", "Rating": "rating",
    "Annual Revenue (5Y)": "annual_revenue_5y",
    "Quarterly Revenue": "quarterly_revenue",
    "Rev Growth TTM %": "rev_growth_ttm", "Rev Growth QoQ %": "rev_growth_qoq",
    "Rev CAGR 3Y %": "rev_cagr", "Rev Consistency Score": "rev_consistency_score",
    "Gross Margin %": "gross_margin", "GM Trend (Qtly)": "gm_trend",
    "Operating Margin %": "operating_margin",
    "Net Margin %": "net_margin", "Net Margin YoY Δ": "net_margin_yoy",
    "FCF Margin %": "fcf_margin",
    "Opex % of Revenue": "opex_pct_revenue",
    "S&M+R&D % of Revenue": "sm_rd_pct_revenue",
    "Rule of 40": "rule_of_40", "Qtrs to Profitability": "qrtrs_to_profitability",
    "EPS Qtrly": "eps_only", "EPS YoY %": "eps_yoy",
    "R40 Score": "r40_score", "Fundamentals Snapshot": "fundamentals_snapshot",
    "Short Outlook": "short_outlook",
    "Outlook": "full_outlook", "Full Outlook": "full_outlook",
    "Key Risks": "key_risks",
    "One-Time Events": "one_time_events", "One Time Events": "one_time_events",
    "AI": "ai", "Analyzed": "ai", "AI Analyzed": "ai",
    "Data": "data", "Data As Of": "data", "Fundamentals Date": "data",
    "Scoring": "scoring",
}

# Numeric columns in the DB (values must be parsed as float).
NUMERIC_COLS = {
    "composite_score", "price", "ps_now", "price_pct_of_52w_high",
    "perf_52w_vs_spy", "rating",
    "rev_growth_ttm_pct", "rev_growth_qoq_pct", "rev_cagr_pct",
    "gross_margin_pct", "operating_margin_pct", "net_margin_pct",
    "net_margin_yoy_pct", "fcf_margin_pct",
    "opex_pct_revenue", "sm_rd_pct_revenue", "rule_of_40",
    "eps_only", "eps_yoy_pct",
}

# Columns that can carry red/yellow emoji flags on numeric values.
FLAGGABLE_COLS = {
    "gross_margin_pct", "operating_margin_pct", "net_margin_pct",
    "fcf_margin_pct", "rule_of_40", "rev_growth_ttm_pct",
    "rev_growth_qoq_pct", "eps_yoy_pct", "opex_pct_revenue",
    "sm_rd_pct_revenue",
}

# Price-Sales sheet column mapping (header → db column).
PS_HEADER_MAP = {
    "ticker": "ticker", "company_name": "company_name",
    "ps_now": "ps_now", "52w_high": "high_52w", "52w_low": "low_52w",
    "12m_median": "median_12m", "ath": "ath", "%_of_ath": "pct_of_ath",
    "history_json": "history_json",
    "last_updated": "last_updated", "first_recorded": "first_recorded",
}

logger = logging.getLogger("migrate")


# ---------------------------------------------------------------------------
# Google Sheets reader
# ---------------------------------------------------------------------------

def get_sheets_service():
    sa_value = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_value:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set")
    if sa_value.strip().startswith("{"):
        info = json.loads(sa_value)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(sa_value, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def read_sheet(service, sheet_name: str) -> list[list[str]]:
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!A1:AZ",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_ticker(val: str) -> str:
    val = str(val).strip()
    if not val:
        return ""
    match = re.search(r'=HYPERLINK\([^,]+,\s*"([^"]+)"\)', val)
    if match:
        return match.group(1).strip().upper()
    return val.strip().upper()


def safe_float(val) -> float | None:
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


def strip_emoji_flags(val: str) -> tuple[str, str | None]:
    """Strip leading red/yellow/green emoji flags from a value.

    Returns (cleaned_value, flag_level) where flag_level is 'red', 'yellow',
    'green', or None.
    """
    val = str(val).strip()
    if val.startswith("\U0001f534") or val.startswith("🔴"):
        return val.lstrip("🔴").strip(), "red"
    if val.startswith("\U0001f7e1") or val.startswith("🟡"):
        return val.lstrip("🟡").strip(), "yellow"
    if val.startswith("\U0001f7e2") or val.startswith("🟢"):
        return val.lstrip("🟢").strip(), "green"
    return val, None


# ---------------------------------------------------------------------------
# Migration: AI Analysis → companies table
# ---------------------------------------------------------------------------

def migrate_ai_analysis(service, db: SupabaseDB) -> int:
    logger.info("Reading AI Analysis sheet...")
    rows = read_sheet(service, "AI Analysis")
    if len(rows) < 3:
        logger.error("AI Analysis sheet has fewer than 3 rows")
        return 0

    # Row 1 = group headers, Row 2 = column headers, Row 3+ = data
    raw_headers = [str(h).strip() for h in rows[1]]
    col_map = {}
    for idx, h in enumerate(raw_headers):
        key = HEADER_ALIASES.get(h, h.lower().replace("%", "").replace(" ", "_"))
        col_map[key] = idx

    logger.info(f"Found {len(raw_headers)} columns, {len(rows) - 2} data rows")

    companies = []
    skipped = 0

    for row_idx, row in enumerate(rows[2:], start=3):
        # Extract ticker
        ticker_idx = col_map.get("ticker")
        if ticker_idx is None or ticker_idx >= len(row):
            skipped += 1
            continue
        ticker = extract_ticker(row[ticker_idx])
        if not ticker:
            skipped += 1
            continue

        def cell(key: str) -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        # Build company record
        company = {"ticker": ticker}
        flags = {}

        # Text columns — direct copy
        for col in ["exchange", "company_name", "country", "sector", "description",
                     "status", "r40_score", "fundamentals_snapshot", "short_outlook",
                     "annual_revenue_5y", "quarterly_revenue", "rev_consistency_score",
                     "gm_trend", "qrtrs_to_profitability",
                     "one_time_events", "full_outlook", "key_risks"]:
            db_col = SHEET_TO_DB.get(col, col)
            val = cell(col)
            if val == NULL_VALUE:
                val = ""
            company[db_col] = val

        # Numeric columns — parse float, extract flags
        for col in ["composite_score", "price", "ps_now", "price_pct_of_52w_high",
                     "perf_52w_vs_spy", "rating",
                     "rev_growth_ttm", "rev_growth_qoq", "rev_cagr",
                     "gross_margin", "operating_margin", "net_margin",
                     "net_margin_yoy", "fcf_margin",
                     "opex_pct_revenue", "sm_rd_pct_revenue", "rule_of_40",
                     "eps_only", "eps_yoy"]:
            db_col = SHEET_TO_DB.get(col, col)
            raw = cell(col)
            if not raw or raw == NULL_VALUE:
                company[db_col] = None
                continue

            # Check for emoji flags on flaggable columns
            if db_col in FLAGGABLE_COLS:
                cleaned, flag = strip_emoji_flags(raw)
                if flag:
                    flags[db_col] = flag
                company[db_col] = safe_float(cleaned)
            else:
                company[db_col] = safe_float(raw)

        # Date columns
        for col in ["ai", "data"]:
            db_col = SHEET_TO_DB.get(col, col)
            val = cell(col)
            if val and val != NULL_VALUE:
                company[db_col] = val  # already ISO format from sheet
            else:
                company[db_col] = None

        company["flags"] = json.dumps(flags) if flags else "{}"
        companies.append(company)

    # Batch upsert in chunks of 50
    total = 0
    for i in range(0, len(companies), 50):
        chunk = companies[i : i + 50]
        db.upsert_companies_batch(chunk)
        total += len(chunk)
        logger.info(f"  Upserted {total}/{len(companies)} companies")

    logger.info(f"AI Analysis migration complete: {len(companies)} migrated, {skipped} skipped")
    return len(companies)


# ---------------------------------------------------------------------------
# Migration: Price-Sales → price_sales table
# ---------------------------------------------------------------------------

def migrate_price_sales(service, db: SupabaseDB) -> int:
    logger.info("Reading Price-Sales sheet...")
    rows = read_sheet(service, "Price-Sales")
    if len(rows) < 2:
        logger.warning("Price-Sales sheet has fewer than 2 rows")
        return 0

    # Row 1 = headers, Row 2+ = data
    raw_headers = [str(h).strip().lower() for h in rows[0]]
    col_map = {}
    for idx, h in enumerate(raw_headers):
        col_map[h] = idx

    logger.info(f"Found {len(raw_headers)} columns, {len(rows) - 1} data rows")

    # Get existing tickers to avoid FK violations
    existing_tickers = db.get_all_tickers()

    ps_rows = []
    skipped = 0

    for row in rows[1:]:
        ticker_idx = col_map.get("ticker", 0)
        if ticker_idx >= len(row):
            skipped += 1
            continue
        ticker = str(row[ticker_idx]).strip().upper()
        if not ticker or ticker not in existing_tickers:
            skipped += 1
            continue

        def cell(header: str) -> str:
            idx = col_map.get(header)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx]).strip()

        ps_record = {"ticker": ticker}
        for sheet_col, db_col in PS_HEADER_MAP.items():
            if db_col == "ticker":
                continue
            val = cell(sheet_col)

            if db_col == "history_json":
                # Parse JSON string into actual JSON
                if val and val != NULL_VALUE:
                    try:
                        ps_record[db_col] = json.loads(val)
                    except json.JSONDecodeError:
                        ps_record[db_col] = []
                else:
                    ps_record[db_col] = []
            elif db_col in ("last_updated", "first_recorded"):
                ps_record[db_col] = val if val and val != NULL_VALUE else None
            elif db_col == "company_name":
                ps_record[db_col] = val if val != NULL_VALUE else ""
            else:
                # Numeric
                ps_record[db_col] = safe_float(val)

        ps_rows.append(ps_record)

    # Batch upsert
    total = 0
    for i in range(0, len(ps_rows), 50):
        chunk = ps_rows[i : i + 50]
        db.upsert_price_sales_batch(chunk)
        total += len(chunk)
        logger.info(f"  Upserted {total}/{len(ps_rows)} price-sales rows")

    logger.info(f"Price-Sales migration complete: {len(ps_rows)} migrated, {skipped} skipped")
    return len(ps_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info("=== Sheets → Supabase Migration ===")

    service = get_sheets_service()
    db = SupabaseDB()

    companies_count = migrate_ai_analysis(service, db)
    ps_count = migrate_price_sales(service, db)

    logger.info(f"Migration complete: {companies_count} companies, {ps_count} price-sales rows")


if __name__ == "__main__":
    main()
