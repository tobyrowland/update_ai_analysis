#!/usr/bin/env python3
"""
Bear Evaluation — Risk Audit for Top Equities.

Sends the top 40 green-eligible equities from AI Analysis to Gemini 2.5 Pro
for a bear/risk audit. Each equity receives a ✅ (pass) or ❌ (fail) verdict.
Results are written to the 'Bear' column in the AI Analysis sheet.

Schedule: Sundays 07:00 UTC (after score_ai_analysis).
"""

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
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
SHEET_NAME = "AI Analysis"
GEMINI_MODEL = "gemini-2.5-pro"
GEMINI_TIMEOUT = 180  # seconds — large prompt needs more time
MAX_RETRIES = 2
RETRY_DELAY = 15
DELAY_BETWEEN_CALLS = 2
TOP_N = 100
NULL_VALUE = "—"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

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
}

# Columns to EXCLUDE from the data sent to Gemini
EXCLUDED_COLUMNS = {
    "status", "composite_score", "price", "ps_now",
    "price_pct_of_52w_high", "price_%_of_52w_high",
    "perf_52w_vs_spy", "rating", "ai", "data", "scoring", "bear",
}

# ---------------------------------------------------------------------------
# Bear prompt
# ---------------------------------------------------------------------------

BEAR_PROMPT_TEMPLATE = """\
Role: You are a strictly objective Risk Auditor. Your task is to evaluate a list \
of equities based exclusively on the provided data and AI analysis. You are not \
allowed to use outside knowledge or "hallucinate" external market trends.

Your Objective: Review every ticker. Assign either a Green Tick (✅) or a Red Cross (❌) to each one.

The "Red Cross" (❌) Criteria:
You must assign a ❌ if the provided data or AI analysis shows any of the following:

Fundamental Weakness: High debt, declining margins, or poor cash flow mentioned in the text.

AI 'Fluff': If the AI analysis mentions "potential" or "future growth" without citing \
specific current revenue or structural advantages.

Risk Factors: Any mention of regulatory hurdles, supply chain vulnerability, or intense competition.

Valuation Warnings: If the data suggests the stock is "overextended," "expensive," or at a "premium."

The "Green Tick" (✅) Criteria:
Assign a ✅ only if the stock passes the audit with stable fundamentals and the AI analysis \
provides concrete, data-backed reasons for its strength with no significant "Bear" warnings.

Output Format:
For every stock in the list, provide the result in this exact format:

[TICKER]: ✅ (Only if it passes all risk checks)

[TICKER]: ❌ [Brief, blunt reason why it failed based only on the provided data.]

Constraint: Do not rank these. Do not provide a summary. Simply go through the list one by one.

=== EQUITIES TO EVALUATE ({count}) ===

{equity_data}

=== END OF DATA ===

Now evaluate each equity above. Output ONLY the verdict lines, one per ticker."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"bear_eval_{date.today().isoformat()}.txt"

    logger = logging.getLogger("bear_evaluation")
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


def read_all_rows(service) -> list[list[str]]:
    """Return all rows from the AI Analysis sheet (including header rows)."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:AZ",
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


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s). 0→A, 25→Z, 26→AA."""
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


# ---------------------------------------------------------------------------
# Data selection
# ---------------------------------------------------------------------------


def select_top_eligible(all_rows, col_map, top_n=TOP_N):
    """
    Filter rows with 🟢 status, sort by composite_score desc, return top N.

    Returns list of (sheet_row_number, row_data, ticker) tuples.
    """
    status_idx = col_map.get("status")
    score_idx = col_map.get("composite_score")
    ticker_idx = col_map.get("ticker")

    if status_idx is None or score_idx is None or ticker_idx is None:
        return []

    eligible = []
    max_idx = max(col_map.values())
    for row_offset, row in enumerate(all_rows[2:]):  # skip header rows
        padded = row + [""] * (max_idx + 1 - len(row))
        status = padded[status_idx].strip()
        if not status.startswith("🟢"):
            continue

        ticker = _extract_ticker(padded[ticker_idx])
        if not ticker:
            continue

        score = _safe_float(padded[score_idx])
        sheet_row = row_offset + 3  # 1-indexed
        eligible.append((sheet_row, row, ticker, score if score is not None else 0.0))

    # Sort by composite_score descending
    eligible.sort(key=lambda x: x[3], reverse=True)
    return [(r, row, t) for r, row, t, _ in eligible[:top_n]]


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def build_equity_block(row, col_map, ticker, excluded=EXCLUDED_COLUMNS):
    """Format a single equity's data for the prompt."""
    max_idx = max(col_map.values()) if col_map else 0
    padded = row + [""] * (max_idx + 1 - len(row))

    # Get company name
    company_idx = col_map.get("company_name")
    company = padded[company_idx].strip() if company_idx is not None else ""

    lines = [f"--- {ticker} ({company}) ---"]
    for col_name, col_idx in sorted(col_map.items(), key=lambda x: x[1]):
        if col_name in excluded or col_name.startswith("_"):
            continue
        if col_idx >= len(padded):
            continue
        val = padded[col_idx].strip()
        if not val or val == NULL_VALUE:
            continue
        # Skip the ticker and company_name since they're in the header
        if col_name in ("ticker", "company_name"):
            continue
        lines.append(f"  {col_name}: {val}")

    return "\n".join(lines)


def build_bear_prompt(equity_blocks):
    """Assemble the full bear prompt with all equity data."""
    equity_data = "\n\n".join(equity_blocks)
    return BEAR_PROMPT_TEMPLATE.format(
        count=len(equity_blocks),
        equity_data=equity_data,
    )


# ---------------------------------------------------------------------------
# Gemini API
# ---------------------------------------------------------------------------


def _call_gemini_text(prompt, api_key, model, timeout=GEMINI_TIMEOUT):
    """Call Gemini via curl subprocess, return raw text response."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
        },
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


def call_gemini_bear(prompt, api_key, logger):
    """Call Gemini with retries, return raw text response or None."""
    for attempt in range(MAX_RETRIES):
        try:
            raw = _call_gemini_text(prompt, api_key, GEMINI_MODEL)
            data = json.loads(raw)
            if "error" in data:
                raise Exception(
                    f"{data['error'].get('code')} {data['error'].get('message', '')}"
                )
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return text

        except Exception as exc:
            exc_str = str(exc)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str else 5
                logger.warning(
                    "Gemini call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Gemini call failed (attempt %d/%d), giving up: %s",
                    attempt + 1, MAX_RETRIES, exc,
                )
                return None

    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_bear_results(response_text):
    """
    Parse Gemini response into {ticker: verdict_string} dict.

    Expected lines like:
        NVDA: ✅ Strong margins, accelerating revenue
        SNOW: ❌ Persistent negative FCF margin
    """
    results = {}
    pattern = re.compile(r'^([A-Z][A-Z0-9.]{0,10}):\s*([✅❌].*)$', re.MULTILINE)
    for match in pattern.finditer(response_text):
        ticker = match.group(1).strip()
        verdict = match.group(2).strip()
        results[ticker] = verdict
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Bear Evaluation — Risk Audit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompt and results without writing to the sheet")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== Bear Evaluation started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    # Validate Gemini key
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        logger.error("GEMINI_API_KEY env var is not set")
        sys.exit(1)

    # Read sheet
    service = get_sheets_service()
    all_rows = read_all_rows(service)
    logger.info("Read %d rows from sheet (including headers)", len(all_rows))

    if len(all_rows) < 3:
        logger.error("Sheet has fewer than 3 rows (need headers + data)")
        sys.exit(1)

    # Build column map from row 2 headers
    col_map = {}
    for idx, header in enumerate(all_rows[1]):
        name = header.strip()
        name = HEADER_ALIASES.get(name, name.lower())
        col_map[name] = idx
    logger.info("Column map keys: %s", list(col_map.keys()))

    # Verify bear column exists
    bear_idx = col_map.get("bear")
    if bear_idx is None:
        logger.error("'Bear' column not found in sheet headers. Available: %s",
                      [h.strip() for h in all_rows[1]])
        sys.exit(1)
    bear_col_letter = _col_letter(bear_idx)
    logger.info("Bear column: %s (index %d)", bear_col_letter, bear_idx)

    # Select top 40 green-eligible equities
    top_equities = select_top_eligible(all_rows, col_map)
    logger.info("Selected %d green-eligible equities for bear evaluation", len(top_equities))

    if not top_equities:
        logger.warning("No green-eligible equities found. Nothing to do.")
        return

    top_tickers = {t for _, _, t in top_equities}

    # Build equity data blocks
    equity_blocks = []
    for sheet_row, row, ticker in top_equities:
        block = build_equity_block(row, col_map, ticker)
        equity_blocks.append(block)
        logger.info("  %s (row %d)", ticker, sheet_row)

    # Build prompt
    prompt = build_bear_prompt(equity_blocks)
    logger.info("Prompt length: %d chars for %d equities", len(prompt), len(equity_blocks))

    if args.dry_run:
        logger.info("[DRY RUN] Prompt:\n%s", prompt[:2000] + "..." if len(prompt) > 2000 else prompt)

    # Call Gemini
    logger.info("Calling Gemini %s...", GEMINI_MODEL)
    response_text = call_gemini_bear(prompt, gemini_key, logger)
    if response_text is None:
        logger.error("Gemini call failed. No results to write.")
        sys.exit(1)

    logger.info("Gemini response length: %d chars", len(response_text))

    if args.dry_run:
        logger.info("[DRY RUN] Gemini response:\n%s", response_text)

    # Parse results
    verdicts = parse_bear_results(response_text)
    logger.info("Parsed %d verdicts from Gemini response", len(verdicts))

    passed = sum(1 for v in verdicts.values() if v.startswith("✅"))
    failed = sum(1 for v in verdicts.values() if v.startswith("❌"))
    logger.info("Results: %d ✅, %d ❌", passed, failed)

    if args.dry_run:
        for ticker, verdict in verdicts.items():
            logger.info("  %s: %s", ticker, verdict)
        logger.info("[DRY RUN] Complete. No writes performed.")
        return

    # Build updates for top equities
    updates = []
    matched = 0
    for sheet_row, row, ticker in top_equities:
        verdict = verdicts.get(ticker)
        if verdict:
            updates.append({
                "row": sheet_row,
                "values": {bear_col_letter: verdict},
            })
            matched += 1
        else:
            logger.warning("No verdict found for %s (row %d)", ticker, sheet_row)

    # Clear stale bear values for equities no longer in top 40
    max_idx = max(col_map.values())
    ticker_idx = col_map.get("ticker")
    clears = 0
    for row_offset, row in enumerate(all_rows[2:]):
        padded = row + [""] * (max_idx + 1 - len(row))
        sheet_row = row_offset + 3
        ticker = _extract_ticker(padded[ticker_idx])
        if ticker in top_tickers:
            continue  # handled above
        # Check if bear column has a value
        bear_val = padded[bear_idx].strip() if bear_idx < len(padded) else ""
        if bear_val:
            updates.append({
                "row": sheet_row,
                "values": {bear_col_letter: ""},
            })
            clears += 1

    logger.info("Writing %d verdicts + clearing %d stale bear values", matched, clears)
    write_row_updates(service, updates)

    elapsed = time.time() - start_time
    logger.info(
        "=== Bear Evaluation complete. %d/%d verdicts written, %d stale cleared. (%.1fs) ===",
        matched, len(top_equities), clears, elapsed,
    )


if __name__ == "__main__":
    main()
