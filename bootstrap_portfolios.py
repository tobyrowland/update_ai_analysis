#!/usr/bin/env python3
"""
Bootstrap agent portfolio accounts.

Idempotently opens an `agent_accounts` row (with $1M starting cash) for every
row in the `agents` table that doesn't already have one. Safe to rerun as
more agents register.

Usage:
    python bootstrap_portfolios.py                  # all agents
    python bootstrap_portfolios.py --handle smash-hit-scout
    python bootstrap_portfolios.py --starting-cash 500000
"""

import argparse
import logging
import sys

from dotenv import load_dotenv

from db import SupabaseDB
from portfolio import DEFAULT_STARTING_CASH, PortfolioManager


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--handle",
        type=str,
        default=None,
        help="Bootstrap only this agent (by handle)",
    )
    parser.add_argument(
        "--starting-cash",
        type=float,
        default=DEFAULT_STARTING_CASH,
        help=f"Starting cash per agent (default ${DEFAULT_STARTING_CASH:,.0f})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("bootstrap_portfolios")

    db = SupabaseDB()
    pm = PortfolioManager(db)

    if args.handle:
        agent = db.get_agent_by_handle(args.handle)
        if not agent:
            logger.error("No agent found with handle '%s'", args.handle)
            return 1
        agents = [agent]
    else:
        resp = db.client.table("agents").select("*").execute()
        agents = resp.data

    if not agents:
        logger.warning("No agents in the database — nothing to bootstrap")
        return 0

    opened = 0
    existed = 0
    for agent in agents:
        before = db.get_agent_account(agent["id"])
        pm.open_account(agent["id"], starting_cash=args.starting_cash)
        if before:
            existed += 1
            logger.info(
                "  %-24s  (already had account, cash=$%.2f)",
                agent["handle"],
                float(before["cash_usd"]),
            )
        else:
            opened += 1
            logger.info(
                "  %-24s  ✓ opened with $%.2f",
                agent["handle"],
                args.starting_cash,
            )

    logger.info(
        "Bootstrap complete: %d opened, %d already existed, %d total",
        opened,
        existed,
        len(agents),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
