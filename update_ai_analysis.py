#!/usr/bin/env python3
"""
AI Analysis Updater for stock tracking spreadsheet.

Reads the 'AI Analysis' sheet, identifies rows where description is blank,
searches for recent news/earnings via SerpAPI, generates AI analysis using
Gemini, and writes results back to the sheet.

Designed to run nightly via GitHub Actions at 02:00 UTC.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
)
SHEET_NAME = "AI Analysis"
SERPAPI_ENDPOINT = "https://serpapi.com/search"
GEMINI_MODEL = "gemini-2.5-flash"
DELAY_BETWEEN_TICKERS = 3  # seconds between tickers
DELAY_BETWEEN_SEARCHES = 1  # seconds between SerpAPI calls
DELAY_AFTER_GEMINI = 2  # seconds after Gemini call
MAX_RETRIES = 3
RETRY_DELAY = 10

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
# Google Sheets helpers (using googleapiclient, matching existing pattern)
# ---------------------------------------------------------------------------


def get_sheets_service():
    """Create a Google Sheets API service from the service account credentials."""
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
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:AD",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


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
    service, row_number: int, values: dict[str, str], logger: logging.Logger = None
) -> None:
    """Batch-write values for a single ticker row. values maps col_letter -> value."""
    data = []
    for col_letter, value in values.items():
        cell_ref = f"{col_letter}{row_number}"
        data.append({"range": f"'{SHEET_NAME}'!{cell_ref}", "values": [[value]]})

    if data:
        body = {"valueInputOption": "USER_ENTERED", "data": data}
        resp = service.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID, body=body
        ).execute()
        updated = resp.get("totalUpdatedCells", 0)
        expected = len(data)
        if logger and updated != expected:
            logger.warning(
                "Row %d: expected to update %d cells but only %d were updated (possible merged cells?)",
                row_number, expected, updated,
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
    for i, q in enumerate(queries):
        result = serpapi_search(q, api_key, logger)
        if result:
            parts.append(result)
        if i < len(queries) - 1:
            time.sleep(DELAY_BETWEEN_SEARCHES)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Gemini AI call
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You are a concise financial analyst assistant. Return ONLY valid JSON — no markdown, no backticks, no explanation.

Today's date is {today}.

Search results:
{web_context}

---
Company: {company_name}
Ticker: {ticker}

IMPORTANT: Do NOT repeat revenue %, margins, or financial metrics — these are already captured in other columns. Focus PURELY on qualitative factors: competitive position, management execution signals, industry tailwinds/headwinds, and what could change the thesis.

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

"short_outlook": 14-20 words. Be opinionated — take a clear stance on whether the fundamental trajectory is improving or deteriorating, and why. Do NOT mention valuation or price. Do NOT repeat financial metrics.
Start with emoji: 🟢 (positive), 🟡 (neutral/mixed), 🔴 (concerning).
Example: "🟢 Enterprise platform shift gaining traction as legacy competitors struggle to adapt to AI-native workflows"

"key_risks": 14-20 words on the main 1-2 risks. Must be COMPANY-SPECIFIC. No generic risks. Name the actual competitor, the actual regulation, the actual customer concentration issue.
Start with emoji: 🟢 (low/manageable), 🟡 (moderate), 🔴 (high/concerning).
Bad example: "🟡 Intense competition in fintech sector poses risks"
Good example: "🟡 Chime's interchange revenue vulnerable if Durbin Amendment extends to fintechs; Robinhood/SoFi encroaching on core demographic"

"full_outlook": ~400 characters. NOT a recap of earnings numbers. Instead cover:
- WHY the growth is happening (product, market, competitive win)
- What the BEAR CASE looks like specifically (not generic "competition")
- One specific thing to watch in the next 2 quarters that will prove/disprove the thesis
- Any management credibility signals (guidance history, beat/miss pattern)
"""


def _call_gemini_subprocess(prompt: str, api_key: str, model: str, timeout: int = 90) -> str:
    """Call Gemini via curl subprocess with a hard OS timeout."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(payload)
        payload_path = f.name
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", url,
             "-H", "Content-Type: application/json",
             "-d", f"@{payload_path}",
             "--max-time", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return result.stdout
    finally:
        os.unlink(payload_path)


def call_gemini(
    company: str, ticker: str, web_context: str, api_key: str, logger: logging.Logger
) -> dict | None:
    """Call Gemini API and return parsed JSON result, or None on failure."""
    prompt = PROMPT_TEMPLATE.format(
        today=date.today().isoformat(),
        web_context=web_context if web_context else "(No search results available)",
        company_name=company,
        ticker=ticker,
    )

    for attempt in range(MAX_RETRIES):
        try:
            raw = _call_gemini_subprocess(prompt, api_key, GEMINI_MODEL)

            if not raw or not raw.strip():
                raise Exception("Empty response from Gemini API")

            data = json.loads(raw)

            if "error" in data:
                raise Exception(f"{data['error'].get('code')} {data['error'].get('message', '')}")

            # Handle safety filter blocks (no candidates returned)
            candidates = data.get("candidates", [])
            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                raise Exception(f"No candidates returned (blockReason={block_reason})")

            finish_reason = candidates[0].get("finishReason", "")
            if finish_reason == "SAFETY":
                raise Exception("Response blocked by safety filter")

            text = candidates[0]["content"]["parts"][0]["text"].strip()

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
                raise Exception(f"Invalid JSON in Gemini response: {text[:300]}")

            required_keys = {"description", "short_outlook", "full_outlook", "key_risks"}
            missing = required_keys - set(result.keys())
            if missing:
                raise Exception(f"Gemini response missing keys: {missing}")

            # Verify all required fields are non-empty strings
            empty_fields = [k for k in required_keys if not str(result[k]).strip()]
            if empty_fields:
                raise Exception(f"Gemini returned empty values for: {empty_fields}")

            return result

        except Exception as exc:
            exc_str = str(exc)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str else 5
                logger.warning(
                    "Gemini call failed for %s (attempt %d/%d), retrying in %ds: %s",
                    ticker, attempt + 1, MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            else:
                logger.error("Gemini call failed for %s (attempt %d/%d), skipping: %s",
                             ticker, attempt + 1, MAX_RETRIES, exc)
                return None

    return None


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def main():
    logger = setup_logging()
    logger.info("=== AI Analysis Updater started ===")
    start_time = time.time()

    # Validate required keys
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        logger.error("GEMINI_API_KEY env var is not set")
        sys.exit(1)

    serp_api_key = os.environ.get("SERPAPI_API_KEY", "")
    if not serp_api_key:
        logger.warning("SERPAPI_API_KEY not set — will skip web searches")

    # Connect to Google Sheets
    service = get_sheets_service()
    all_rows = read_all_rows(service)
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

            # Step 2: Gemini analysis
            result = call_gemini(company, ticker, web_context, gemini_key, logger)
            time.sleep(DELAY_AFTER_GEMINI)
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

            logger.info("Writing %s — description: %r", ticker, result["description"])
            write_ticker_updates(service, row_number, values, logger)
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
