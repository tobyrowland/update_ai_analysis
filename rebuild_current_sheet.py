#!/usr/bin/env python3
"""
Rebuild the CURRENT sheet in the EJ2N Google Spreadsheet.

Reads existing data from the old CURRENT sheet structure, clears the sheet,
writes new category headers (row 1), column titles (row 2), and migrated
data starting at row 3 — all via the Google Sheets API.

Usage:
    python rebuild_current_sheet.py
    python rebuild_current_sheet.py --dry-run   # preview mapping without writing
"""

import argparse
import json
import logging
import os
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

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SHEET_NAME = "CURRENT"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# New sheet structure
# ---------------------------------------------------------------------------

# Row 1: merged category cells  (merge_range, label)
CATEGORY_MERGES = [
    ("A1:E1", "IDENTITY"),
    ("F1:G1", "PRICE"),
    ("H1:J1", "VALUATION"),
    ("K1:L1", "REVENUE"),
    ("M1:R1", "MARGINS"),
    ("S1:T1", "MARKET"),
    ("U1:AB1", "AI NARRATIVE"),
    ("AC1:AF1", "TRACKING"),
]

# Row 2: column titles  (position matches column letter A-AF = indices 0-31)
NEW_HEADERS = [
    "ticker",               # A   0
    "exchange",             # B   1
    "company_name",         # C   2
    "country",              # D   3
    "sector",               # E   4
    "price",                # F   5
    "market_cap",           # G   6
    "ps_ratio_ttm",         # H   7
    "entry_ps_ttm",         # I   8
    "ps_discount",          # J   9
    "total_revenue_ttm",    # K  10
    "rev_growth_ttm%",      # L  11
    "gross_margin_%",       # M  12
    "net_margin_ttm",       # N  13
    "net_margin_direction", # O  14
    "net_margin_annual",    # P  15
    "net_margin_qoq",       # Q  16
    "fcf_margin_ttm",       # R  17
    "perf_52w_vs_spy",      # S  18
    "rating",               # T  19
    "status",               # U  20
    "description",          # V  21
    "fundamentals",         # W  22
    "short_outlook",        # X  23
    "signal",               # Y  24
    "outlook",              # Z  25
    "ai_analysis_date",     # AA 26
    "net_income_ttm",       # AB 27
    "first_seen",           # AC 28
    "days_on_list",         # AD 29
    "chart_data",           # AE 30
    "chart_data_date",      # AF 31
]

NUM_COLS = len(NEW_HEADERS)  # 32

# Column widths (pixels ≈ width * 7 for Sheets API which uses pixel units)
COLUMN_WIDTHS = {
    0: 10, 1: 10, 2: 28, 3: 14, 4: 18, 5: 10, 6: 14, 7: 10,
    8: 12, 9: 12, 10: 16, 11: 14, 12: 14, 13: 14, 14: 18, 15: 16,
    16: 14, 17: 14, 18: 14, 19: 10, 20: 20, 21: 32, 22: 42, 23: 36,
    24: 28, 25: 40, 26: 16, 27: 16, 28: 14, 29: 10, 30: 20, 31: 16,
}

# Number formats by 0-indexed column
NUMBER_FORMATS = {
    5:  {"type": "NUMBER", "pattern": "#,##0.00"},         # F price
    6:  {"type": "NUMBER", "pattern": "#,##0"},            # G market_cap
    7:  {"type": "NUMBER", "pattern": "0.00"},             # H ps_ratio_ttm
    8:  {"type": "NUMBER", "pattern": "0.00"},             # I entry_ps_ttm
    9:  {"type": "PERCENT", "pattern": "+0.0%;-0.0%;\"-\""},  # J ps_discount
    10: {"type": "NUMBER", "pattern": "#,##0"},            # K total_revenue_ttm
    11: {"type": "PERCENT", "pattern": "0.0%"},            # L rev_growth_ttm%
    12: {"type": "PERCENT", "pattern": "0.0%"},            # M gross_margin_%
    13: {"type": "PERCENT", "pattern": "0.0%"},            # N net_margin_ttm
    17: {"type": "PERCENT", "pattern": "0.0%"},            # R fcf_margin_ttm
    18: {"type": "PERCENT", "pattern": "+0.0%;-0.0%;\"-\""},  # S perf_52w_vs_spy
    27: {"type": "NUMBER", "pattern": "#,##0"},            # AB net_income_ttm
    28: {"type": "DATE", "pattern": "dd-mmm-yyyy"},        # AC first_seen
    29: {"type": "NUMBER", "pattern": "0"},                # AD days_on_list
    31: {"type": "DATE", "pattern": "dd-mmm-yyyy"},        # AF chart_data_date
}

# Old header name → new header name mapping for data migration.
# The script reads old headers from row 1 of the existing CURRENT sheet
# and maps values by header name, NOT by column position.
OLD_TO_NEW = {
    "ticker":               "ticker",
    "exchange":             "exchange",
    "company_name":         "company_name",
    "country":              "country",
    "sector":               "sector",
    "price":                "price",
    "market_cap":           "market_cap",
    "ps_ratio_ttm":         "ps_ratio_ttm",
    "entry_ps_ttm":         "entry_ps_ttm",
    # ps_discount → formula, do NOT copy old values
    "total_revenue_ttm":    "total_revenue_ttm",
    "rev_growth_ttm%":      "rev_growth_ttm%",
    "gross_margin_%":       "gross_margin_%",
    "gross_margin%":        "gross_margin_%",
    "net_margin_ttm":       "net_margin_ttm",
    "net_margin_direction": "net_margin_direction",
    "net_margin_annual":    "net_margin_annual",
    "net_margin_qoq":       "net_margin_qoq",
    "fcf_margin_ttm":       "fcf_margin_ttm",
    "perf_52w_vs_spy":      "perf_52w_vs_spy",
    "rating":               "rating",
    "status":               "status",
    "description":          "description",
    "fundamentals_snapshot": "fundamentals",
    "fundamentals":         "fundamentals",
    "short_outlook":        "short_outlook",
    "signal":               "signal",
    "outlook":              "outlook",
    "ai_analysis_date":     "ai_analysis_date",
    "net_income_ttm":       "net_income_ttm",
    "first_seen":           "first_seen",
    # days_on_list → formula, do NOT copy old values
    "chart_data":           "chart_data",
    "chart_data_date":      "chart_data_date",
}

# Columns that are formulas (not migrated from old data)
FORMULA_COLS = {"ps_discount", "days_on_list"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"rebuild_current_{date.today().isoformat()}.txt"

    logger = logging.getLogger("rebuild_current")
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
    """Convert 0-based column index to Excel-style letter(s). 0→A, 25→Z, 26→AA."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def _get_sheet_id(service, sheet_name: str) -> int:
    """Return the numeric sheet ID for a given sheet name."""
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties")
        .execute()
    )
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == sheet_name:
            return sheet["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{sheet_name}' not found")


def _hex_to_rgb(hex_color: str) -> dict:
    """Convert hex color (e.g. '1a1a2e') to Sheets API RGB dict (0-1 floats)."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255
    g = int(hex_color[2:4], 16) / 255
    b = int(hex_color[4:6], 16) / 255
    return {"red": r, "green": g, "blue": b}


def _parse_merge_range(range_str: str) -> tuple[int, int, int, int]:
    """Parse 'A1:E1' to (startRow, endRow, startCol, endCol) 0-indexed, end-exclusive."""
    import re
    match = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", range_str)
    if not match:
        raise ValueError(f"Invalid range: {range_str}")

    def col_to_idx(col_str):
        idx = 0
        for ch in col_str:
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1  # 0-indexed

    start_col = col_to_idx(match.group(1))
    start_row = int(match.group(2)) - 1
    end_col = col_to_idx(match.group(3))
    end_row = int(match.group(4)) - 1
    return start_row, end_row + 1, start_col, end_col + 1


# ---------------------------------------------------------------------------
# Read old CURRENT sheet data
# ---------------------------------------------------------------------------


def read_old_current(service, logger) -> tuple[list[str], list[list[str]]]:
    """
    Read the existing CURRENT sheet.
    The old sheet has a single header row (row 1) and data from row 2 onwards.
    Returns (old_headers, data_rows).

    Uses FORMULA render option for company_name HYPERLINK formulas.
    """
    # First read with FORMULA to preserve HYPERLINK formulas
    result_formula = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:AZ",
            valueRenderOption="FORMULA",
        )
        .execute()
    )
    rows_formula = result_formula.get("values", [])

    if not rows_formula:
        logger.error("CURRENT sheet is empty!")
        return [], []

    old_headers = [str(h).strip() for h in rows_formula[0]]
    data_rows = rows_formula[1:]

    logger.info("Old CURRENT sheet: %d headers, %d data rows", len(old_headers), len(data_rows))
    logger.info("Old headers: %s", old_headers)

    return old_headers, data_rows


# ---------------------------------------------------------------------------
# Build new sheet data
# ---------------------------------------------------------------------------


def migrate_data(
    old_headers: list[str],
    old_data: list[list[str]],
    logger,
) -> list[list[str]]:
    """
    Map old data rows into the new column structure.
    Returns list of new rows (each row is a list of cell values for A-AF).
    """
    # Build old header name → old column index mapping
    old_col_map = {}
    for idx, h in enumerate(old_headers):
        h_lower = h.strip().lower()
        old_col_map[h_lower] = idx
        old_col_map[h.strip()] = idx

    # Build new header name → new column index mapping
    new_col_map = {h: i for i, h in enumerate(NEW_HEADERS)}

    # Build the actual old-idx → new-idx mapping
    migration_map = {}  # old_col_idx → new_col_idx
    for old_name, new_name in OLD_TO_NEW.items():
        old_idx = old_col_map.get(old_name) or old_col_map.get(old_name.lower())
        new_idx = new_col_map.get(new_name)
        if old_idx is not None and new_idx is not None:
            migration_map[old_idx] = new_idx
            logger.info("  Map: old[%d] '%s' → new[%d] '%s'",
                        old_idx, old_name, new_idx, new_name)

    # Log unmapped old columns
    mapped_old_indices = set(migration_map.keys())
    for idx, h in enumerate(old_headers):
        if idx not in mapped_old_indices and h.strip():
            logger.info("  Dropped: old[%d] '%s'", idx, h)

    # Migrate rows
    new_rows = []
    ps_discount_idx = new_col_map["ps_discount"]
    days_on_list_idx = new_col_map["days_on_list"]
    entry_ps_idx = new_col_map["entry_ps_ttm"]
    ps_ratio_idx = new_col_map["ps_ratio_ttm"]
    first_seen_idx = new_col_map["first_seen"]

    for row_idx, old_row in enumerate(old_data):
        new_row = [""] * NUM_COLS

        # Map each old column value to its new position
        for old_col, new_col in migration_map.items():
            if old_col < len(old_row):
                val = old_row[old_col]
                if val is not None:
                    new_row[new_col] = val

        # Write formulas for formula columns
        sheet_row = row_idx + 3  # data starts at row 3

        # ps_discount: =IFERROR((I{row}-H{row})/I{row}, "")
        i_col = _col_letter(entry_ps_idx)
        h_col = _col_letter(ps_ratio_idx)
        new_row[ps_discount_idx] = f'=IFERROR(({i_col}{sheet_row}-{h_col}{sheet_row})/{i_col}{sheet_row}, "")'

        # days_on_list: =IFERROR(TODAY()-AC{row}, "")
        ac_col = _col_letter(first_seen_idx)
        new_row[days_on_list_idx] = f'=IFERROR(TODAY()-{ac_col}{sheet_row}, "")'

        new_rows.append(new_row)

    logger.info("Migrated %d data rows", len(new_rows))
    return new_rows


# ---------------------------------------------------------------------------
# Write new structure
# ---------------------------------------------------------------------------


def write_new_structure(service, new_data_rows: list[list[str]], logger):
    """
    Clear the CURRENT sheet and write the new structure:
    - Row 1: merged category headers
    - Row 2: column titles
    - Row 3+: data
    """
    sheet_id = _get_sheet_id(service, SHEET_NAME)

    # --- Step 1: Clear the entire sheet ---
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1:ZZ",
    ).execute()
    logger.info("Cleared CURRENT sheet")

    # --- Step 2: Write category row (row 1) ---
    # First, build the row 1 values (just the label in the first cell of each merge)
    cat_row = [""] * NUM_COLS
    for merge_range, label in CATEGORY_MERGES:
        start_row, _, start_col, _ = _parse_merge_range(merge_range)
        cat_row[start_col] = label

    # --- Step 3: Write header row (row 2) ---
    header_row = NEW_HEADERS[:]

    # --- Step 4: Build all data for single batch write ---
    all_rows = [cat_row, header_row] + new_data_rows

    end_col = _col_letter(NUM_COLS - 1)
    end_row = len(all_rows)
    write_range = f"'{SHEET_NAME}'!A1:{end_col}{end_row}"

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=write_range,
        valueInputOption="USER_ENTERED",
        body={"values": all_rows},
    ).execute()
    logger.info("Wrote %d rows to %s", len(all_rows), write_range)

    # --- Step 5: Apply formatting via batchUpdate ---
    requests_list = []

    # Ensure enough columns
    requests_list.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "columnCount": max(NUM_COLS, 32),
                    "frozenRowCount": 2,
                    "frozenColumnCount": 3,
                },
            },
            "fields": "gridProperties.columnCount,gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })

    # Merge cells for category headers (row 1)
    for merge_range, _ in CATEGORY_MERGES:
        start_row, end_row_m, start_col, end_col_m = _parse_merge_range(merge_range)
        requests_list.append({
            "mergeCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "endRowIndex": end_row_m,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col_m,
                },
                "mergeType": "MERGE_ALL",
            }
        })

    # Row 1 formatting: dark navy background, white bold text, centered
    cat_bg = _hex_to_rgb("1a1a2e")
    requests_list.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": NUM_COLS,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": cat_bg,
                    "textFormat": {
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        "bold": True,
                        "fontFamily": "Arial",
                        "fontSize": 9,
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }
    })

    # Row 2 formatting: slightly lighter navy, white bold, centered
    hdr_bg = _hex_to_rgb("2d2d44")
    requests_list.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": 2,
                "startColumnIndex": 0,
                "endColumnIndex": NUM_COLS,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": hdr_bg,
                    "textFormat": {
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        "bold": True,
                        "fontFamily": "Arial",
                        "fontSize": 9,
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }
    })

    # Data rows (row 3+): Arial 10pt, reset formatting
    total_data_rows = len(new_data_rows)
    if total_data_rows > 0:
        # Set font for all data rows
        requests_list.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": 2 + total_data_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": NUM_COLS,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                            "bold": False,
                            "fontFamily": "Arial",
                            "fontSize": 10,
                        },
                    }
                },
                "fields": "userEnteredFormat.textFormat",
            }
        })

        # Alternating row fills: white / light grey
        white_bg = {"red": 1, "green": 1, "blue": 1}
        alt_bg = _hex_to_rgb("f7f7f7")

        for i in range(total_data_rows):
            bg = white_bg if i % 2 == 0 else alt_bg
            requests_list.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 2 + i,
                        "endRowIndex": 3 + i,
                        "startColumnIndex": 0,
                        "endColumnIndex": NUM_COLS,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg,
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })

    # Column widths
    for col_idx, width_chars in COLUMN_WIDTHS.items():
        pixel_width = width_chars * 7  # rough char-to-pixel conversion
        requests_list.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": pixel_width},
                "fields": "pixelSize",
            }
        })

    # Row heights: row 1&2 = 20px, data rows = 18px
    requests_list.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": 0,
                "endIndex": 2,
            },
            "properties": {"pixelSize": 20},
            "fields": "pixelSize",
        }
    })
    if total_data_rows > 0:
        requests_list.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": 2,
                    "endIndex": 2 + total_data_rows,
                },
                "properties": {"pixelSize": 18},
                "fields": "pixelSize",
            }
        })

    # Number formats for data columns
    for col_idx, fmt_spec in NUMBER_FORMATS.items():
        if total_data_rows > 0:
            requests_list.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 2,
                        "endRowIndex": 2 + total_data_rows,
                        "startColumnIndex": col_idx,
                        "endColumnIndex": col_idx + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": fmt_spec["type"],
                                "pattern": fmt_spec["pattern"],
                            }
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

    # Execute all formatting requests in one batch
    if requests_list:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": requests_list},
        ).execute()
        logger.info("Applied %d formatting requests", len(requests_list))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()
    logger = setup_logging()

    parser = argparse.ArgumentParser(description="Rebuild CURRENT sheet")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read and map data but do not write changes")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Rebuild CURRENT sheet — starting")
    logger.info("=" * 60)

    service = get_sheets_service()

    # Read existing data
    old_headers, old_data = read_old_current(service, logger)

    if not old_headers:
        logger.error("No headers found in CURRENT sheet — aborting")
        sys.exit(1)

    # Migrate data to new structure
    new_data_rows = migrate_data(old_headers, old_data, logger)

    if args.dry_run:
        logger.info("=== DRY RUN — not writing changes ===")
        logger.info("Would write %d data rows with %d columns", len(new_data_rows), NUM_COLS)
        if new_data_rows:
            logger.info("Sample row 0: %s", new_data_rows[0][:10])
        return

    # Write new structure
    write_new_structure(service, new_data_rows, logger)

    logger.info("=" * 60)
    logger.info("Rebuild CURRENT sheet — done (%d data rows migrated)", len(new_data_rows))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
