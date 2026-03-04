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

from google import genai

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SHEET_NAME = "AI Analysis"
STALENESS_DAYS = 90
SERPAPI_ENDPOINT = "https://serpapi.com/search"
GEMINI_MODEL = "gemini-2.5-flash"
DELAY_BETWEEN_CALLS = 4  # seconds between tickers
RETRY_DELAY = 30  # seconds — Gemini rate limit asks for ~28s
MAX_RETRIES = 4  # total attempts per ticker

# Column indices (0-based) matching the actual sheet layout (27 columns, A-AA)
COL_TICKER = 0       # A
COL_COMPANY = 1      # B
COL_DESCRIPTION = 2  # C
COL_SIGNAL = 3       # D
COL_SHORT_OUTLOOK = 4  # E
COL_FULL_OUTLOOK = 5   # F
COL_KEY_RISKS = 6      # G
COL_ANALYZED = 7       # H
COL_DATA_AS_OF = 8     # I
COL_FIN_START = 9      # J  (financial data starts here)
COL_FIN_END = 26       # AA (financial data ends here, inclusive)

# Financial column labels (J–AA) for prompt context
FINANCIAL_LABELS = [
    "Annual Revenue (5Y)",       # J
    "Quarterly Revenue",          # K
    "Rev Growth TTM %",           # L
    "Rev Growth QoQ %",           # M
    "Rev CAGR 3Y %",              # N
    "Rev Consistency Score",      # O
    "Gross Margin %",             # P
    "GM Trend (Qtly)",            # Q
    "Operating Margin %",         # R
    "Net Margin %",               # S
    "Net Margin YoY Δ",           # T
    "FCF Margin %",               # U
    "Opex % of Revenue",          # V
    "S&M+R&D % of Revenue",       # W
    "Rule of 40",                 # X
    "Qtrs to Profitability",      # Y
    "EPS Qtrly",                  # Z
    "EPS YoY %",                  # AA
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
            range=f"'{SHEET_NAME}'!A1:AB",
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


def needs_update(row: list[str]) -> bool:
    """Return True if this row should be re-analysed."""
    # Pad row so index access is safe
    padded = row + [""] * (COL_ANALYZED + 1 - len(row))

    ticker = padded[COL_TICKER].strip()
    if not ticker:
        return False

    analyzed_str = padded[COL_ANALYZED].strip()
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
Signal: 💎 Rev +55% YoY | 3Y CAGR -5% | GM 40% | Margins →
Short Outlook: 🟢 Strong fundraising and fee growth driving record assets under management.
Full Outlook: Reported Q3 FY26 earnings on Feb 5, 2026, showing strong fee-related earnings growth driven by record AUM from robust fundraising. As an established, profitable asset manager, GAAP earnings can be volatile due to performance fees. However, the consistent expansion of management fees from sticky, long-term capital provides a stable and growing earnings base, supporting a positive fundamental outlook.
Key Risks: 🟡 Performance fee volatility and fundraising slowdown in an economic downturn.

--- EXAMPLE 2 (GTLB - GitLab) ---
Signal: 💎💎💎 Rev +27% YoY | 3Y CAGR +44% | GM 88% | Margins ↑
Short Outlook: 🟢 Strong revenue growth and high margins; enterprise adoption is key.
Full Outlook: Latest earnings (Dec 4, 2025) showed strong 30% YoY revenue growth. Exceptional 91% gross margins are funding growth, with operating leverage improving. While still unprofitable on a GAAP basis due to high R&D and S&M spend (incl. SBC), losses are narrowing. Adoption of AI features and enterprise tiers should drive the company towards GAAP profitability within the next 1-2 years.
Key Risks: 🟡 Intense competition from Microsoft/GitHub and macroeconomic spending pressures.

--- EXAMPLE 3 (ADI - Analog Devices, cautious) ---
Signal: 💎💎💎 Rev +26% YoY | 3Y CAGR -3% | GM 63% | Margins ↑
Short Outlook: 🟡 Cautious guidance as industrial market weakness and inventory correction persists.
Full Outlook: Analog Devices' Q1 FY26 earnings (Feb 18, 2026) beat lowered expectations but Q2 guidance points to continued decline. Revenue is pressured by a broad-based cyclical downturn, particularly in industrial and communications, with inventory digestion taking longer than expected. While historically strong gross margins (>60%) have compressed due to lower factory utilization, the company remains highly profitable. Recovery hinges on a second-half 2026 market rebound.
Key Risks: 🟡 Prolonged cyclical downturn and continued inventory digestion in key markets.

---

Now generate the four fields for {company_name} ({ticker}). Follow these rules exactly:

SIGNAL RULES:
- Use 💎 (1 gem) for moderate quality, 💎💎 for good, 💎💎💎 for exceptional — base gem count on the overall combination of growth rate, gross margin level, and margin trend
- Format MUST be: 💎[gems] Rev +/-X% YoY | 3Y CAGR +/-X% | GM X% | Margins ↑/↓/→
- "Rev YoY %" comes from column M (Rev Growth TTM %)
- "3Y CAGR" comes from column O (Rev CAGR 3Y %)
- "GM" comes from column Q (Gross Margin %)
- "Margins" trend: use ↑ if Net Margin YoY Δ (col T) is positive, ↓ if negative, → if flat (within ±1%)
- Always include the sign (+ or -) on all percentage figures
- If a value is missing or blank, omit that segment rather than showing "N/A"

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
  "signal": "...",
  "short_outlook": "...",
  "full_outlook": "...",
  "key_risks": "..."
}}
"""


def build_financial_summary(row: list[str]) -> str:
    """Format financial columns K–AB into readable text for the prompt."""
    padded = row + [""] * (COL_FIN_END + 1 - len(row))
    lines = []
    for i, label in enumerate(FINANCIAL_LABELS):
        col_idx = COL_FIN_START + i
        val = padded[col_idx].strip() if col_idx < len(padded) else ""
        if val:
            lines.append(f"  {label}: {val}")
    return "\n".join(lines) if lines else "  (no financial data available)"


def call_gemini(prompt: str, api_key: str, logger: logging.Logger) -> dict | None:
    """Call Gemini and return parsed JSON, with retries on failure."""
    from google.genai.types import GenerateContentConfig
    client = genai.Client(api_key=api_key)
    config = GenerateContentConfig(response_mime_type="application/json")
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt, config=config,
            )
            text = response.text.strip()

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

            required_keys = {"signal", "short_outlook", "full_outlook", "key_risks"}
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


def process_company(
    row: list[str],
    row_number: int,
    serpapi_key: str,
    dry_run: bool,
    logger: logging.Logger,
) -> dict | None:
    """
    Process a single company row. Returns an update dict for writing,
    or None on failure.
    """
    padded = row + [""] * (COL_FIN_END + 1 - len(row))
    ticker = padded[COL_TICKER].strip()
    company = padded[COL_COMPANY].strip()

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
    fin_summary = build_financial_summary(row)
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
    return {
        "row": row_number,
        "values": {
            "E": result["signal"],
            "F": result["short_outlook"],
            "G": result["full_outlook"],
            "H": result["key_risks"],
            "I": today_str,
        },
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

    # Data starts at row 3 (index 2) — row 1 is group headers, row 2 is column headers
    data_rows = all_rows[2:] if len(all_rows) > 2 else []

    # Find rows needing update
    rows_to_update = []
    for i, row in enumerate(data_rows):
        row_number = i + 3  # 1-indexed sheet row
        if needs_update(row):
            rows_to_update.append((row_number, row))

    logger.info("Found %d companies needing update", len(rows_to_update))

    if not rows_to_update:
        logger.info("Nothing to do — all companies are up to date.")
        return

    # Process each company
    updates = []
    errors = 0
    for idx, (row_number, row) in enumerate(rows_to_update):
        try:
            result = process_company(
                row, row_number, serpapi_key, args.dry_run, logger
            )
            if result:
                updates.append(result)
            elif not args.dry_run:
                errors += 1
        except Exception as exc:
            padded = row + [""] * 2
            logger.error("Error processing %s: %s", padded[COL_TICKER], exc,
                         exc_info=True)
            errors += 1

        # Delay between API calls (skip after last)
        if idx < len(rows_to_update) - 1:
            time.sleep(DELAY_BETWEEN_CALLS)

    # Write results
    if updates and not args.dry_run:
        logger.info("Writing %d updates to sheet...", len(updates))
        write_row_updates(service, updates)
        logger.info("Sheet updated successfully.")

    elapsed = time.time() - start_time
    if args.dry_run:
        logger.info("=== DRY RUN complete. %d companies processed in %.1fs ===",
                     len(rows_to_update), elapsed)
    else:
        logger.info(
            "=== Updated %d companies. Skipped %d due to errors. (%.1fs) ===",
            len(updates), errors, elapsed,
        )


if __name__ == "__main__":
    main()
