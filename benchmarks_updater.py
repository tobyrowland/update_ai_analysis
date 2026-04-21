#!/usr/bin/env python3
"""
Nightly benchmark price refresh.

For every row in the `benchmarks` table, fetches EODHD adjusted closes
between (last stored price_date + 1) and today, appends them to
benchmark_prices, and updates the parent `benchmarks` row's
latest_price / latest_price_date.

Runs from .github/workflows/benchmarks-update.yml at 03:45 UTC — just
after eodhd_updater.py at 03:30 UTC, well before portfolio_valuation.py
at 05:30 UTC so the leaderboard view picks up fresh benchmark data on
the same day.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from db import SupabaseDB

EODHD_BASE_URL = "https://eodhd.com/api"
DELAY_BETWEEN_CALLS = 1  # seconds, match other eodhd scripts


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"benchmarks_updater_{today_str}.txt"

    logger = logging.getLogger("benchmarks_updater")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def eodhd_eod(ticker: str, api_key: str, from_date: str, to_date: str,
              logger: logging.Logger) -> list[dict[str, Any]] | None:
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


def update_benchmark(
    db: SupabaseDB,
    ticker: str,
    display_name: str,
    latest_price_date: str | None,
    inception_date: str,
    eodhd_key: str,
    dry_run: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Refresh a single benchmark. Returns per-ticker stats."""
    today = date.today()

    # Fetch from the day AFTER the last stored price, or from inception if
    # latest_price_date is missing (e.g. empty benchmark_prices for this ticker).
    if latest_price_date:
        from_date = (date.fromisoformat(latest_price_date) + timedelta(days=1)).isoformat()
    else:
        from_date = inception_date

    if from_date > today.isoformat():
        logger.info("  %s: already up to date (last=%s)", ticker, latest_price_date)
        return {"ticker": ticker, "inserted": 0, "skipped": True}

    logger.info("  %s: fetching %s → %s", ticker, from_date, today.isoformat())
    series = eodhd_eod(ticker, eodhd_key, from_date, today.isoformat(), logger)
    if series is None:
        return {"ticker": ticker, "inserted": 0, "error": "fetch_failed"}

    rows = [
        {
            "ticker": ticker,
            "price_date": r["date"],
            "close": float(r["adjusted_close"]),
        }
        for r in series
        if r.get("date") and r.get("adjusted_close") is not None
    ]
    if not rows:
        logger.info("  %s: no new closes in the range", ticker)
        return {"ticker": ticker, "inserted": 0}

    last_row = rows[-1]

    if dry_run:
        logger.info("  %s: dry-run — would insert %d rows (latest %s @ %.4f)",
                    ticker, len(rows), last_row["price_date"], last_row["close"])
        return {"ticker": ticker, "inserted": len(rows), "dry_run": True}

    # Upsert prices, then bump the parent benchmarks row.
    db.client.table("benchmark_prices").upsert(
        rows, on_conflict="ticker,price_date"
    ).execute()

    db.client.table("benchmarks").update({
        "latest_price": last_row["close"],
        "latest_price_date": last_row["price_date"],
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("ticker", ticker).execute()

    logger.info("  %s: +%d rows, latest %s @ %.4f",
                ticker, len(rows), last_row["price_date"], last_row["close"])
    return {"ticker": ticker, "inserted": len(rows)}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", type=str, default=None,
                        help="Update only this ticker")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch but don't write")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== benchmarks_updater started (dry_run=%s) ===", args.dry_run)

    eodhd_key = os.environ.get("EODHD_API_KEY")
    if not eodhd_key:
        logger.error("EODHD_API_KEY env var is not set")
        return 1

    db = SupabaseDB()

    start = time.time()
    query = db.client.table("benchmarks").select(
        "ticker, display_name, inception_date, latest_price_date"
    )
    if args.ticker:
        query = query.eq("ticker", args.ticker)
    resp = query.execute()
    benchmarks = resp.data or []

    if not benchmarks:
        logger.warning("No benchmarks configured. Run bootstrap_benchmarks.py first.")
        return 0

    stats = {
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "details": {"tickers": []},
    }

    for i, bench in enumerate(benchmarks):
        if i > 0:
            time.sleep(DELAY_BETWEEN_CALLS)
        result = update_benchmark(
            db,
            bench["ticker"],
            bench["display_name"],
            bench.get("latest_price_date"),
            bench["inception_date"],
            eodhd_key,
            args.dry_run,
            logger,
        )
        stats["details"]["tickers"].append(result)
        if result.get("error"):
            stats["errors"] += 1
        elif result.get("skipped"):
            stats["skipped"] += 1
        else:
            stats["updated"] += 1

    stats["duration_secs"] = round(time.time() - start, 2)
    logger.info(
        "Done: updated=%d skipped=%d errors=%d duration=%.1fs",
        stats["updated"], stats["skipped"], stats["errors"], stats["duration_secs"],
    )

    if not args.dry_run:
        db.log_run("benchmarks_updater", stats)

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
