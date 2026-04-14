#!/usr/bin/env python3
"""
Daily Portfolio Valuation — mark-to-market snapshots for every agent.

Runs after `score_ai_analysis.py` so `companies.price` reflects the freshest
data. For each agent with an `agent_accounts` row, computes cash + holdings
value at the latest `companies.price` and upserts a row into
`agent_portfolio_history`. Feeds the `agent_leaderboard` view.

Scheduled at 05:30 UTC via `.github/workflows/portfolio-valuation.yml`.

Flags:
    --dry-run         Compute snapshots and log them, but don't write.
    --agent HANDLE    Snapshot only one agent (by handle).
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from db import SupabaseDB
from portfolio import PortfolioManager


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"portfolio_valuation_{today_str}.txt"

    logger = logging.getLogger("portfolio_valuation")
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


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute snapshots without writing to the database",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        help="Snapshot only this agent (by handle)",
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== portfolio_valuation started (dry_run=%s) ===", args.dry_run)

    db = SupabaseDB()
    pm = PortfolioManager(db)

    stats = pm.snapshot_all(dry_run=args.dry_run, agent_handle=args.agent)

    logger.info(
        "Snapshot complete: updated=%d skipped=%d errors=%d duration=%.1fs",
        stats["updated"],
        stats["skipped"],
        stats["errors"],
        stats["duration_secs"],
    )
    for item in stats["details"].get("agents", []):
        logger.info("  %s", item)

    if not args.dry_run:
        db.log_run("portfolio_valuation", stats)

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
