#!/usr/bin/env python3
"""
Bull Evaluation — The Smash-Hit Scout.

Sends the top 100 green-eligible equities from AI Analysis to Claude Opus 4.6
for a growth/venture equity audit. Looks for companies with powerful fundamental
trajectories in massive or rapidly expanding verticals.
Results are written to the 'Bull' column in the AI Analysis sheet.

Schedule: Mondays 08:30 UTC (after bear_evaluation).
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
CLAUDE_MODEL = "claude-opus-4-6"
CLAUDE_TIMEOUT = 300  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 15
TOP_N = 100
NULL_VALUE = "\u2014"

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
    "Bull":                 "bull",
    "Bull Eval":            "bull",
}

# Columns to EXCLUDE from the data sent to Claude
EXCLUDED_COLUMNS = {
    "status", "composite_score", "price", "ps_now",
    "price_pct_of_52w_high", "price_%_of_52w_high",
    "perf_52w_vs_spy", "rating", "ai", "data", "scoring", "price data",
    "price_data", "bear", "bull", "history_json",
}

# ---------------------------------------------------------------------------
# Bull prompt — The Smash-Hit Scout
# ---------------------------------------------------------------------------

BULL_PROMPT_TEMPLATE = """\
Role: You are a Growth-Focused Venture Equity Analyst. Your mission is to find \
"Smash Hits"\u2014companies with powerful fundamental trajectories operating in \
massive or rapidly expanding verticals. You are looking for the next dominant \
category leaders.

Your Objective: Review every ticker. Assign either a Green Tick (\u2705) or a \
Red Cross (\u274c) to each one.

Evaluation Framework:

The Internal Engine: Use only the provided data/analysis to evaluate the company's \
specific trajectory (revenue acceleration, margin expansion, or competitive wins).

The External Context: You may use your internal knowledge of industry sectors \
(e.g., AdTech, SaaS, BioTech, Energy) to provide context on market size, sector \
difficulty, and whether the vertical has "Smash Hit" potential.

The "Green Tick" (\u2705) Criteria:
Assign a \u2705 only if the company meets both of these requirements:

Fundamental Velocity: The provided data shows a company "firing on all cylinders" \
(e.g., accelerating sales, dominant product-market fit, or structural efficiency).

High-Ceiling Vertical: Based on your industry knowledge, the company operates in \
a sector that is either massive or poised for a generational shift. It isn't just \
a "good business"; it's a business that could be 10x larger in a decade.

The "Red Cross" (\u274c) Criteria:
Assign a \u274c if:

Small Pond: The company is performing well but in a niche, stagnant, or "difficult" \
sector with limited upside (e.g., high-competition/low-margin legacy industries).

Friction: The provided analysis mentions slowing momentum or "ceiling" effects.

"Just a Business": The company is solid but lacks the "Smash Hit" DNA\u2014it's an \
incremental grower, not a category disruptor.

Output Format:
For every stock in the list, provide the result in this exact format. \
Use the EXACT ticker as shown in the data header (including numbers and slashes):

TICKER: \u2705 (Briefly explain why this vertical/trajectory combo could be a \
"Smash Hit")

TICKER: \u274c (Briefly explain why it lacks the "Smash Hit" potential, citing \
sector headwinds or fundamental friction)

Strict Constraints:

AI is Secondary: Treat AI as just one possible tool for growth, not the sole requirement.

Data Integrity: Do not invent company-specific data; use only what is provided.

You MUST output a verdict for ALL {count} equities.

=== EQUITIES TO EVALUATE ({count}) ===

{equity_data}

=== END OF DATA ===

Now evaluate each equity above. Output ONLY the verdict lines, one per ticker. \
You must cover ALL {count} tickers."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"bull_eval_{date.today().isoformat()}.txt"

    logger = logging.getLogger("bull_evaluation")
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
    """Convert 0-based column index to Excel-style letter(s). 0->A, 25->Z, 26->AA."""
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
    Filter rows with green status, sort by composite_score desc, return top N.

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
        if "\U0001f7e2" not in status:  # green circle emoji
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


def build_bull_prompt(equity_blocks):
    """Assemble the full bull prompt with all equity data."""
    equity_data = "\n\n".join(equity_blocks)
    return BULL_PROMPT_TEMPLATE.format(
        count=len(equity_blocks),
        equity_data=equity_data,
    )


# ---------------------------------------------------------------------------
# Claude API (Anthropic)
# ---------------------------------------------------------------------------


def _call_claude(prompt, api_key, model, timeout=CLAUDE_TIMEOUT):
    """Call Claude via curl subprocess, return raw JSON response."""
    url = "https://api.anthropic.com/v1/messages"
    payload = json.dumps({
        "model": model,
        "max_tokens": 32768,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(payload)
        payload_path = f.name
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", url,
             "-H", "Content-Type: application/json",
             "-H", f"x-api-key: {api_key}",
             "-H", "anthropic-version: 2023-06-01",
             "-d", f"@{payload_path}",
             "--max-time", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return result.stdout
    finally:
        os.unlink(payload_path)


def call_claude_bull(prompt, api_key, logger):
    """Call Claude with retries, return response text or None."""
    for attempt in range(MAX_RETRIES):
        try:
            raw = _call_claude(prompt, api_key, CLAUDE_MODEL)
            if not raw or not raw.strip():
                raise Exception("Empty response from curl (possible timeout)")
            logger.info("Raw API response length: %d chars", len(raw))
            data = json.loads(raw)
            if data.get("type") == "error":
                err = data.get("error", {})
                raise Exception(
                    f"{err.get('type', 'unknown')}: {err.get('message', '')}"
                )
            # Claude Messages API: content is a list of content blocks
            content_blocks = data.get("content", [])
            text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
            text = "\n".join(text_parts).strip()
            if not text:
                raise Exception("Empty response text from Claude")

            stop = data.get("stop_reason", "")
            logger.info("Claude stop_reason: %s", stop)
            if stop == "max_tokens":
                logger.warning("Response was truncated (hit max_tokens)!")

            return text

        except Exception as exc:
            exc_str = str(exc)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY if "overloaded" in exc_str or "rate" in exc_str.lower() else 5
                logger.warning(
                    "Claude call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Claude call failed (attempt %d/%d), giving up: %s",
                    attempt + 1, MAX_RETRIES, exc,
                )
                return None

    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_bull_results(response_text):
    """
    Parse Claude response into {ticker: verdict_string} dict.

    Handles tickers that start with letters, numbers, or contain slashes.
    Examples: NVDA, 6857, VISTA/A, A3EGAB, 896047
    """
    results = {}
    pattern = re.compile(
        r'^([A-Z0-9][A-Z0-9./]*?):\s*([\u2705\u274c].*)$',
        re.MULTILINE,
    )
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

    parser = argparse.ArgumentParser(description="Bull Evaluation - Smash-Hit Scout")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompt and results without writing to the sheet")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== Bull Evaluation started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    # Validate Claude API key
    claude_key = os.environ.get("ANTHROPIC_API_KEY")
    if not claude_key:
        logger.error("ANTHROPIC_API_KEY env var is not set")
        sys.exit(1)
    logger.info("ANTHROPIC_API_KEY is set (length=%d)", len(claude_key))

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
    logger.info("Column map: %s", {k: v for k, v in col_map.items()})

    # Verify bull column exists
    bull_idx = col_map.get("bull")
    if bull_idx is None:
        logger.error("'Bull' column not found in sheet headers. Available: %s",
                      [h.strip() for h in all_rows[1]])
        sys.exit(1)
    bull_col_letter = _col_letter(bull_idx)
    logger.info("Bull column: %s (index %d)", bull_col_letter, bull_idx)

    # Select top green-eligible equities
    top_equities = select_top_eligible(all_rows, col_map)
    logger.info("Selected %d green-eligible equities for bull evaluation", len(top_equities))

    if not top_equities:
        logger.warning("No green-eligible equities found. Nothing to do.")
        status_idx = col_map.get("status")
        if status_idx is not None:
            for i, row in enumerate(all_rows[2:7]):
                padded = row + [""] * (status_idx + 1 - len(row))
                logger.info("  Row %d status: [%s] repr=%r", i + 3,
                            padded[status_idx].strip(), padded[status_idx])
        return

    top_tickers = {t for _, _, t in top_equities}

    # Build equity data blocks
    equity_blocks = []
    for sheet_row, row, ticker in top_equities:
        block = build_equity_block(row, col_map, ticker)
        equity_blocks.append(block)
        logger.info("  %s (row %d)", ticker, sheet_row)

    # Build prompt
    prompt = build_bull_prompt(equity_blocks)
    logger.info("Prompt length: %d chars for %d equities", len(prompt), len(equity_blocks))

    if args.dry_run:
        logger.info("[DRY RUN] Prompt:\n%s", prompt[:2000] + "..." if len(prompt) > 2000 else prompt)

    # Call Claude
    logger.info("Calling Claude %s...", CLAUDE_MODEL)
    response_text = call_claude_bull(prompt, claude_key, logger)
    if response_text is None:
        logger.error("Claude call failed. No results to write.")
        sys.exit(1)

    logger.info("Claude response length: %d chars", len(response_text))
    logger.info("Claude response preview: %s", response_text[:1000])

    if args.dry_run:
        logger.info("[DRY RUN] Claude response:\n%s", response_text)

    # Parse results
    verdicts = parse_bull_results(response_text)
    logger.info("Parsed %d verdicts from Claude response", len(verdicts))

    if not verdicts:
        logger.error("No verdicts parsed! Response may not match expected format.")
        logger.error("First 2000 chars of response:\n%s", response_text[:2000])
        sys.exit(1)

    passed = sum(1 for v in verdicts.values() if "\u2705" in v)
    failed = sum(1 for v in verdicts.values() if "\u274c" in v)
    logger.info("Results: %d pass, %d fail", passed, failed)

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
                "values": {bull_col_letter: verdict},
            })
            matched += 1
        else:
            logger.warning("No verdict found for %s (row %d)", ticker, sheet_row)

    # Clear stale bull values for equities no longer in top list
    max_idx = max(col_map.values())
    ticker_idx = col_map.get("ticker")
    clears = 0
    for row_offset, row in enumerate(all_rows[2:]):
        padded = row + [""] * (max_idx + 1 - len(row))
        sheet_row = row_offset + 3
        ticker = _extract_ticker(padded[ticker_idx])
        if ticker in top_tickers:
            continue  # handled above
        # Check if bull column has a value
        bull_val = padded[bull_idx].strip() if bull_idx < len(padded) else ""
        if bull_val:
            updates.append({
                "row": sheet_row,
                "values": {bull_col_letter: ""},
            })
            clears += 1

    logger.info("Writing %d verdicts + clearing %d stale bull values", matched, clears)
    write_row_updates(service, updates)

    elapsed = time.time() - start_time
    logger.info(
        "=== Bull Evaluation complete. %d/%d verdicts written, %d stale cleared. (%.1fs) ===",
        matched, len(top_equities), clears, elapsed,
    )


if __name__ == "__main__":
    main()
