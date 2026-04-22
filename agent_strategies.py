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
# Strategy: momentum
# ---------------------------------------------------------------------------


MOMENTUM_DEFAULTS = {
    "max_positions": 10,
    "max_sells_per_heartbeat": 2,
    "max_buys_per_heartbeat": 2,
    "cash_reserve_pct": 0.02,
    "min_trade_usd": 500.0,
    # Eligible-universe band for perf_52w_vs_spy.
    "momentum_floor": -0.15,
    "momentum_ceiling": 0.40,
    # Extra gate on new entries: must be at least this strong vs SPY.
    "momentum_min_for_entry": 0.0,
    # Exit guard: rating strictly greater than this triggers a sell.
    "rating_ceiling": 1.6,
}


def _eligible_momentum(db: SupabaseDB, params: dict) -> list[dict]:
    """Return the momentum-ranked eligible set.

    A ticker is eligible if all hold:
        - bear_eval contains ✅ and bull_eval contains ✅
        - rating is not null and <= rating_ceiling
        - exchange ∈ US_EXCHANGES (v1 prices are USD-flat; skip foreign dups)
        - perf_52w_vs_spy is not null and ∈ [momentum_floor, momentum_ceiling]

    Deduped by company (US-listing preferred anyway), sorted by
    perf_52w_vs_spy descending.
    """
    floor = float(params["momentum_floor"])
    ceiling = float(params["momentum_ceiling"])
    rating_ceiling = float(params["rating_ceiling"])

    eligible: list[dict] = []
    for c in db.get_all_companies():
        if not c.get("ticker"):
            continue
        if "✅" not in str(c.get("bear_eval") or ""):
            continue
        if "✅" not in str(c.get("bull_eval") or ""):
            continue
        if not _is_us(c.get("exchange")):
            continue
        rating = SupabaseDB.safe_float(c.get("rating"))
        if rating is None or rating > rating_ceiling:
            continue
        perf = SupabaseDB.safe_float(c.get("perf_52w_vs_spy"))
        if perf is None or perf < floor or perf > ceiling:
            continue
        eligible.append(c)

    eligible = _dedupe_by_company(eligible)
    eligible.sort(
        key=lambda c: SupabaseDB.safe_float(c.get("perf_52w_vs_spy")) or 0.0,
        reverse=True,
    )
    return eligible


def rebalance_momentum(ctx: RebalanceContext) -> RebalanceResult:
    """Low-churn momentum rebalance.

    Phase 1 — sell only the becalmed. A current holding is sold in full iff
    any of:
        (a) it's no longer in the eligible universe AND its
            perf_52w_vs_spy is < momentum_floor (becalmed drift),
        (b) its rating has risen above rating_ceiling,
        (c) its bear_eval has flipped to ❌.
    Missing perf data (NULL) does NOT trigger a sell — the holding is
    preserved until the scoring pipeline repopulates it.

    Sells are capped at max_sells_per_heartbeat; if more qualify, the two
    with the lowest perf_52w_vs_spy go first and the rest land in
    notes["deferred_sells"] for visibility.

    Phase 2 — redeploy freed cash into top momentum names. After sells,
    available slots = max_positions - remaining_holdings (clamped at
    max_buys_per_heartbeat). Entries must additionally have
    perf_52w_vs_spy >= momentum_min_for_entry. Freed cash is
    equal-weighted across the new buys with a cash_reserve_pct buffer.

    Idempotence: on an unchanged universe, a second run produces 0/0.
    """
    result = RebalanceResult()
    params = {**MOMENTUM_DEFAULTS, **(ctx.params or {})}
    agent_id = ctx.agent["id"]
    handle = ctx.agent.get("handle", agent_id[:8])

    eligible = _eligible_momentum(ctx.db, params)
    eligible_by_ticker = {c["ticker"]: c for c in eligible}

    portfolio = ctx.pm.get_portfolio(agent_id)
    holdings = portfolio["holdings"]
    total_value = portfolio["total_value_usd"]
    if total_value <= 0:
        result.errors.append(f"total_value_usd <= 0 for agent {handle}")
        return result

    rating_ceiling = float(params["rating_ceiling"])
    momentum_floor = float(params["momentum_floor"])

    # --- Phase 1: classify current holdings.
    momentum_snapshot: list[dict] = []
    sell_candidates: list[tuple[str, float, str]] = []  # (ticker, perf, reason)

    for h in holdings:
        ticker = h["ticker"]
        company = ctx.db.get_company(ticker)
        perf = (
            SupabaseDB.safe_float(company.get("perf_52w_vs_spy"))
            if company else None
        )
        rating = (
            SupabaseDB.safe_float(company.get("rating"))
            if company else None
        )
        bear_flipped = (
            company is not None
            and "❌" in str(company.get("bear_eval") or "")
        )
        momentum_snapshot.append(
            {"ticker": ticker, "perf_52w_vs_spy": perf, "rating": rating}
        )

        reasons: list[str] = []
        if rating is not None and rating > rating_ceiling:
            reasons.append(f"rating>{rating_ceiling}")
        if bear_flipped:
            reasons.append("bear_flipped_red")
        if (
            ticker not in eligible_by_ticker
            and perf is not None
            and perf < momentum_floor
        ):
            reasons.append(f"becalmed_below_{momentum_floor}")

        if reasons:
            # Missing perf sorts last; we never want to sell a NULL-perf
            # position unless rating/bear forced it, and even then we want
            # forced sells to rank above purely-becalmed ones.
            sell_candidates.append(
                (ticker, perf if perf is not None else float("inf"), ";".join(reasons))
            )

    # Cap at max_sells_per_heartbeat, prioritising the lowest perf.
    max_sells = int(params["max_sells_per_heartbeat"])
    sell_candidates.sort(key=lambda s: s[1])
    planned_sells = sell_candidates[:max_sells]
    deferred_sells = [
        {"ticker": t, "perf_52w_vs_spy": p if p != float("inf") else None, "reason": r}
        for (t, p, r) in sell_candidates[max_sells:]
    ]

    # --- Phase 2: buys. Compute available slots BEFORE executing sells
    # (we know exactly what will be sold).
    held_tickers = {h["ticker"] for h in holdings}
    tickers_after_sells = held_tickers - {t for (t, _, _) in planned_sells}
    max_positions = int(params["max_positions"])
    free_slots = max(0, max_positions - len(tickers_after_sells))
    max_buys = min(int(params["max_buys_per_heartbeat"]), free_slots)

    momentum_min_for_entry = float(params["momentum_min_for_entry"])
    min_trade = float(params["min_trade_usd"])
    cash_reserve = float(params["cash_reserve_pct"])

    # Candidate buys: top of eligible, not already held, above entry floor.
    # Price them; skip unpriced.
    buy_candidates_priced: list[tuple[str, float]] = []
    unpriced: list[str] = []
    for c in eligible:
        if len(buy_candidates_priced) >= max_buys:
            break
        t = c["ticker"]
        if t in tickers_after_sells:
            continue
        perf = SupabaseDB.safe_float(c.get("perf_52w_vs_spy"))
        if perf is None or perf < momentum_min_for_entry:
            continue
        try:
            buy_candidates_priced.append((t, ctx.pm.get_price(t)))
        except PortfolioError:
            unpriced.append(t)
    if unpriced:
        result.notes["unpriced"] = unpriced

    # Estimate free cash post-sell. We don't have exact fill prices yet, so
    # use current market price for the planned sells.
    cash = float(portfolio["cash_usd"])
    sell_proceeds_estimate = 0.0
    for sell_ticker, _perf, _reason in planned_sells:
        held_qty = next(
            (float(h["quantity"]) for h in holdings if h["ticker"] == sell_ticker),
            0.0,
        )
        try:
            px = ctx.pm.get_price(sell_ticker)
            sell_proceeds_estimate += held_qty * px
        except PortfolioError:
            # Unpriceable holding — sell will fail too. Leave it out of
            # the cash estimate and let the live branch record the error.
            pass
    free_cash = cash + sell_proceeds_estimate
    investable = free_cash * (1.0 - cash_reserve)

    per_target_usd = (
        investable / len(buy_candidates_priced)
        if buy_candidates_priced else 0.0
    )

    buys: list[tuple[str, int]] = []
    for ticker, price in buy_candidates_priced:
        qty = int(math.floor(per_target_usd / price)) if price > 0 else 0
        if qty <= 0:
            continue
        if qty * price < min_trade:
            continue
        buys.append((ticker, qty))

    # --- Dry-run: serialise the plan and return.
    if ctx.dry_run:
        result.notes["dry_run_plan"] = {
            "sells": [
                {"ticker": t, "perf_52w_vs_spy": (p if p != float("inf") else None), "reason": r}
                for (t, p, r) in planned_sells
            ],
            "buys": [{"ticker": t, "qty": q} for (t, q) in buys],
            "per_target_usd": round(per_target_usd, 2),
            "total_value_usd": total_value,
            "free_cash_post_sell_estimate": round(free_cash, 2),
            "max_positions": max_positions,
            "holdings_after_sells": len(tickers_after_sells),
            "free_slots": free_slots,
        }
        if deferred_sells:
            result.notes["deferred_sells"] = deferred_sells
        result.notes["momentum_snapshot"] = momentum_snapshot
        result.notes["eligible_snapshot"] = [
            {
                "ticker": c["ticker"],
                "perf_52w_vs_spy": SupabaseDB.safe_float(c.get("perf_52w_vs_spy")),
                "rating": SupabaseDB.safe_float(c.get("rating")),
            }
            for c in eligible[:max_positions]
        ]
        logger.info(
            "[dry-run] %s: %d sells, %d buys, eligible=%d, total=$%.2f",
            handle, len(planned_sells), len(buys), len(eligible), total_value,
        )
        return result

    # --- Live: execute sells first, then buys.
    for ticker, _perf, reason in planned_sells:
        held_qty = next(
            (float(h["quantity"]) for h in holdings if h["ticker"] == ticker),
            0.0,
        )
        if held_qty <= 0:
            continue
        try:
            ctx.pm.sell(agent_id, ticker, held_qty, note=f"heartbeat/momentum:{reason}")
            result.sells += 1
        except PortfolioError as exc:
            result.errors.append(f"sell {ticker} x{held_qty}: {exc}")

    for ticker, qty in buys:
        try:
            ctx.pm.buy(agent_id, ticker, qty, note="heartbeat/momentum")
            result.buys += 1
        except PortfolioError as exc:
            # Most likely: price drift between plan and fill left us short
            # of cash for the last buy. Partial fill is acceptable.
            result.errors.append(f"buy {ticker} x{qty}: {exc}")

    result.notes["eligible_count"] = len(eligible)
    result.notes["per_target_usd"] = round(per_target_usd, 2)
    result.notes["total_value_usd"] = total_value
    if deferred_sells:
        result.notes["deferred_sells"] = deferred_sells
    return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


STRATEGIES: dict[str, Strategy] = {
    "dual_positive": rebalance_dual_positive,
    "momentum": rebalance_momentum,
}


def get_strategy(name: str) -> Strategy | None:
    return STRATEGIES.get(name)
