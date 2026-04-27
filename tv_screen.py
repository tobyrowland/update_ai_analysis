#!/usr/bin/env python3
"""
Shared TradingView screening logic.

Extracted from nightly_current_update.py so both nightly_screen.py and
score_ai_analysis.py can reuse the same screening pass without duplication.
"""

import math
import re

from tradingview_screener import Query, col

# TradingView screener fields
TV_SELECT_FIELDS = [
    "name", "exchange", "description", "country", "sector",
    "close", "market_cap_basic", "price_revenue_ttm",
    "total_revenue_ttm", "total_revenue_yoy_growth_ttm",
    "gross_profit_margin_fy", "after_tax_margin",
    "free_cash_flow_margin_ttm", "Perf.Y",
    "recommendation_mark",
    "net_income_ttm",
]

# Countries to exclude
EXCLUDED_COUNTRIES = {"China", "Hong Kong", "Taiwan"}

# Sectors to exclude
EXCLUDED_SECTORS = {
    "Real Estate", "REIT", "Real Estate Investment Trusts",
    "Non-Energy Minerals", "Finance", "Utilities",
}

# Market passes
PASS1_MARKETS = ["america", "canada", "brazil", "mexico"]
PASS2_MARKETS = [
    "uk", "germany", "france", "spain", "italy", "netherlands",
    "switzerland", "sweden", "norway", "denmark", "finland", "belgium",
    "austria", "portugal", "ireland", "israel", "south_africa",
    "saudi_arabia", "uae", "poland", "greece", "turkey",
]
PASS3_MARKETS = [
    "australia", "india", "japan", "south_korea", "singapore",
    "new_zealand", "indonesia", "thailand", "malaysia", "philippines",
    "vietnam",
]


def clean_ticker(raw_name: str) -> str:
    """Clean ticker from TradingView name field."""
    return re.sub(r"^\d(?=\D)", "", raw_name)


def safe_divide_100(val):
    """If value looks like a whole-number percentage (>1 or <-1), divide by 100."""
    if val is None:
        return None
    try:
        v = float(val)
    except (ValueError, TypeError):
        return None
    if abs(v) > 1.0:
        return v / 100.0
    return v


def _get_spy_perf_y(logger) -> float:
    """Fetch SPY's Perf.Y from TradingView."""
    try:
        _, df = (
            Query()
            .set_tickers("AMEX:SPY")
            .select("Perf.Y")
            .get_scanner_data()
        )
        if len(df) > 0:
            spy_val = df.iloc[0]["Perf.Y"]
            logger.info("SPY Perf.Y = %.4f", spy_val)
            return float(spy_val)
    except Exception as e:
        logger.warning("Failed to fetch SPY Perf.Y: %s — defaulting to 0", e)
    return 0.0


def _screen_markets(markets: list[str], spy_perf_y: float, logger) -> list[dict]:
    """Run TradingView screener for a set of markets and return equity dicts."""
    try:
        total_count, df = (
            Query()
            .set_markets(*markets)
            .select(*TV_SELECT_FIELDS)
            .where(
                col("market_cap_basic").between(2_000_000_000, 500_000_000_000),
                col("gross_profit_margin_fy") > 45,
                col("total_revenue_yoy_growth_ttm").between(15, 500),
                col("total_revenue_ttm") > 200_000_000,
                col("price_revenue_ttm") < 15,
                col("recommendation_mark") <= 1.8,
                col("sector").not_in(["Finance", "Utilities", "Non-Energy Minerals"]),
            )
            .limit(5000)
            .get_scanner_data()
        )
        logger.info("Screener returned %d results (total available: %d)", len(df), total_count)
    except Exception as e:
        logger.error("TradingView screener error: %s", e)
        return []

    unique_sectors = df["sector"].dropna().unique() if "sector" in df.columns else []
    logger.info("Unique sectors in results (%d): %s", len(unique_sectors), sorted(unique_sectors))

    equities = []
    for _, row in df.iterrows():
        raw_name = str(row.get("name", ""))
        ticker = clean_ticker(raw_name)
        if not ticker:
            continue

        country = str(row.get("country", ""))
        sector = str(row.get("sector", ""))

        if country in EXCLUDED_COUNTRIES:
            continue
        if sector in EXCLUDED_SECTORS:
            continue

        exchange = str(row.get("exchange", ""))
        company_name = str(row.get("description", ""))
        price = row.get("close")

        # Relative performance vs SPY
        perf_y = row.get("Perf.Y")
        if perf_y is not None:
            perf_52w_vs_spy = safe_divide_100(perf_y) - safe_divide_100(spy_perf_y)
        else:
            perf_52w_vs_spy = None

        # Rating: recommendation_mark score (1-5 scale)
        rec_mark = row.get("recommendation_mark")
        try:
            mark_f = float(rec_mark) if rec_mark is not None else None
            if mark_f is not None and math.isnan(mark_f):
                mark_f = None
        except (ValueError, TypeError):
            mark_f = None
        rating = f"{mark_f:.1f}" if mark_f is not None else ""

        equities.append({
            "ticker": ticker,
            "exchange": exchange,
            "company_name": company_name,
            "country": country,
            "sector": sector,
            "price": price,
            "perf_52w_vs_spy": perf_52w_vs_spy,
            "rating": rating,
        })

    return equities


def run_tradingview_screen(logger) -> list[dict]:
    """
    Query TradingView screener with filters and return list of equity dicts.
    Runs 3 passes with deduplication across passes.
    """
    logger.info("=" * 60)
    logger.info("TradingView Screening")
    logger.info("=" * 60)

    spy_perf_y = _get_spy_perf_y(logger)

    all_results = {}

    for pass_num, markets in enumerate(
        [PASS1_MARKETS, PASS2_MARKETS, PASS3_MARKETS], start=1
    ):
        logger.info("Pass %d: scanning %d markets...", pass_num, len(markets))
        equities = _screen_markets(markets, spy_perf_y, logger)
        new_count = 0
        for eq in equities:
            ticker = eq["ticker"]
            if ticker not in all_results:
                all_results[ticker] = eq
                new_count += 1
        logger.info(
            "Pass %d: found %d equities, %d new (total so far: %d)",
            pass_num, len(equities), new_count, len(all_results),
        )

    logger.info("TradingView screening complete: %d unique equities", len(all_results))
    return list(all_results.values())


def fetch_market_data(tickers_with_exchange: list[tuple[str, str]], logger) -> dict[str, dict]:
    """
    Fetch price, rating, and 52-week performance for specific tickers from
    TradingView (no screening filters). Returns {TICKER: {price, rating,
    perf_52w_vs_spy}}.

    tickers_with_exchange: list of (ticker, exchange) tuples.
    """
    if not tickers_with_exchange:
        return {}

    spy_perf_y = _get_spy_perf_y(logger)

    # Build EXCHANGE:TICKER identifiers for TradingView
    tv_ids = []
    ticker_map = {}  # tv_id → clean ticker
    for ticker, exchange in tickers_with_exchange:
        tv_id = f"{exchange}:{ticker}" if exchange else ticker
        tv_ids.append(tv_id)
        ticker_map[tv_id] = ticker

    # TradingView limits queries — batch in chunks of 500
    BATCH_SIZE = 500
    results = {}

    for i in range(0, len(tv_ids), BATCH_SIZE):
        batch = tv_ids[i:i + BATCH_SIZE]
        try:
            _, df = (
                Query()
                .set_tickers(*batch)
                .select("name", "close", "Perf.Y", "recommendation_mark")
                .limit(len(batch))
                .get_scanner_data()
            )
            logger.info("TradingView market data batch %d: got %d/%d",
                        i // BATCH_SIZE + 1, len(df), len(batch))
        except Exception as e:
            logger.warning("TradingView market data batch failed: %s", e)
            continue

        for _, row in df.iterrows():
            raw_name = str(row.get("name", ""))
            ticker = clean_ticker(raw_name)
            if not ticker:
                continue

            price = row.get("close")
            perf_y = row.get("Perf.Y")
            if perf_y is not None:
                perf_52w_vs_spy = safe_divide_100(perf_y) - safe_divide_100(spy_perf_y)
            else:
                perf_52w_vs_spy = None

            rec_mark = row.get("recommendation_mark")
            try:
                mark_f = float(rec_mark) if rec_mark is not None else None
                if mark_f is not None and math.isnan(mark_f):
                    mark_f = None
            except (ValueError, TypeError):
                mark_f = None
            rating = f"{mark_f:.1f}" if mark_f is not None else ""

            results[ticker.upper()] = {
                "price": price,
                "perf_52w_vs_spy": perf_52w_vs_spy,
                "rating": rating,
            }

    logger.info("Fetched market data for %d/%d tickers",
                len(results), len(tickers_with_exchange))
    return results


def _fetch_sector_batch(tv_ids: list[str], logger) -> dict[str, str]:
    """Query TradingView for sector given a list of EXCHANGE:TICKER ids."""
    BATCH_SIZE = 500
    results = {}
    for i in range(0, len(tv_ids), BATCH_SIZE):
        batch = tv_ids[i:i + BATCH_SIZE]
        try:
            _, df = (
                Query()
                .set_tickers(*batch)
                .select("name", "sector")
                .limit(len(batch))
                .get_scanner_data()
            )
            logger.info("TradingView sector batch: got %d/%d",
                        len(df), len(batch))
        except Exception as e:
            logger.warning("TradingView sector batch failed: %s", e)
            continue

        for _, row in df.iterrows():
            raw_name = str(row.get("name", ""))
            ticker = clean_ticker(raw_name)
            if not ticker:
                continue
            sector = str(row.get("sector", ""))
            if sector and sector.lower() not in ("nan", "none", ""):
                results[ticker.upper()] = sector
    return results


def fetch_sector_data(tickers_with_exchange: list[tuple[str, str]], logger) -> dict[str, str]:
    """
    Fetch sector for specific tickers from TradingView (no screening filters).
    Returns {TICKER: sector_string}.

    Tries EXCHANGE:TICKER first, then retries unmatched tickers as just TICKER
    (lets TradingView resolve the exchange automatically).
    """
    if not tickers_with_exchange:
        return {}

    # First pass: query with exchange prefix
    tv_ids = []
    all_tickers = {}  # ticker → exchange
    for ticker, exchange in tickers_with_exchange:
        tv_id = f"{exchange}:{ticker}" if exchange else ticker
        tv_ids.append(tv_id)
        all_tickers[ticker.upper()] = exchange

    logger.info("Fetching sector for %d tickers (pass 1: with exchange)...",
                len(tv_ids))
    results = _fetch_sector_batch(tv_ids, logger)

    # Second pass: retry unmatched tickers on common exchanges
    missed = [t for t in all_tickers if t not in results]
    if missed:
        FALLBACK_EXCHANGES = [
            "NYSE", "NASDAQ", "XETR", "FWB", "LSE", "TSE", "ASX",
            "TSX", "NSE", "EPA", "AMS", "SWX", "BIT", "BME", "STO",
            "KRX", "SGX", "NZX", "JSE",
        ]
        retry_ids = []
        for ticker in missed:
            for ex in FALLBACK_EXCHANGES:
                retry_ids.append(f"{ex}:{ticker}")
        logger.info("Retrying %d unmatched tickers across %d common exchanges...",
                     len(missed), len(FALLBACK_EXCHANGES))
        retry_results = _fetch_sector_batch(retry_ids, logger)
        results.update(retry_results)

    logger.info("Fetched sector for %d/%d tickers",
                len(results), len(tickers_with_exchange))
    return results
