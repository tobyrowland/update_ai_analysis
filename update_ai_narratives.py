#!/usr/bin/env python3
"""
AI Narrative Updater for EJ2N Spreadsheet.

Reads the 'AI Analysis' sheet, identifies stale/unanalysed companies,
generates fresh narrative content via Gemini + SerpAPI web search,
and writes results back to the sheet.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dateutil import parser as dateparser
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SHEET_NAME = "AI Analysis"
STALENESS_DAYS = 90
SERPAPI_ENDPOINT = "https://serpapi.com/search"
GEMINI_MODEL = "gemini-2.5-flash"
DELAY_BETWEEN_CALLS = 2  # seconds between tickers
RETRY_DELAY = 10  # seconds between retries
MAX_RETRIES = 2  # total attempts per ticker

# Header names used to locate columns dynamically from row 2.
# The script reads actual sheet headers rather than relying on hardcoded positions.
HEADER_TICKER = "ticker"
HEADER_COMPANY = "company_name"
HEADER_SHORT_OUTLOOK = "short_outlook"
HEADER_OUTLOOK = "full_outlook"
HEADER_RISKS = "key_risks"
HEADER_ANALYZED = "ai"

# Map legacy/alternative sheet headers → current lowercase underscore keys.
HEADER_ALIASES = {
    "Ticker":               "ticker",
    "ticker_clean":         "ticker",
    "Company":              "company_name",
    "Company Name":         "company_name",
    "Short Outlook":        "short_outlook",
    "Outlook":              "full_outlook",
    "Full Outlook":         "full_outlook",
    "Key Risks":            "key_risks",
    "AI":                   "ai",
    "Analyzed":             "ai",
    "AI Analyzed":          "ai",
    "Data":                 "data",
    "Data As Of":           "data",
    "Fundamentals Date":    "data",
}

NULL_VALUE = "—"  # must match eodhd_updater.py

# Financial column labels for prompt context (order doesn't matter for lookup).
# These are the lowercase underscore column keys used in the sheet.
FINANCIAL_HEADERS = [
    "r40_score",
    "fundamentals_snapshot",
    "annual_revenue_5y",
    "quarterly_revenue",
    "rev_growth_ttm%",
    "rev_growth_qoq%",
    "rev_cagr%",
    "rev_consistency_score",
    "gross_margin%",
    "gm_trend%",
    "operating_margin%",
    "net_margin%",
    "net_margin_yoy%",
    "fcf_margin%",
    "opex_%_of_revenue",
    "s&m+r&d_%_of_revenue",
    "rule_of_40",
    "qrtrs_to_profitability",
    "eps_only",
    "eps_yoy%",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"update_log_{today_str}.txt"

    logger = logging.getLogger("ai_narrative_updater")
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

    # Support both a file path and raw JSON content (for GitHub Actions secrets)
    if sa_value.strip().startswith("{"):
        import json as _json
        info = _json.loads(sa_value)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            sa_value, scopes=SCOPES
        )
    return build("sheets", "v4", credentials=creds)


def read_all_rows(service) -> list[list[str]]:
    """Return all rows from the AI Analysis sheet (including header rows)."""
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


def write_row_updates(service, updates: list[dict]):
    """
    Batch-write updates to the sheet.

    Each entry in `updates` is:
        {"row": <1-indexed row number>, "values": {col_letter: value, ...}}
    """
    if not updates:
        return

    data = []
    for upd in updates:
        row = upd["row"]
        for col_letter, value in upd["values"].items():
            data.append({
                "range": f"'{SHEET_NAME}'!{col_letter}{row}",
                "values": [[value]],
            })

    body = {"valueInputOption": "USER_ENTERED", "data": data}
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body=body
    ).execute()


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------


def needs_update(row: list[str], col_map: dict) -> bool:
    """Return True if this row should be re-analysed."""
    ticker_idx = col_map.get(HEADER_TICKER, 0)
    analyzed_idx = col_map.get(HEADER_ANALYZED)

    max_idx = max(ticker_idx, analyzed_idx or 0)
    padded = row + [""] * (max_idx + 1 - len(row))

    ticker = padded[ticker_idx].strip()
    if not ticker:
        return False

    if analyzed_idx is None:
        return True  # column doesn't exist yet → treat as stale

    analyzed_str = padded[analyzed_idx].strip()
    if not analyzed_str:
        return True

    try:
        analyzed_date = dateparser.parse(analyzed_str).date()
    except (ValueError, TypeError):
        return True  # unparseable date → treat as stale

    return (date.today() - analyzed_date) > timedelta(days=STALENESS_DAYS)


# ---------------------------------------------------------------------------
# SerpAPI web search
# ---------------------------------------------------------------------------


def serpapi_search(query: str, api_key: str, logger: logging.Logger) -> str:
    """Run a SerpAPI Google search and return concatenated snippets."""
    try:
        resp = requests.get(
            SERPAPI_ENDPOINT,
            params={"q": query, "api_key": api_key, "num": 5, "engine": "google"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("SerpAPI search failed for query '%s': %s", query, exc)
        return ""

    organic = data.get("organic_results", [])
    snippets = []
    total_chars = 0
    for result in organic[:5]:
        snippet = result.get("snippet", "")
        title = result.get("title", "")
        entry = f"- {title}: {snippet}"
        if total_chars + len(entry) > 2000:
            break
        snippets.append(entry)
        total_chars += len(entry)

    return "\n".join(snippets)


def gather_web_context(
    company: str, ticker: str, api_key: str, logger: logging.Logger
) -> str:
    """Run two SerpAPI searches and merge results."""
    current_year = date.today().year
    prev_year = current_year - 1

    q1 = f"{company} {ticker} earnings results {prev_year} {current_year}"
    q2 = f"{company} {ticker} outlook forecast analyst {current_year}"

    s1 = serpapi_search(q1, api_key, logger)
    s2 = serpapi_search(q2, api_key, logger)

    parts = []
    if s1:
        parts.append(f"EARNINGS SEARCH:\n{s1}")
    if s2:
        parts.append(f"OUTLOOK SEARCH:\n{s2}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Gemini prompt & call
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You are a financial analyst writing brief, data-driven narratives for a stock tracking spreadsheet.

COMPANY: {company_name} ({ticker})
FINANCIAL DATA FROM SPREADSHEET:
{financial_data_summary}

{web_section}

EXAMPLES OF THE DESIRED FORMAT (use these as style guides):

--- EXAMPLE 1 (STEP - StepStone Group) ---
Short Outlook: 🟢 Strong fundraising and fee growth driving record assets under management.
Full Outlook: Reported Q3 FY26 earnings on Feb 5, 2026, showing strong fee-related earnings growth driven by record AUM from robust fundraising. As an established, profitable asset manager, GAAP earnings can be volatile due to performance fees. However, the consistent expansion of management fees from sticky, long-term capital provides a stable and growing earnings base, supporting a positive fundamental outlook.
Key Risks: 🟡 Performance fee volatility and fundraising slowdown in an economic downturn.

--- EXAMPLE 2 (GTLB - GitLab) ---
Short Outlook: 🟢 Strong revenue growth and high margins; enterprise adoption is key.
Full Outlook: Latest earnings (Dec 4, 2025) showed strong 30% YoY revenue growth. Exceptional 91% gross margins are funding growth, with operating leverage improving. While still unprofitable on a GAAP basis due to high R&D and S&M spend (incl. SBC), losses are narrowing. Adoption of AI features and enterprise tiers should drive the company towards GAAP profitability within the next 1-2 years.
Key Risks: 🟡 Intense competition from Microsoft/GitHub and macroeconomic spending pressures.

--- EXAMPLE 3 (ADI - Analog Devices, cautious) ---
Short Outlook: 🟡 Cautious guidance as industrial market weakness and inventory correction persists.
Full Outlook: Analog Devices' Q1 FY26 earnings (Feb 18, 2026) beat lowered expectations but Q2 guidance points to continued decline. Revenue is pressured by a broad-based cyclical downturn, particularly in industrial and communications, with inventory digestion taking longer than expected. While historically strong gross margins (>60%) have compressed due to lower factory utilization, the company remains highly profitable. Recovery hinges on a second-half 2026 market rebound.
Key Risks: 🟡 Prolonged cyclical downturn and continued inventory digestion in key markets.

---

Now generate the three fields for {company_name} ({ticker}). Follow these rules exactly:

SHORT OUTLOOK RULES:
- One sentence, max 15 words
- Start with 🟢 (positive), 🟡 (cautious/mixed), or 🔴 (negative) emoji
- Reference the single most important recent development

FULL OUTLOOK RULES:
- 3–5 sentences
- Reference the most recent earnings report by date if available in web search results
- Explain the fundamental earnings quality story
- Mention path to profitability if not yet profitable, or sustainability of profits if already profitable
- Do NOT use bullet points

KEY RISKS RULES:
- One sentence, max 15 words
- Start with 🟡 or 🔴 emoji
- Name the 1–2 most specific risks

Respond ONLY with a JSON object in this exact format, no markdown, no preamble:
{{
  "short_outlook": "...",
  "full_outlook": "...",
  "key_risks": "..."
}}
"""


def build_financial_summary(row: list[str], col_map: dict) -> str:
    """Format financial columns into readable text for the prompt."""
    max_idx = max(col_map.values()) if col_map else 0
    padded = row + [""] * (max_idx + 1 - len(row))
    lines = []
    for label in FINANCIAL_HEADERS:
        col_idx = col_map.get(label)
        if col_idx is not None and col_idx < len(padded):
            val = padded[col_idx].strip()
            if val and val != NULL_VALUE:
                lines.append(f"  {label}: {val}")
    return "\n".join(lines) if lines else "  (no financial data available)"


def _call_gemini_subprocess(prompt: str, api_key: str, model: str, timeout: int = 90) -> str:
    """Call Gemini via curl subprocess with a hard OS timeout."""
    import subprocess, tempfile
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


def call_gemini(prompt: str, api_key: str, logger: logging.Logger) -> dict | None:
    """Call Gemini via curl subprocess with hard timeout, retries on failure."""
    for attempt in range(MAX_RETRIES):
        try:
            raw = _call_gemini_subprocess(prompt, api_key, GEMINI_MODEL)
            data = json.loads(raw)
            if "error" in data:
                raise Exception(f"{data['error'].get('code')} {data['error'].get('message','')}")
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

            # Strip markdown fences if present (fallback for non-JSON mode)
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3].strip()
            if text.startswith("json"):
                text = text[4:].strip()

            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("Gemini returned non-JSON: %s", text[:200])
                return None

            required_keys = {"short_outlook", "full_outlook", "key_risks"}
            missing = required_keys - set(result.keys())
            if missing:
                logger.warning("Gemini response missing keys: %s (got: %s)", missing, list(result.keys()))
                return None
            empty = [k for k in required_keys if not result.get(k)]
            if empty:
                logger.warning("Gemini response has empty values for: %s — accepting partial result", empty)
                for k in empty:
                    result[k] = "N/A"

            return result

        except Exception as exc:
            exc_str = str(exc)
            if attempt < MAX_RETRIES - 1:
                # Use longer delay for rate limits (429)
                delay = RETRY_DELAY if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str else 5
                logger.warning(
                    "Gemini call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            else:
                logger.error("Gemini call failed (attempt %d/%d), skipping: %s",
                             attempt + 1, MAX_RETRIES, exc)
                return None

    return None


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s). 0→A, 25→Z, 26→AA."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def process_company(
    row: list[str],
    row_number: int,
    serpapi_key: str,
    dry_run: bool,
    logger: logging.Logger,
    col_map: dict | None = None,
) -> dict | None:
    """
    Process a single company row. Returns an update dict for writing,
    or None on failure.
    """
    col_map = col_map or {}
    ticker_idx = col_map.get(HEADER_TICKER, 0)
    company_idx = col_map.get(HEADER_COMPANY, 1)
    max_idx = max(col_map.values()) if col_map else 1
    padded = row + [""] * (max_idx + 1 - len(row))
    ticker = padded[ticker_idx].strip()
    company = padded[company_idx].strip()

    logger.info("Processing %s (%s) — row %d", ticker, company, row_number)

    # Web search
    web_context = ""
    if serpapi_key:
        web_context = gather_web_context(company, ticker, serpapi_key, logger)

    web_section = ""
    if web_context:
        web_section = f"RECENT WEB SEARCH RESULTS:\n{web_context}"
    else:
        web_section = "(No recent web search results available — rely on financial data.)"

    # Build prompt
    fin_summary = build_financial_summary(row, col_map)
    prompt = PROMPT_TEMPLATE.format(
        company_name=company,
        ticker=ticker,
        financial_data_summary=fin_summary,
        web_section=web_section,
    )

    if dry_run:
        logger.info("[DRY RUN] Prompt for %s:\n%s", ticker, prompt[:500] + "...")

    # Call Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    result = call_gemini(prompt, gemini_key, logger)
    if result is None:
        return None

    if dry_run:
        logger.info("[DRY RUN] Gemini result for %s:\n%s",
                     ticker, json.dumps(result, indent=2, ensure_ascii=False))
        return None  # don't write in dry-run mode

    today_str = date.today().isoformat()

    # Map output fields to sheet columns dynamically
    field_to_header = {
        "short_outlook": HEADER_SHORT_OUTLOOK,
        "full_outlook": HEADER_OUTLOOK,
        "key_risks": HEADER_RISKS,
    }
    values = {}
    for field, header in field_to_header.items():
        idx = col_map.get(header)
        if idx is not None:
            values[_col_letter(idx)] = result[field]
        else:
            logger.warning("Column '%s' not found in sheet, skipping field '%s'", header, field)

    # Write the analysis date
    analyzed_idx = col_map.get(HEADER_ANALYZED)
    if analyzed_idx is not None:
        values[_col_letter(analyzed_idx)] = today_str

    if not values:
        logger.warning("No writable columns found for %s", ticker)
        return None

    return {
        "row": row_number,
        "values": values,
    }


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="AI Narrative Updater")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts and results without writing to the sheet")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== AI Narrative Updater started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    # Validate Gemini key
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        logger.error("GEMINI_API_KEY env var is not set")
        sys.exit(1)

    serpapi_key = os.environ.get("SERPAPI_API_KEY", "")
    if not serpapi_key:
        logger.warning("SERPAPI_API_KEY not set — will skip web searches")

    # Read sheet
    service = get_sheets_service()
    all_rows = read_all_rows(service)
    logger.info("Read %d rows from sheet (including headers)", len(all_rows))

    # Build column map from row 2 headers
    col_map = {}  # header_name → 0-based column index
    if len(all_rows) >= 2:
        for idx, header in enumerate(all_rows[1]):
            name = header.strip()
            name = HEADER_ALIASES.get(name, name)
            col_map[name] = idx
    logger.info("Column map: %s", {k: v for k, v in col_map.items()})

    # Data starts at row 3 (index 2) — row 1 is group headers, row 2 is column headers
    data_rows = all_rows[2:] if len(all_rows) > 2 else []

    # Find rows needing update
    rows_to_update = []
    for i, row in enumerate(data_rows):
        row_number = i + 3  # 1-indexed sheet row
        if needs_update(row, col_map):
            rows_to_update.append((row_number, row))

    logger.info("Found %d companies needing update", len(rows_to_update))

    if not rows_to_update:
        logger.info("Nothing to do — all companies are up to date.")
        return

    # Process each company — write in batches of 5 to avoid losing progress
    updates = []
    total_written = 0
    errors = 0
    BATCH_SIZE = 5
    for idx, (row_number, row) in enumerate(rows_to_update):
        try:
            result = process_company(
                row, row_number, serpapi_key, args.dry_run, logger, col_map
            )
            if result:
                updates.append(result)
            elif not args.dry_run:
                errors += 1
        except Exception as exc:
            ticker_idx = col_map.get(HEADER_TICKER, 0)
            padded = row + [""] * (ticker_idx + 1 - len(row))
            logger.error("Error processing %s: %s", padded[ticker_idx], exc,
                         exc_info=True)
            errors += 1

        # Write batch every BATCH_SIZE tickers (or at the end)
        if updates and not args.dry_run and (len(updates) >= BATCH_SIZE or idx == len(rows_to_update) - 1):
            logger.info("Writing batch of %d updates to sheet...", len(updates))
            write_row_updates(service, updates)
            total_written += len(updates)
            updates = []

        # Delay between API calls (skip after last)
        if idx < len(rows_to_update) - 1:
            time.sleep(DELAY_BETWEEN_CALLS)

    elapsed = time.time() - start_time
    if args.dry_run:
        logger.info("=== DRY RUN complete. %d companies processed in %.1fs ===",
                     len(rows_to_update), elapsed)
    else:
        logger.info(
            "=== Updated %d companies. Skipped %d due to errors. (%.1fs) ===",
            total_written, errors, elapsed,
        )


if __name__ == "__main__":
    main()
