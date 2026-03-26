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

from tv_screen import run_tradingview_screen, fetch_sector_data

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

# Map TradingView exchange codes → Google Finance exchange codes.
TV_TO_GOOGLE_FINANCE = {
    # United States
    "NASDAQ": "NASDAQ", "NYSE": "NYSE", "NYSEARCA": "NYSEARCA",
    "NYSEMKT": "NYSEAMERICAN", "AMEX": "NYSEAMERICAN", "OTC": "OTCMKTS",
    "BATS": "BATS",
    # Canada
    "TSX": "TSE", "TSXV": "CVE",
    # United Kingdom
    "LSE": "LON", "LON": "LON", "LSIN": "LON",
    # Germany (many alternative exchanges → map to primary ETR/FRA)
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


def _google_finance_url(ticker: str, exchange: str) -> str:
    """Build a Google Finance URL for a ticker."""
    gf_exchange = TV_TO_GOOGLE_FINANCE.get(exchange.upper(), exchange)
    return f"https://www.google.com/finance/quote/{ticker}:{gf_exchange}"


def _ticker_hyperlink(ticker: str, exchange: str) -> str:
    """Build a =HYPERLINK() formula pointing to Google Finance."""
    url = _google_finance_url(ticker, exchange)
    return f'=HYPERLINK("{url}", "{ticker}")'


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
    logger.info("Raw headers (row 2): %s", raw_headers[:10])
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
            exchange = eq.get("exchange", "")
            row[col_map["ticker"]] = _ticker_hyperlink(eq["ticker"], exchange) if exchange else eq["ticker"]
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


def linkify_tickers(service, col_map: dict, ticker_rows: dict, logger):
    """Convert plain-text tickers to =HYPERLINK() formulas pointing to Google Finance."""
    if "ticker" not in col_map:
        return

    ticker_col = _col_letter(col_map["ticker"])
    # Read ticker column with FORMULA render to see raw cell values
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{AI_ANALYSIS_SHEET}'!{ticker_col}3:{ticker_col}",
            valueRenderOption="FORMULA",
        )
        .execute()
    )
    raw_cells = result.get("values", [])

    data = []
    for row_offset, cell in enumerate(raw_cells):
        val = str(cell[0]) if cell else ""
        if not val or val.startswith("="):
            continue  # already a formula or empty
        # Plain text ticker — convert to HYPERLINK
        ticker = val.strip().upper()
        info = ticker_rows.get(ticker, {})
        exchange = info.get("exchange", "")
        if not exchange:
            continue  # can't build URL without exchange
        sheet_row = row_offset + 3
        formula = _ticker_hyperlink(ticker, exchange)
        data.append({
            "range": f"'{AI_ANALYSIS_SHEET}'!{ticker_col}{sheet_row}",
            "values": [[formula]],
        })

    if not data:
        logger.info("All tickers already have HYPERLINK formulas")
        return

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    logger.info("Linked %d tickers to Google Finance", len(data))


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

    logger.info("col_map keys: %s", sorted(col_map.keys()))
    if "sector" in col_map:
        logger.info("sector column index: %d (col %s)", col_map["sector"],
                     _col_letter(col_map["sector"]))
    else:
        logger.warning("'sector' NOT found in col_map!")

    # Count tickers missing sector in the sheet
    total_missing_sector = sum(
        1 for info in ticker_rows.values() if not info.get("sector")
    )
    logger.info("Tickers missing sector in sheet: %d / %d",
                total_missing_sector, len(ticker_rows))

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

    sector_via_screen = sum(1 for u in field_updates if "sector" in u["fields"])
    logger.info("New tickers to add: %d", len(new_tickers))
    logger.info("Existing tickers with missing fields to update: %d (%d with sector)",
                len(field_updates), sector_via_screen)

    # Step 5: Write changes
    add_new_tickers(service, new_tickers, col_map, logger)
    update_missing_fields(service, field_updates, col_map, logger)

    # Step 6: Backfill sector from TradingView for ALL tickers still missing it
    # Re-read which tickers still need sector (not covered by Step 5)
    tickers_needing_sector = []
    updated_rows = {u["row"] for u in field_updates if "sector" in u["fields"]}
    for ticker, info in ticker_rows.items():
        if not info.get("sector") and info["row"] not in updated_rows:
            tickers_needing_sector.append((ticker, info.get("exchange", "")))

    logger.info("Tickers still missing sector after screen merge: %d", len(tickers_needing_sector))

    if tickers_needing_sector:
        if len(tickers_needing_sector) <= 20:
            logger.info("Missing sector tickers: %s",
                         [(t, e) for t, e in tickers_needing_sector])

        sector_data = fetch_sector_data(tickers_needing_sector, logger)
        logger.info("TradingView returned sector for %d / %d tickers",
                     len(sector_data), len(tickers_needing_sector))

        sector_updates = []
        for ticker, exchange in tickers_needing_sector:
            sector = sector_data.get(ticker.upper(), "")
            if sector:
                sector_updates.append({
                    "row": ticker_rows[ticker]["row"],
                    "fields": {"sector": sector},
                })
            else:
                logger.debug("No sector found for %s (exchange=%s)", ticker, exchange)

        if sector_updates:
            update_missing_fields(service, sector_updates, col_map, logger)
            logger.info("Backfilled sector for %d tickers via TradingView lookup",
                         len(sector_updates))
        else:
            logger.info("No sector data returned from TradingView for any missing ticker")

    # Step 7: Linkify plain-text tickers to Google Finance
    linkify_tickers(service, col_map, ticker_rows, logger)

    # Log new tickers
    if new_tickers:
        for eq in sorted(new_tickers, key=lambda e: e["ticker"]):
            logger.info("  NEW: %s (%s) — %s / %s",
                        eq["ticker"], eq.get("exchange", ""),
                        eq.get("sector", ""), eq.get("country", ""))

    logger.info("Nightly screen complete")


if __name__ == "__main__":
    if "--backfill-sector" in sys.argv:
        logger = setup_logging()
        logger.info("=" * 60)
        logger.info("Sector Backfill Only — %s", date.today().isoformat())
        logger.info("=" * 60)
        service = get_sheets_service()
        existing_tickers, col_map, ticker_rows = load_ai_analysis_tickers(service, logger)
        if "sector" not in col_map:
            logger.error("'sector' column not found in headers! col_map: %s", sorted(col_map.keys()))
            sys.exit(1)
        logger.info("sector column: index %d (col %s)", col_map["sector"],
                     _col_letter(col_map["sector"]))

        missing = [(t, info.get("exchange", ""))
                    for t, info in ticker_rows.items() if not info.get("sector")]
        logger.info("Tickers missing sector: %d / %d", len(missing), len(ticker_rows))
        if not missing:
            logger.info("All tickers already have sector — nothing to do")
            sys.exit(0)

        for t, e in missing[:10]:
            logger.info("  e.g. %s (exchange=%r)", t, e)

        sector_data = fetch_sector_data(missing, logger)
        logger.info("TradingView returned sector for %d / %d", len(sector_data), len(missing))

        updates = []
        for ticker, exchange in missing:
            sector = sector_data.get(ticker.upper(), "")
            if sector:
                updates.append({
                    "row": ticker_rows[ticker]["row"],
                    "fields": {"sector": sector},
                })
        logger.info("Updates to write: %d", len(updates))
        if updates:
            for u in updates[:10]:
                logger.info("  row %d → %s", u["row"], u["fields"]["sector"])
            update_missing_fields(service, updates, col_map, logger)
            logger.info("Done — backfilled sector for %d tickers", len(updates))
        else:
            logger.info("TradingView had no sector data for any of the missing tickers")
    else:
        main()
