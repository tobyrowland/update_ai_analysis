#!/usr/bin/env python3
"""
AI Analysis Updater for stock tracking spreadsheet.

Reads the 'AI Analysis' sheet, identifies rows where description is blank,
searches for recent news/earnings via SerpAPI, generates AI analysis using
Claude, and writes results back to the sheet.

Designed to run nightly via GitHub Actions at 02:00 UTC.
"""

import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import anthropic
import gspread
import requests
from google.oauth2 import service_account

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
SHEET_NAME = "AI Analysis"
SERPAPI_ENDPOINT = "https://serpapi.com/search"
CLAUDE_MODEL = "claude-opus-4-5"
DELAY_BETWEEN_TICKERS = 1  # seconds

# Column indices (0-based) matching the sheet structure
COL_TICKER = 0       # A
COL_EXCHANGE = 1     # B
COL_COMPANY = 2      # C
COL_DESCRIPTION = 3  # D
COL_SHORT_OUTLOOK = 6  # G
COL_FULL_OUTLOOK = 7   # H
COL_KEY_RISKS = 8      # I
COL_AI_DATE = 27        # AB

HEADER_ROWS = 2  # data starts at row 3

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"ai_analysis_{date.today().isoformat()}.txt"

    logger = logging.getLogger("ai_analysis_updater")
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


def get_gspread_client() -> gspread.Client:
    """Create a gspread client from the service account credentials."""
    sa_value = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    if not sa_value:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT env var is not set")

    if sa_value.strip().startswith("{"):
        info = json.loads(sa_value)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            sa_value, scopes=SCOPES
        )

    return gspread.authorize(creds)


def read_all_rows(worksheet: gspread.Worksheet) -> list[list[str]]:
    """Return all rows from the worksheet."""
    return worksheet.get_all_values()


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s). 0->A, 25->Z, 26->AA."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def write_ticker_updates(
    worksheet: gspread.Worksheet, row_number: int, values: dict[str, str]
) -> None:
    """Batch-write values for a single ticker row. values maps col_letter -> value."""
    cells = []
    for col_letter, value in values.items():
        cell_ref = f"{col_letter}{row_number}"
        cells.append({"range": f"'{SHEET_NAME}'!{cell_ref}", "values": [[value]]})

    if cells:
        worksheet.spreadsheet.values_batch_update(
            body={"valueInputOption": "USER_ENTERED", "data": cells}
        )


# ---------------------------------------------------------------------------
# Row selection logic
# ---------------------------------------------------------------------------


def needs_update(row: list[str]) -> bool:
    """Return True if ticker is present and description is blank."""
    if len(row) <= COL_TICKER:
        return False
    ticker = row[COL_TICKER].strip()
    if not ticker:
        return False

    description = row[COL_DESCRIPTION].strip() if len(row) > COL_DESCRIPTION else ""
    return description == ""


# ---------------------------------------------------------------------------
# SerpAPI web search
# ---------------------------------------------------------------------------


def serpapi_search(query: str, api_key: str, logger: logging.Logger) -> str:
    """Run a SerpAPI Google search and return concatenated title+snippet lines."""
    try:
        resp = requests.get(
            SERPAPI_ENDPOINT,
            params={"q": query, "api_key": api_key, "num": 4, "engine": "google"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("SerpAPI search failed for '%s': %s", query, exc)
        return ""

    organic = data.get("organic_results", [])
    snippets = []
    for result in organic[:4]:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        if title or snippet:
            snippets.append(f"- {title}: {snippet}")

    return "\n".join(snippets)


def gather_web_context(
    company: str, ticker: str, api_key: str, logger: logging.Logger
) -> str:
    """Run three SerpAPI searches and merge results."""
    today = date.today()
    current_ym = today.strftime("%Y-%m")

    queries = [
        f"{company} {ticker} earnings results 2025",
        f"{company} {ticker} latest news {current_ym}",
        f"{company} {ticker} risks outlook analyst",
    ]

    parts = []
    for q in queries:
        result = serpapi_search(q, api_key, logger)
        if result:
            parts.append(result)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude AI call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a concise financial analyst assistant. "
    "Return ONLY valid JSON — no markdown, no backticks, no explanation."
)

USER_PROMPT_TEMPLATE = """\
Today's date is {today}.

Search results:
{web_context}

---
Company: {company_name}
Ticker: {ticker}

Instructions:
- Only use information from the last 3 months for news/earnings.
- Mention the date of the earnings report you found.
- This stock passed a fundamental screen (good revenue growth, margins). Focus on QUALITATIVE analysis and future progression.

Return ONLY valid JSON in this exact format:
{{
  "ticker": "{ticker}",
  "company_name": "{company_name}",
  "description": "...",
  "short_outlook": "...",
  "key_risks": "...",
  "full_outlook": "..."
}}

Field definitions:

"description": One-sentence summary of the company's core business, products/services, and industry. 60-80 characters.
Examples:
- "Japanese conveyor-belt sushi chain operator with global expansion"
- "LNG containment system designer for gas carriers and onshore tanks"
- "Semiconductor deposition equipment maker for advanced chip manufacturing"

"short_outlook": 14-20 word summary of fundamental outlook based on latest earnings. Focus ONLY on business fundamentals (revenue, margins, guidance, catalysts). Do NOT mention valuation or price.
Start with emoji: 🟢 (positive), 🟡 (neutral/mixed), 🔴 (concerning).
Example: "🟢 Revenue accelerating, margins expanding, strong guidance raised for FY2025"

"key_risks": 14-20 words on the main 1-2 risks.
Start with emoji: 🟢 (low/manageable), 🟡 (moderate), 🔴 (high/concerning).
Example: "🟡 Customer concentration risk and macro slowdown could pressure near-term growth"

"full_outlook": ~400 characters covering: recent earnings (with date), margin trajectory, key catalysts, path to profitability, and near-term view. For pre-profit companies, specifically mention what's driving losses (R&D, SBC, expansion) and whether operating leverage is visible.
"""


def call_claude(
    company: str, ticker: str, web_context: str, logger: logging.Logger
) -> dict | None:
    """Call Claude API and return parsed JSON result, or None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY env var is not set")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        today=date.today().isoformat(),
        web_context=web_context if web_context else "(No search results available)",
        company_name=company,
        ticker=ticker,
    )

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        logger.error("Claude API call failed for %s: %s", ticker, exc)
        return None

    # Extract text from response
    text = ""
    for block in message.content:
        if block.type == "text":
            text += block.text

    text = text.strip()

    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()
    if text.startswith("json"):
        text = text[4:].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Claude returned invalid JSON for %s: %s", ticker, text[:300])
        return None

    required_keys = {"description", "short_outlook", "full_outlook", "key_risks"}
    missing = required_keys - set(result.keys())
    if missing:
        logger.error("Claude response missing keys for %s: %s", ticker, missing)
        return None

    return result


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def main():
    logger = setup_logging()
    logger.info("=== AI Analysis Updater started ===")
    start_time = time.time()

    # Validate required keys
    serp_api_key = os.environ.get("SERP_API_KEY", "")
    if not serp_api_key:
        logger.warning("SERP_API_KEY not set — will skip web searches")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        logger.error("ANTHROPIC_API_KEY env var is not set")
        sys.exit(1)

    # Connect to Google Sheets
    client = get_gspread_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(SHEET_NAME)

    all_rows = read_all_rows(worksheet)
    logger.info("Read %d rows from sheet (including %d header rows)", len(all_rows), HEADER_ROWS)

    # Data starts after header rows
    data_rows = all_rows[HEADER_ROWS:] if len(all_rows) > HEADER_ROWS else []

    # Find rows needing update
    rows_to_process = []
    for i, row in enumerate(data_rows):
        row_number = i + HEADER_ROWS + 1  # 1-indexed sheet row
        if needs_update(row):
            rows_to_process.append((row_number, row))

    logger.info("Found %d tickers needing update", len(rows_to_process))

    if not rows_to_process:
        logger.info("Nothing to do — all tickers have descriptions.")
        return

    succeeded = 0
    failed = 0

    for idx, (row_number, row) in enumerate(rows_to_process):
        ticker = row[COL_TICKER].strip()
        company = row[COL_COMPANY].strip() if len(row) > COL_COMPANY else ticker

        try:
            logger.info("Processing %s (%s) — row %d [%d/%d]",
                         ticker, company, row_number, idx + 1, len(rows_to_process))

            # Step 1: SerpAPI searches
            web_context = ""
            if serp_api_key:
                web_context = gather_web_context(company, ticker, serp_api_key, logger)

            # Step 2: Claude analysis
            result = call_claude(company, ticker, web_context, logger)
            if result is None:
                failed += 1
                continue

            # Step 3: Write back to sheet
            today_str = date.today().isoformat()
            values = {
                _col_letter(COL_DESCRIPTION): result["description"],
                _col_letter(COL_SHORT_OUTLOOK): result["short_outlook"],
                _col_letter(COL_FULL_OUTLOOK): result["full_outlook"],
                _col_letter(COL_KEY_RISKS): result["key_risks"],
                _col_letter(COL_AI_DATE): today_str,
            }

            write_ticker_updates(worksheet, row_number, values)
            logger.info("Successfully updated %s", ticker)
            succeeded += 1

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc, exc_info=True)
            failed += 1

        # Rate limit delay between tickers (skip after last)
        if idx < len(rows_to_process) - 1:
            time.sleep(DELAY_BETWEEN_TICKERS)

    elapsed = time.time() - start_time
    logger.info(
        "=== Finished: %d succeeded, %d failed out of %d tickers (%.1fs) ===",
        succeeded, failed, len(rows_to_process), elapsed,
    )


if __name__ == "__main__":
    main()
