#!/usr/bin/env python3
"""
Nightly benchmark price refresh.

For every row in the `benchmarks` table, fetches daily adjusted closes
from Yahoo Finance between (last stored price_date + 1) and today,
appends them to benchmark_prices, and updates the parent `benchmarks`
row's latest_price / latest_price_date.

Yahoo is used instead of EODHD because the project's EODHD plan doesn't
cover /api/eod/ for ETFs (403 Forbidden). Matches the same data source
used by price_sales_updater.py.

Runs from .github/workflows/benchmarks-update.yml at 03:45 UTC — just
after eodhd_updater.py at 03:30 UTC, well before portfolio_valuation.py
at 05:30 UTC so the leaderboard view picks up fresh benchmark data on
the same day.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from db import SupabaseDB

DELAY_BETWEEN_CALLS = 1  # seconds, match other scrapers


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


def yahoo_ticker_for(storage_ticker: str) -> str:
    """Translate a storage ticker (EODHD-style, e.g. 'SPY.US') to Yahoo's symbol."""
    if storage_ticker.endswith(".US"):
        return storage_ticker[:-3]
    return storage_ticker


def yahoo_daily(
    storage_ticker: str,
    from_date: date,
    to_date: date,
    logger: logging.Logger,
) -> list[dict[str, Any]] | None:
    """Fetch daily adjusted closes from Yahoo Finance between two dates."""
    yticker = yahoo_ticker_for(storage_ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yticker}"
    period1 = int(datetime.combine(from_date, datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(to_date + timedelta(days=1), datetime.min.time(),
                                   tzinfo=timezone.utc).timestamp())
    params = {"interval": "1d", "period1": period1, "period2": period2}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result") or []
            if not result:
                logger.warning("Yahoo %s: empty result", yticker)
                return None
            ts = result[0].get("timestamp") or []
            adj = (
                result[0].get("indicators", {})
                .get("adjclose", [{}])[0]
                .get("adjclose", [])
            )
            if not ts or not adj:
                logger.warning("Yahoo %s: no timestamp / adjclose", yticker)
                return None

            rows = []
            for t, c in zip(ts, adj):
                if c is None:
                    continue
                d = datetime.fromtimestamp(t, tz=timezone.utc).date()
                rows.append({"date": d.isoformat(), "adjusted_close": float(c)})
            return rows if rows else None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            wait = 2 ** (attempt + 1)
            logger.warning("Yahoo %s attempt %d/3 failed (%s), retrying in %ds",
                           yticker, attempt + 1, type(e).__name__, wait)
            time.sleep(wait)
        except Exception as e:
            logger.error("Yahoo fetch failed for %s: %s", yticker, e)
            return None
    return None


def update_benchmark(
    db: SupabaseDB,
    ticker: str,
    display_name: str,
    latest_price_date: str | None,
    inception_date: str,
    dry_run: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Refresh a single benchmark. Returns per-ticker stats."""
    today = date.today()

    if latest_price_date:
        from_date = date.fromisoformat(latest_price_date) + timedelta(days=1)
    else:
        from_date = date.fromisoformat(inception_date)

    if from_date > today:
        logger.info("  %s: already up to date (last=%s)", ticker, latest_price_date)
        return {"ticker": ticker, "inserted": 0, "skipped": True}

    logger.info("  %s: fetching %s → %s", ticker, from_date.isoformat(), today.isoformat())
    series = yahoo_daily(ticker, from_date, today, logger)
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
