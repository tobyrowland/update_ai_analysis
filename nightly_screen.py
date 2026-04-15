#!/usr/bin/env python3
"""
Nightly TradingView Screen → Supabase Ingest.

Runs the TradingView screener and adds any new tickers to the companies table.
Also backfills country/sector for existing tickers where missing.

Schedule: 03:00 UTC daily (before eodhd_updater at 03:30).
"""

import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from db import SupabaseDB
from tv_screen import run_tradingview_screen, fetch_sector_data

load_dotenv()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"nightly_screen_{date.today().isoformat()}.txt"

    logger = logging.getLogger("nightly_screen")
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
# Main
# ---------------------------------------------------------------------------


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Nightly Screen — %s", date.today().isoformat())
    logger.info("=" * 60)

    db = SupabaseDB()

    # Step 1: TradingView screen
    screened = run_tradingview_screen(logger)
    logger.info("TradingView returned %d equities", len(screened))

    # Build lookup of screened tickers
    all_equities = {}
    for eq in screened:
        all_equities[eq["ticker"].upper()] = eq

    logger.info("Universe: %d equities", len(all_equities))

    # Step 2: Read existing tickers from DB
    existing = db.get_all_companies(
        columns="ticker, exchange, country, sector"
    )
    existing_tickers = {row["ticker"] for row in existing}
    existing_map = {row["ticker"]: row for row in existing}

    logger.info("DB has %d existing tickers", len(existing_tickers))

    # Step 3: Identify new tickers and missing fields
    new_tickers = []
    field_updates = []

    for ticker, eq in all_equities.items():
        if ticker not in existing_tickers:
            new_tickers.append(eq)
        else:
            # Check if country/sector/exchange are missing and we have them
            existing_row = existing_map.get(ticker, {})
            updates = {}
            if not existing_row.get("country") and eq.get("country"):
                updates["country"] = eq["country"]
            if not existing_row.get("sector") and eq.get("sector"):
                updates["sector"] = eq["sector"]
            if not existing_row.get("exchange") and eq.get("exchange"):
                updates["exchange"] = eq["exchange"]
            if updates:
                field_updates.append({"ticker": ticker, "fields": updates})

    sector_via_screen = sum(1 for u in field_updates if "sector" in u["fields"])
    logger.info("New tickers to add: %d", len(new_tickers))
    logger.info(
        "Existing tickers with missing fields to update: %d (%d with sector)",
        len(field_updates),
        sector_via_screen,
    )

    # Step 4: Insert new tickers
    if new_tickers:
        rows = []
        for eq in new_tickers:
            rows.append({
                "ticker": eq["ticker"],
                "exchange": eq.get("exchange", ""),
                "company_name": eq.get("company_name", ""),
                "country": eq.get("country", ""),
                "sector": eq.get("sector", ""),
                "in_tv_screen": True,
            })
        db.upsert_companies_batch(rows)
        logger.info("Inserted %d new tickers", len(new_tickers))

    # Step 5: Update missing fields for existing tickers
    if field_updates:
        for upd in field_updates:
            db.upsert_company(upd["ticker"], upd["fields"])
        logger.info("Updated missing fields for %d existing tickers", len(field_updates))

    # Step 6: Backfill sector via TradingView for tickers still missing it
    updated_tickers = {u["ticker"] for u in field_updates if "sector" in u["fields"]}
    tickers_needing_sector = [
        (row["ticker"], row.get("exchange", ""))
        for row in existing
        if not row.get("sector") and row["ticker"] not in updated_tickers
    ]

    logger.info(
        "Tickers still missing sector after screen merge: %d",
        len(tickers_needing_sector),
    )

    if tickers_needing_sector:
        if len(tickers_needing_sector) <= 20:
            logger.info(
                "Missing sector tickers: %s",
                [(t, e) for t, e in tickers_needing_sector],
            )

        sector_data = fetch_sector_data(tickers_needing_sector, logger)
        logger.info(
            "TradingView returned sector for %d / %d tickers",
            len(sector_data),
            len(tickers_needing_sector),
        )

        sector_count = 0
        for ticker, exchange in tickers_needing_sector:
            sector = sector_data.get(ticker.upper(), "")
            if sector:
                db.upsert_company(ticker, {"sector": sector})
                sector_count += 1
            else:
                logger.debug(
                    "No sector found for %s (exchange=%s)", ticker, exchange
                )

        if sector_count:
            logger.info(
                "Backfilled sector for %d tickers via TradingView lookup",
                sector_count,
            )

    # Step 7: Mark which tickers are in the current TV screen
    screened_tickers = set(all_equities.keys())
    mark_rows = []
    for row in existing:
        in_screen = row["ticker"] in screened_tickers
        if row.get("in_tv_screen") != in_screen:
            mark_rows.append({"ticker": row["ticker"], "in_tv_screen": in_screen})
    if mark_rows:
        db.upsert_companies_batch(mark_rows)
        logger.info("Updated in_tv_screen flag for %d tickers", len(mark_rows))

    # Log new tickers
    if new_tickers:
        for eq in sorted(new_tickers, key=lambda e: e["ticker"]):
            logger.info(
                "  NEW: %s (%s) — %s / %s",
                eq["ticker"],
                eq.get("exchange", ""),
                eq.get("sector", ""),
                eq.get("country", ""),
            )

    logger.info("Nightly screen complete")


if __name__ == "__main__":
    main()
