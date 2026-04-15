#!/usr/bin/env python3
"""
Build Portfolio — Populate portfolio with dual-positive equities.

Reads companies table from Supabase, finds equities where both bear and bull
columns have a checkmark, deduplicates by company (favouring ADRs/US listings),
and marks them as in_portfolio with relevant ranking.

Schedule: Sundays 08:00 UTC (after bear + bull evaluations).
"""

import argparse
import logging
import re
import sys
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv

from db import SupabaseDB, NULL_VALUE
from exchanges import TV_TO_GOOGLE_FINANCE

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD = 0.80

# US exchanges — ADRs and primary US listings live here
US_EXCHANGES = {
    "NYSE", "NASDAQ", "AMEX", "NYSEARCA", "BATS", "ARCA",
}

# Corporate suffixes to strip when normalising company names
CORPORATE_SUFFIXES = re.compile(
    r'\b('
    r'inc|incorporated|corp|corporation|ltd|limited|llc|plc|'
    r'sa|s\.a\.|se|s\.e\.|nv|n\.v\.|ag|a\.g\.|'
    r'co|company|group|holdings|holding|enterprises|'
    r'international|intl|technologies|technology|tech|'
    r'systems|solutions|therapeutics|pharmaceuticals|pharma|'
    r'biosciences|biopharma|medical|healthcare|'
    r'class\s*[a-z]|cl\s*[a-z]|adr'
    r')\b',
    re.IGNORECASE,
)

# Columns to include in portfolio output (for logging / dry-run display)
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
# Helpers
# ---------------------------------------------------------------------------


def _is_us_exchange(exchange_val):
    """Check if exchange is a US exchange (ADR-friendly)."""
    return str(exchange_val).strip().upper() in US_EXCHANGES


# ---------------------------------------------------------------------------
# Revenue parsing
# ---------------------------------------------------------------------------


def _parse_revenue_amount(s):
    """
    Parse a formatted revenue string like '$5.2B', '$480M', '$1.5K' into a float.
    Returns the value in dollars or None.
    """
    s = s.strip()
    match = re.match(r'^-?\$?([\d.]+)\s*([BMK])?$', s, re.IGNORECASE)
    if not match:
        return None
    num = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    if suffix == "B":
        return num * 1e9
    elif suffix == "M":
        return num * 1e6
    elif suffix == "K":
        return num * 1e3
    return num


def _compute_ttm_revenue(quarterly_revenue_str):
    """
    Parse quarterly_revenue column and sum last 4 quarters for TTM revenue.

    Format: "$5.2B (2026-03-31) | $4.8B (2025-12-31) | $4.5B (2025-09-30) | ..."
    Returns TTM revenue in dollars or None.
    """
    if not quarterly_revenue_str or quarterly_revenue_str == NULL_VALUE:
        return None

    parts = quarterly_revenue_str.split("|")
    revenues = []
    for part in parts[:4]:  # take first 4 (most recent)
        # Extract the dollar amount before the parenthesised date
        part = part.strip()
        amount_match = re.match(r'^(-?\$?[\d.]+[BMK]?)', part, re.IGNORECASE)
        if amount_match:
            rev = _parse_revenue_amount(amount_match.group(1))
            if rev is not None:
                revenues.append(rev)

    if len(revenues) < 4:
        return None  # not enough quarters for TTM

    return sum(revenues)


def _build_ps_formula(ticker, exchange, ttm_revenue):
    """
    Build a GOOGLEFINANCE formula for live P/S ratio.

    =GOOGLEFINANCE("EXCHANGE:TICKER","marketcap") / TTM_REVENUE
    """
    gf_exchange = TV_TO_GOOGLE_FINANCE.get(exchange.upper(), exchange.upper())
    gf_ticker = f"{gf_exchange}:{ticker}"
    # Round TTM revenue to avoid floating point noise in formula
    ttm_int = round(ttm_revenue)
    return f'=GOOGLEFINANCE("{gf_ticker}","marketcap")/{ttm_int}'


# ---------------------------------------------------------------------------
# Company name normalisation & fuzzy matching
# ---------------------------------------------------------------------------


def _normalise_company(name):
    """
    Normalise a company name for dedup comparison.
    """
    s = name.strip().upper()
    s = CORPORATE_SUFFIXES.sub("", s)
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _names_match(name_a, name_b):
    """Check if two company names refer to the same company."""
    norm_a = _normalise_company(name_a)
    norm_b = _normalise_company(name_b)

    if norm_a == norm_b:
        return True

    if norm_a and norm_b:
        shorter, longer = sorted([norm_a, norm_b], key=len)
        if longer.startswith(shorter) and len(shorter) >= 3:
            return True

    ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
    return ratio >= FUZZY_THRESHOLD


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _pick_best(candidates, logger):
    """From a list of candidate dicts for the same company, pick the best one."""
    if len(candidates) == 1:
        return candidates[0]

    tickers = [c["ticker"] for c in candidates]
    names = [c.get("company_name", "?") for c in candidates]
    logger.info("  Dedup: [%s] tickers: %s", names[0], tickers)

    us_candidates = [
        c for c in candidates
        if _is_us_exchange(c.get("exchange", ""))
    ]

    pool = us_candidates if us_candidates else candidates
    best = max(
        pool,
        key=lambda c: SupabaseDB.safe_float(c.get("composite_score")) or 0.0,
    )
    label = "ADR" if us_candidates else "best score"
    logger.info("    -> Picked %s: %s (%s)", label, best["ticker"],
                best.get("exchange", "?"))
    return best


def deduplicate_by_company(entries, logger):
    """
    Deduplicate entries by company name using fuzzy matching.
    entries is a list of company dicts.
    """
    items = []
    for company in entries:
        raw_name = company.get("company_name", "").strip()
        norm = _normalise_company(raw_name) if raw_name else company["ticker"]
        items.append((company, raw_name, norm))

    groups = []
    assigned = [False] * len(items)

    for i in range(len(items)):
        if assigned[i]:
            continue
        group = [items[i]]
        assigned[i] = True
        for j in range(i + 1, len(items)):
            if assigned[j]:
                continue
            if _names_match(items[i][1], items[j][1]):
                group.append(items[j])
                assigned[j] = True
        groups.append(group)

    deduped = []
    for group in groups:
        candidates = [company for company, _, _ in group]
        best = _pick_best(candidates, logger)
        deduped.append(best)

    return deduped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Build Portfolio from dual-positive equities")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without writing to the database")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== Build Portfolio started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    db = SupabaseDB()

    # ---------------------------------------------------------------
    # Read all companies from Supabase
    # ---------------------------------------------------------------
    all_companies = db.get_all_companies()
    logger.info("Read %d companies from Supabase", len(all_companies))

    if not all_companies:
        logger.error("No companies found in database")
        sys.exit(1)

    # ---------------------------------------------------------------
    # Find dual-positive equities (both bear and bull have checkmark)
    # ---------------------------------------------------------------
    dual_positive = []

    for company in all_companies:
        bear_val = str(company.get("bear", "") or "").strip()
        bull_val = str(company.get("bull", "") or "").strip()

        if "\u2705" in bear_val and "\u2705" in bull_val:
            ticker = company.get("ticker", "")
            if not ticker:
                continue
            dual_positive.append(company)

    logger.info("Found %d equities with both Bear \u2705 and Bull \u2705", len(dual_positive))

    if not dual_positive:
        logger.warning("No dual-positive equities found. Portfolio will be cleared.")

    # ---------------------------------------------------------------
    # Deduplicate by company name (fuzzy match, favour ADR / US listing)
    # ---------------------------------------------------------------
    if dual_positive:
        before_count = len(dual_positive)
        dual_positive = deduplicate_by_company(dual_positive, logger)
        after_count = len(dual_positive)
        if before_count != after_count:
            logger.info("Deduplicated: %d -> %d (removed %d duplicates)",
                        before_count, after_count, before_count - after_count)

    # Sort by composite_score descending
    dual_positive.sort(
        key=lambda c: SupabaseDB.safe_float(c.get("composite_score")) or 0.0,
        reverse=True,
    )

    # ---------------------------------------------------------------
    # Fetch price_sales data for 12m_median
    # ---------------------------------------------------------------
    price_sales_map = db.get_all_price_sales()

    # ---------------------------------------------------------------
    # Build portfolio list and compute TTM-based P/S formula
    # ---------------------------------------------------------------
    portfolio_tickers = set()
    portfolio_updates = []

    for rank, company in enumerate(dual_positive, start=1):
        ticker = company["ticker"]
        exchange = company.get("exchange", "") or ""
        portfolio_tickers.add(ticker)

        # Compute TTM revenue for reference (Google Finance formula preserved
        # in ps_formula field for any downstream sheet integrations)
        qrev_str = company.get("quarterly_revenue", "") or ""
        ttm_revenue = _compute_ttm_revenue(qrev_str)

        ps_formula = None
        if ttm_revenue is not None and ttm_revenue > 0:
            ps_formula = _build_ps_formula(ticker, exchange, ttm_revenue)
            logger.info("    %s: live P/S formula (TTM rev: $%.0f)", ticker, ttm_revenue)

        # Enrich with 12m_median from price_sales table
        ps_data = price_sales_map.get(ticker, {})
        median_12m = ps_data.get("median_12m")

        update_row = {
            "ticker": ticker,
            "in_portfolio": True,
            "portfolio_sort_order": rank,
        }
        # Store formula for downstream use if available
        if ps_formula is not None:
            update_row["ps_formula"] = ps_formula

        portfolio_updates.append(update_row)

        score_display = SupabaseDB.safe_float(company.get("composite_score"))
        logger.info("  #%d %s (%s) score: %s", rank, ticker, exchange,
                    f"{score_display:.2f}" if score_display is not None else "?")

    if args.dry_run:
        logger.info("[DRY RUN] Would mark %d tickers as in_portfolio", len(portfolio_updates))
        for row in portfolio_updates:
            logger.info("  #%s %s", row.get("portfolio_sort_order", "?"), row["ticker"])
        logger.info("[DRY RUN] Complete. No writes performed.")
        return

    # ---------------------------------------------------------------
    # Write to Supabase: clear old portfolio flags, then set new ones
    # ---------------------------------------------------------------

    # First, clear in_portfolio for all companies that are currently in portfolio
    # but are no longer dual-positive
    all_tickers = {c["ticker"] for c in all_companies}
    tickers_to_clear = all_tickers - portfolio_tickers
    if tickers_to_clear:
        clear_rows = [
            {"ticker": t, "in_portfolio": False, "portfolio_sort_order": None}
            for t in tickers_to_clear
        ]
        # Batch upsert in chunks to avoid oversized requests
        CHUNK_SIZE = 500
        for i in range(0, len(clear_rows), CHUNK_SIZE):
            chunk = clear_rows[i:i + CHUNK_SIZE]
            db.upsert_companies_batch(chunk)
        logger.info("Cleared in_portfolio flag for %d companies", len(tickers_to_clear))

    # Write new portfolio entries
    if portfolio_updates:
        db.upsert_companies_batch(portfolio_updates)
        logger.info("Marked %d companies as in_portfolio", len(portfolio_updates))
    else:
        logger.info("No portfolio entries to write")

    # Log the run
    elapsed = time.time() - start_time
    db.log_run("build_portfolio", {
        "updated": len(portfolio_updates),
        "skipped": 0,
        "errors": 0,
        "duration_secs": round(elapsed, 1),
        "details": {
            "dual_positive_found": len(dual_positive),
            "portfolio_written": len(portfolio_updates),
        },
    })

    logger.info(
        "=== Build Portfolio complete. %d equities marked as in_portfolio. (%.1fs) ===",
        len(portfolio_updates), elapsed,
    )


if __name__ == "__main__":
    main()
