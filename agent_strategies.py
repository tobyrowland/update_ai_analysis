"""Strategy registry for agent heartbeats.

Each strategy is a callable that takes a rebalance context and executes
trades via ``PortfolioManager``. New strategies register themselves in the
``STRATEGIES`` dict at the bottom of the module. Agents opt in by setting
``agents.strategy`` to the key.

Strategy contract:

    def rebalance(ctx: RebalanceContext) -> RebalanceResult: ...

A strategy should be **idempotent modulo price drift**: running it twice
back-to-back on an unchanged universe should be a no-op (no trades). This
keeps the heartbeat safe to retry and makes the journal useful.

Current strategies:
    dual_positive  — equal-weight the top N tickers where `bear` and `bull`
                     are both ✅, deduped by company, favouring US listings.
                     This is the rebalance version of build_portfolio.py.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable

from db import SupabaseDB
from portfolio import PortfolioError, PortfolioManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy API
# ---------------------------------------------------------------------------


@dataclass
class RebalanceContext:
    """Inputs handed to every strategy."""

    db: SupabaseDB
    pm: PortfolioManager
    agent: dict
    dry_run: bool = False
    # Optional overrides (agents.config JSONB could feed these later).
    params: dict = field(default_factory=dict)


@dataclass
class RebalanceResult:
    """Structured return value for the orchestrator."""

    buys: int = 0
    sells: int = 0
    notes: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def trades(self) -> int:
        return self.buys + self.sells


Strategy = Callable[[RebalanceContext], RebalanceResult]


# ---------------------------------------------------------------------------
# Shared helpers (mirrors build_portfolio.py — kept inline to avoid coupling
# the strategy to the legacy script's argparse/logging plumbing)
# ---------------------------------------------------------------------------


US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSEARCA", "BATS", "ARCA"}

_CORPORATE_SUFFIXES = re.compile(
    r"\b("
    r"inc|incorporated|corp|corporation|ltd|limited|llc|plc|"
    r"sa|s\.a\.|se|s\.e\.|nv|n\.v\.|ag|a\.g\.|"
    r"co|company|group|holdings|holding|enterprises|"
    r"international|intl|technologies|technology|tech|"
    r"systems|solutions|therapeutics|pharmaceuticals|pharma|"
    r"biosciences|biopharma|medical|healthcare|"
    r"class\s*[a-z]|cl\s*[a-z]|adr"
    r")\b",
    re.IGNORECASE,
)

FUZZY_THRESHOLD = 0.80


def _normalise_company(name: str) -> str:
    s = (name or "").strip().upper()
    s = _CORPORATE_SUFFIXES.sub("", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _names_match(a: str, b: str) -> bool:
    na, nb = _normalise_company(a), _normalise_company(b)
    if na and na == nb:
        return True
    if na and nb:
        shorter, longer = sorted([na, nb], key=len)
        if longer.startswith(shorter) and len(shorter) >= 3:
            return True
    return SequenceMatcher(None, na, nb).ratio() >= FUZZY_THRESHOLD


def _is_us(exchange: Any) -> bool:
    return str(exchange or "").strip().upper() in US_EXCHANGES


def _pick_best(group: list[dict]) -> dict:
    if len(group) == 1:
        return group[0]
    us = [c for c in group if _is_us(c.get("exchange"))]
    pool = us or group
    return max(
        pool,
        key=lambda c: SupabaseDB.safe_float(c.get("composite_score")) or 0.0,
    )


def _dedupe_by_company(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    used = [False] * len(rows)
    for i, row in enumerate(rows):
        if used[i]:
            continue
        group = [row]
        used[i] = True
        for j in range(i + 1, len(rows)):
            if used[j]:
                continue
            if _names_match(row.get("company_name", ""), rows[j].get("company_name", "")):
                group.append(rows[j])
                used[j] = True
        out.append(_pick_best(group))
    return out


# ---------------------------------------------------------------------------
# Strategy: dual_positive
# ---------------------------------------------------------------------------


DUAL_POSITIVE_DEFAULTS = {
    "max_positions": 20,       # top-N deduped dual-positive tickers
    "cash_reserve_pct": 0.02,  # keep 2% cash to absorb rounding / price drift
    "min_trade_usd": 500.0,    # ignore rebalance deltas below this notional
}


def _target_tickers(db: SupabaseDB, params: dict) -> list[dict]:
    """Return the top-N dual-positive companies, deduped, score-sorted."""
    all_companies = db.get_all_companies()
    dual_positive = [
        c for c in all_companies
        if "✅" in str(c.get("bear_eval") or "")
        and "✅" in str(c.get("bull_eval") or "")
        and c.get("ticker")
    ]
    dual_positive = _dedupe_by_company(dual_positive)
    dual_positive.sort(
        key=lambda c: SupabaseDB.safe_float(c.get("composite_score")) or 0.0,
        reverse=True,
    )
    n = int(params.get("max_positions", DUAL_POSITIVE_DEFAULTS["max_positions"]))
    return dual_positive[:n]


def rebalance_dual_positive(ctx: RebalanceContext) -> RebalanceResult:
    """Equal-weight the top-N dual-positive tickers.

    Algorithm:
        1. Fetch target set (dual ✅, deduped, score-sorted, top-N).
        2. Price every target; drop tickers with no usable price.
        3. Compute total portfolio value (cash + MTM) and per-target
           allocation = total * (1 - cash_reserve) / len(targets).
        4. For each current holding not in the target set → sell in full.
        5. For each target, compute desired qty = alloc / price. Sell excess
           first (to free cash), then buy shortfalls. Trades smaller than
           ``min_trade_usd`` are skipped as noise.
    """
    result = RebalanceResult()
    params = {**DUAL_POSITIVE_DEFAULTS, **(ctx.params or {})}
    agent_id = ctx.agent["id"]
    handle = ctx.agent.get("handle", agent_id[:8])

    targets = _target_tickers(ctx.db, params)
    if not targets:
        result.notes["reason"] = "no dual-positive tickers found"
        return result

    # Price every candidate; skip unpriced ones rather than aborting.
    priced: list[tuple[str, float]] = []
    unpriced: list[str] = []
    for c in targets:
        t = c["ticker"]
        try:
            priced.append((t, ctx.pm.get_price(t)))
        except PortfolioError:
            unpriced.append(t)
    if unpriced:
        result.notes["unpriced"] = unpriced
    if not priced:
        result.notes["reason"] = "no priced targets"
        return result

    target_tickers = {t for t, _ in priced}
    price_map = dict(priced)

    # Snapshot: compute current total value.
    portfolio = ctx.pm.get_portfolio(agent_id)
    total_value = portfolio["total_value_usd"]
    if total_value <= 0:
        result.errors.append(f"total_value_usd <= 0 for agent {handle}")
        return result

    cash_reserve = float(params["cash_reserve_pct"])
    investable = total_value * (1.0 - cash_reserve)
    per_target_usd = investable / len(priced)
    min_trade = float(params["min_trade_usd"])

    # Desired quantity per target. Floor to integer shares (no fractional
    # lots for v1). Tickers priced above per_target_usd get qty=0 and will
    # simply not be bought — the reserve absorbs the leftover.
    desired_qty: dict[str, int] = {
        t: int(math.floor(per_target_usd / p)) for t, p in priced
    }

    current_qty: dict[str, float] = {
        h["ticker"]: float(h["quantity"]) for h in portfolio["holdings"]
    }

    # --- Phase 1: sell positions no longer in target set, and trim overshoots.
    # Sells before buys so cash is available for rotations.
    sells: list[tuple[str, float]] = []
    buys: list[tuple[str, int]] = []

    for ticker, held in current_qty.items():
        want = desired_qty.get(ticker, 0) if ticker in target_tickers else 0
        delta = held - want  # positive → need to sell
        if delta <= 0:
            continue
        # Price may be missing for a stale holding; PortfolioManager will raise.
        # Use the company's current price if we have it, else let sell() try.
        px = price_map.get(ticker)
        if px is None:
            try:
                px = ctx.pm.get_price(ticker)
            except PortfolioError:
                result.notes.setdefault("unpriced_holdings", []).append(ticker)
                continue
        if delta * px < min_trade and ticker in target_tickers:
            # Noise trim within an existing position — skip.
            continue
        sells.append((ticker, delta))

    for ticker, price in priced:
        held = current_qty.get(ticker, 0.0)
        want = desired_qty[ticker]
        delta = want - held
        if delta <= 0:
            continue
        if delta * price < min_trade:
            continue
        buys.append((ticker, int(delta)))

    # --- Execute
    if ctx.dry_run:
        result.notes["dry_run_plan"] = {
            "sells": [{"ticker": t, "qty": q} for t, q in sells],
            "buys": [{"ticker": t, "qty": q} for t, q in buys],
            "targets": len(priced),
            "per_target_usd": round(per_target_usd, 2),
            "total_value_usd": total_value,
        }
        logger.info(
            "[dry-run] %s: %d sells, %d buys, target=%d, total=$%.2f",
            handle, len(sells), len(buys), len(priced), total_value,
        )
        return result

    for ticker, qty in sells:
        try:
            ctx.pm.sell(agent_id, ticker, qty, note="heartbeat/dual_positive")
            result.sells += 1
        except PortfolioError as exc:
            result.errors.append(f"sell {ticker} x{qty}: {exc}")

    for ticker, qty in buys:
        try:
            ctx.pm.buy(agent_id, ticker, qty, note="heartbeat/dual_positive")
            result.buys += 1
        except PortfolioError as exc:
            # Insufficient cash is the most likely failure mode (prices drift
            # between the plan and execution). Log and continue — partial
            # rebalance is better than aborting.
            result.errors.append(f"buy {ticker} x{qty}: {exc}")

    result.notes["targets"] = len(priced)
    result.notes["per_target_usd"] = round(per_target_usd, 2)
    result.notes["total_value_usd"] = total_value
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


STRATEGIES: dict[str, Strategy] = {
    "dual_positive": rebalance_dual_positive,
}


def get_strategy(name: str) -> Strategy | None:
    return STRATEGIES.get(name)
