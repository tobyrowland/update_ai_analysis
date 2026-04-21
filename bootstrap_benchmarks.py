#!/usr/bin/env python3
"""
One-off seed for benchmark portfolios (S&P 500, MSCI World).

For each (ticker, display_name) below:
  1. Picks an anchor date: MIN(inception_date) across agent_accounts,
     falling back to today if no accounts exist yet.
  2. Hits EODHD /eod/{ticker} for the full range (anchor → today) and
     inserts every (price_date, adjusted_close) into benchmark_prices.
  3. Upserts the benchmarks row with inception_date/price and
     latest_price/latest_price_date derived from the fetched series.

Idempotent — re-running refreshes prices and re-anchors inception only
if necessary. Usage:

    python bootstrap_benchmarks.py                          # seed both
    python bootstrap_benchmarks.py --ticker SPY.US          # one only
    python bootstrap_benchmarks.py --dry-run                # compute only

Env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY, EODHD_API_KEY.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from typing import Any

import requests
from dotenv import load_dotenv

from db import SupabaseDB

EODHD_BASE_URL = "https://eodhd.com/api"

DEFAULT_BENCHMARKS = [
    ("SPY.US", "S&P 500 (SPY)"),
    ("URTH.US", "MSCI World (URTH)"),
]


def eodhd_eod(ticker: str, api_key: str, from_date: str, to_date: str,
              logger: logging.Logger) -> list[dict[str, Any]] | None:
    """Fetch EODHD end-of-day prices for a ticker over a date range."""
    url = f"{EODHD_BASE_URL}/eod/{ticker}"
    params = {
        "api_token": api_key,
        "fmt": "json",
        "from": from_date,
        "to": to_date,
        "period": "d",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.warning("EODHD %s: unexpected payload shape", ticker)
            return None
        return data
    except Exception as e:
        logger.error("EODHD EOD fetch failed for %s: %s", ticker, e)
        return None


def get_anchor_date(db: SupabaseDB, logger: logging.Logger) -> date:
    """Earliest agent inception date, or today if no accounts exist yet."""
    resp = (
        db.client.table("agent_accounts")
        .select("inception_date")
        .order("inception_date", desc=False)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if rows and rows[0].get("inception_date"):
        anchor = date.fromisoformat(rows[0]["inception_date"])
        logger.info("Anchor date = %s (earliest agent inception)", anchor)
        return anchor
    logger.info("No agent accounts yet — anchoring to today")
    return date.today()


def seed_benchmark(
    db: SupabaseDB,
    ticker: str,
    display_name: str,
    anchor: date,
    eodhd_key: str,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    """Fetch EOD prices and seed benchmarks + benchmark_prices. Returns True on success."""
    today = date.today()
    logger.info("Seeding %s (%s) from %s → %s", ticker, display_name, anchor, today)

    series = eodhd_eod(ticker, eodhd_key, anchor.isoformat(), today.isoformat(), logger)
    if not series:
        logger.error("%s: no EOD data returned; skipping", ticker)
        return False

    # Sort by date ascending (EODHD returns ascending by default but be defensive).
    series.sort(key=lambda r: r.get("date", ""))

    # First row at or after anchor → inception row.
    first = next((r for r in series if r.get("date") and r.get("adjusted_close") is not None), None)
    last = next(
        (r for r in reversed(series) if r.get("date") and r.get("adjusted_close") is not None),
        None,
    )
    if not first or not last:
        logger.error("%s: no valid close prices in response", ticker)
        return False

    inception_date = date.fromisoformat(first["date"])
    inception_price = float(first["adjusted_close"])
    latest_date = date.fromisoformat(last["date"])
    latest_price = float(last["adjusted_close"])

    logger.info(
        "  first=%s @ %.4f   last=%s @ %.4f   (%d rows)",
        inception_date, inception_price, latest_date, latest_price, len(series),
    )

    if dry_run:
        logger.info("  dry-run — skipping writes")
        return True

    # Upsert benchmark row first (so FK on benchmark_prices is satisfied).
    db.client.table("benchmarks").upsert(
        {
            "ticker": ticker,
            "display_name": display_name,
            "inception_date": inception_date.isoformat(),
            "inception_price": inception_price,
            "latest_price": latest_price,
            "latest_price_date": latest_date.isoformat(),
        },
        on_conflict="ticker",
    ).execute()

    # Bulk-upsert daily prices — batch to stay under Supabase payload limits.
    rows = [
        {
            "ticker": ticker,
            "price_date": r["date"],
            "close": float(r["adjusted_close"]),
        }
        for r in series
        if r.get("date") and r.get("adjusted_close") is not None
    ]
    BATCH = 500
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        db.client.table("benchmark_prices").upsert(
            chunk, on_conflict="ticker,price_date"
        ).execute()
    logger.info("  wrote %d price rows", len(rows))
    return True


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", type=str, default=None,
                        help="Seed only this ticker (e.g. SPY.US)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + compute but don't write")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("bootstrap_benchmarks")

    eodhd_key = os.environ.get("EODHD_API_KEY")
    if not eodhd_key:
        logger.error("EODHD_API_KEY env var is not set")
        return 1

    db = SupabaseDB()
    anchor = get_anchor_date(db, logger)

    targets = DEFAULT_BENCHMARKS
    if args.ticker:
        targets = [(t, n) for (t, n) in DEFAULT_BENCHMARKS if t == args.ticker]
        if not targets:
            logger.error("Unknown ticker: %s (must be one of %s)",
                         args.ticker, [t for t, _ in DEFAULT_BENCHMARKS])
            return 1

    ok = 0
    for ticker, name in targets:
        if seed_benchmark(db, ticker, name, anchor, eodhd_key, args.dry_run, logger):
            ok += 1

    logger.info("Done. %d/%d benchmarks seeded.", ok, len(targets))
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
