#!/usr/bin/env python3
"""
Build Portfolio — Populate Portfolio sheet with dual-positive equities.

Reads AI Analysis sheet, finds equities where both Bear and Bull columns
have a ✅, deduplicates by company (favouring ADRs/US listings), and writes
them to the Portfolio sheet with relevant data.

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

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# US exchanges — ADRs and primary US listings live here
US_EXCHANGES = {
    "NYSE", "NASDAQ", "AMEX", "NYSEARCA", "BATS", "ARCA",
}

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
    "ps_now",
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
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_by_company(entries, exchange_idx, company_idx, score_idx, logger):
    """
    Deduplicate entries by company_name, favouring ADRs (US exchange listings).

    For each company_name, pick:
      1. The US-listed version (ADR) if available
      2. Otherwise the one with the highest composite_score

    entries: list of (ticker, padded_row)
    Returns: deduplicated list of (ticker, padded_row)
    """
    # Group by normalised company name
    groups = {}
    for ticker, padded in entries:
        company = padded[company_idx].strip().upper() if company_idx is not None else ticker
        if not company:
            company = ticker  # fallback to ticker if no company name
        if company not in groups:
            groups[company] = []
        groups[company].append((ticker, padded))

    deduped = []
    for company, candidates in groups.items():
        if len(candidates) == 1:
            deduped.append(candidates[0])
            continue

        # Multiple listings for same company — pick best one
        tickers = [t for t, _ in candidates]
        logger.info("  Dedup: %s has %d listings: %s", company, len(candidates), tickers)

        # Prefer US exchange (ADR)
        us_candidates = [
            (t, p) for t, p in candidates
            if exchange_idx is not None and _is_us_exchange(p[exchange_idx])
        ]

        if us_candidates:
            # Among US listings, pick highest composite_score
            best = max(
                us_candidates,
                key=lambda x: _safe_float(x[1][score_idx]) or 0.0 if score_idx is not None else 0.0,
            )
            logger.info("    -> Picked ADR: %s (%s)", best[0],
                        best[1][exchange_idx].strip() if exchange_idx is not None else "?")
        else:
            # No US listing — pick highest composite_score
            best = max(
                candidates,
                key=lambda x: _safe_float(x[1][score_idx]) or 0.0 if score_idx is not None else 0.0,
            )
            logger.info("    -> Picked best score: %s (%s)", best[0],
                        best[1][exchange_idx].strip() if exchange_idx is not None else "?")

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

    if bear_idx is None:
        logger.error("'bear' column not found in AI Analysis")
        sys.exit(1)
    if bull_idx is None:
        logger.error("'bull' column not found in AI Analysis")
        sys.exit(1)
    if ticker_idx is None:
        logger.error("'ticker' column not found in AI Analysis")
        sys.exit(1)

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
            dual_positive.append((ticker, padded))

    logger.info("Found %d equities with both Bear ✅ and Bull ✅", len(dual_positive))

    if not dual_positive:
        logger.warning("No dual-positive equities found. Portfolio will be cleared.")

    # ---------------------------------------------------------------
    # Deduplicate by company name (favour ADR / US listing)
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
    portfolio_rows = []
    for ticker, padded in dual_positive:
        row_data = []
        for col_key in PORTFOLIO_COLUMNS:
            src_idx = col_map.get(col_key)
            if src_idx is not None and src_idx < len(padded):
                val = padded[src_idx].strip()
                # For ticker column, use plain text (not HYPERLINK formula)
                if col_key == "ticker":
                    val = ticker
                row_data.append(val)
            else:
                row_data.append("")
        portfolio_rows.append(row_data)
        logger.info("  %s (score: %s)", ticker,
                    padded[score_idx].strip() if score_idx is not None else "?")

    if args.dry_run:
        logger.info("[DRY RUN] Would write %d rows to Portfolio sheet", len(portfolio_rows))
        for row in portfolio_rows:
            logger.info("  %s", row[:3])
        logger.info("[DRY RUN] Complete. No writes performed.")
        return

    # ---------------------------------------------------------------
    # Write to Portfolio sheet
    # ---------------------------------------------------------------
    # First, clear existing data rows (keep header row 1)
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
