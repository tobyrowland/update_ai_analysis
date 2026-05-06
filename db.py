"""
Shared Supabase database access layer.

Replaces the duplicated Google Sheets read/write code across all pipeline
scripts with a single module backed by Supabase (PostgreSQL).

Environment variables:
    SUPABASE_URL         — Supabase project URL
    SUPABASE_SERVICE_KEY — Service-role key (bypasses RLS)
"""

import json
import math
import os
import re
from datetime import date, timedelta

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

NULL_VALUE = "\u2014"  # em-dash — consistent placeholder for missing data


class SupabaseDB:
    """Shared database access layer for all pipeline scripts."""

    def __init__(self):
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY env vars must be set"
            )
        # Log connection info for debugging (mask most of the key)
        import logging
        _log = logging.getLogger("db")
        _log.info("Connecting to Supabase: url=%s key=%s...%s",
                   url, key[:8], key[-4:])
        self.client: Client = create_client(url, key)

    # ------------------------------------------------------------------
    # Companies (AI Analysis)
    # ------------------------------------------------------------------

    def get_all_companies(self, columns: str = "*") -> list[dict]:
        """Return all rows from the companies table."""
        resp = self.client.table("companies").select(columns).execute()
        return resp.data

    def get_company(self, ticker: str) -> dict | None:
        """Return a single company row, or None."""
        resp = (
            self.client.table("companies")
            .select("*")
            .eq("ticker", ticker)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def get_all_tickers(self) -> set[str]:
        """Return the set of all ticker symbols in the companies table."""
        resp = self.client.table("companies").select("ticker").execute()
        return {row["ticker"] for row in resp.data}

    def get_stale_companies(self, date_column: str, days: int) -> list[dict]:
        """Return companies where `date_column` is older than `days` or NULL."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        # Supabase doesn't support OR with is_.null in a single filter chain,
        # so we fetch all and filter in Python for reliability.
        resp = self.client.table("companies").select("*").execute()
        results = []
        for row in resp.data:
            val = row.get(date_column)
            if val is None or val == "" or val < cutoff:
                results.append(row)
        return results

    def upsert_company(self, ticker: str, data: dict) -> None:
        """Insert or update a single company row."""
        data["ticker"] = ticker
        self._sanitize(data)
        self.client.table("companies").upsert(data).execute()

    def upsert_companies_batch(self, rows: list[dict]) -> None:
        """Insert or update multiple company rows."""
        for row in rows:
            self._sanitize(row)
        if rows:
            self.client.table("companies").upsert(rows).execute()

    # ------------------------------------------------------------------
    # Price-Sales
    # ------------------------------------------------------------------

    def get_all_price_sales(self) -> dict[str, dict]:
        """Return all price-sales rows keyed by ticker."""
        resp = self.client.table("price_sales").select("*").execute()
        return {row["ticker"]: row for row in resp.data}

    def get_price_sales(self, ticker: str) -> dict | None:
        """Return a single price-sales row, or None."""
        resp = (
            self.client.table("price_sales")
            .select("*")
            .eq("ticker", ticker)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def upsert_price_sales(self, ticker: str, data: dict) -> None:
        """Insert or update a price-sales row."""
        data["ticker"] = ticker
        self._sanitize(data)
        self.client.table("price_sales").upsert(data).execute()

    def upsert_price_sales_batch(self, rows: list[dict]) -> None:
        """Insert or update multiple price-sales rows."""
        for row in rows:
            self._sanitize(row)
        if rows:
            self.client.table("price_sales").upsert(rows).execute()

    # ------------------------------------------------------------------
    # Agents / Portfolio Manager
    # ------------------------------------------------------------------

    def get_agent_by_handle(self, handle: str) -> dict | None:
        """Return a single agents row by handle, or None."""
        resp = (
            self.client.table("agents")
            .select("*")
            .eq("handle", handle)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def get_agent_account(self, agent_id: str) -> dict | None:
        """Return a single agent_accounts row, or None."""
        resp = (
            self.client.table("agent_accounts")
            .select("*")
            .eq("agent_id", agent_id)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def get_all_agent_accounts(self) -> list[dict]:
        """Return all agent_accounts rows."""
        resp = self.client.table("agent_accounts").select("*").execute()
        return resp.data

    def upsert_agent_account(self, agent_id: str, data: dict) -> None:
        """Insert or update an agent_accounts row."""
        data["agent_id"] = agent_id
        self._sanitize(data)
        self.client.table("agent_accounts").upsert(data).execute()

    def get_agent_holdings(self, agent_id: str) -> list[dict]:
        """Return all holdings rows for an agent."""
        resp = (
            self.client.table("agent_holdings")
            .select("*")
            .eq("agent_id", agent_id)
            .execute()
        )
        return resp.data

    def get_agent_holding(self, agent_id: str, ticker: str) -> dict | None:
        """Return a single holdings row, or None."""
        resp = (
            self.client.table("agent_holdings")
            .select("*")
            .eq("agent_id", agent_id)
            .eq("ticker", ticker)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def upsert_agent_holding(self, data: dict) -> None:
        """Insert or update an agent_holdings row. Caller must set agent_id+ticker."""
        self._sanitize(data)
        self.client.table("agent_holdings").upsert(data).execute()

    def delete_agent_holding(self, agent_id: str, ticker: str) -> None:
        """Remove an agent_holdings row (used when quantity reaches 0)."""
        (
            self.client.table("agent_holdings")
            .delete()
            .eq("agent_id", agent_id)
            .eq("ticker", ticker)
            .execute()
        )

    def insert_agent_trade(self, data: dict) -> None:
        """Append a row to the immutable trade journal."""
        self._sanitize(data)
        self.client.table("agent_trades").insert(data).execute()

    def upsert_portfolio_snapshot(self, data: dict) -> None:
        """Insert or update a daily agent_portfolio_history row."""
        self._sanitize(data)
        self.client.table("agent_portfolio_history").upsert(data).execute()

    # ------------------------------------------------------------------
    # Swarm Consensus
    # ------------------------------------------------------------------

    def fetch_holdings_with_agent_company(self) -> list[dict]:
        """Return every agent_holdings row joined to its agent + company.

        One round-trip; the consensus aggregation runs in Python afterwards.
        Output rows are flattened: {agent_id, ticker, quantity, avg_cost_usd,
        handle, display_name, is_house_agent, company_name, current_price}.
        """
        resp = (
            self.client.table("agent_holdings")
            .select(
                "agent_id, ticker, quantity, avg_cost_usd, "
                "agents(handle, display_name, is_house_agent), "
                "companies(company_name, price)"
            )
            .execute()
        )
        out: list[dict] = []
        for r in resp.data or []:
            agent = r.get("agents") or {}
            company = r.get("companies") or {}
            out.append({
                "agent_id": r.get("agent_id"),
                "ticker": r.get("ticker"),
                "quantity": r.get("quantity"),
                "avg_cost_usd": r.get("avg_cost_usd"),
                "handle": agent.get("handle"),
                "display_name": agent.get("display_name"),
                "is_house_agent": agent.get("is_house_agent"),
                "company_name": company.get("company_name"),
                "current_price": company.get("price"),
            })
        return out

    def get_latest_consensus_top_tickers(
        self, limit: int = 5
    ) -> tuple[list[dict], str | None]:
        """Return the highest-conviction tickers from the latest snapshot.

        Joins ``companies`` for ``company_name`` so callers can disambiguate
        the ticker when classifying social posts. Used by the Bluesky
        heartbeat's equity-targeting phase.
        """
        latest = (
            self.client.table("consensus_snapshots")
            .select("snapshot_date")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        if not latest.data:
            return [], None
        snapshot_date = latest.data[0]["snapshot_date"]

        resp = (
            self.client.table("consensus_snapshots")
            .select(
                "rank, ticker, num_agents, total_agents, pct_agents, "
                "swarm_pnl_pct, companies(company_name)"
            )
            .eq("snapshot_date", snapshot_date)
            .order("rank", desc=False)
            .limit(limit)
            .execute()
        )
        rows: list[dict] = []
        for r in resp.data or []:
            company = r.get("companies") or {}
            rows.append({
                "rank": r.get("rank"),
                "ticker": r.get("ticker"),
                "company_name": company.get("company_name") or r.get("ticker"),
                "num_agents": r.get("num_agents"),
                "total_agents": r.get("total_agents"),
                "pct_agents": r.get("pct_agents"),
                "swarm_pnl_pct": r.get("swarm_pnl_pct"),
            })
        return rows, snapshot_date

    def replace_consensus_snapshot(
        self, snapshot_date: str, rows: list[dict]
    ) -> None:
        """Replace the consensus snapshot for a given date.

        Deletes any existing rows for snapshot_date, then inserts the new set
        in a single batch. Idempotent — safe to re-run on the same date.
        """
        (
            self.client.table("consensus_snapshots")
            .delete()
            .eq("snapshot_date", snapshot_date)
            .execute()
        )
        if not rows:
            return
        for row in rows:
            self._sanitize(row)
        self.client.table("consensus_snapshots").insert(rows).execute()

    # ------------------------------------------------------------------
    # Agent Heartbeats
    # ------------------------------------------------------------------

    def get_all_agents(self) -> list[dict]:
        """Return every row in the agents table."""
        resp = self.client.table("agents").select("*").execute()
        return resp.data

    def update_agent_last_heartbeat(self, agent_id: str, when_iso: str) -> None:
        """Mark an agent's last_heartbeat_at timestamp."""
        (
            self.client.table("agents")
            .update({"last_heartbeat_at": when_iso})
            .eq("id", agent_id)
            .execute()
        )

    def insert_agent_heartbeat(self, data: dict) -> None:
        """Append a row to the agent_heartbeats journal."""
        self._sanitize(data)
        self.client.table("agent_heartbeats").insert(data).execute()

    # ------------------------------------------------------------------
    # Run Logs
    # ------------------------------------------------------------------

    def log_run(self, script_name: str, stats: dict) -> None:
        """Insert a run-log entry."""
        row = {
            "script_name": script_name,
            "run_date": date.today().isoformat(),
            **stats,
        }
        self._sanitize(row)
        self.client.table("run_logs").insert(row).execute()

    # ------------------------------------------------------------------
    # Sanitization
    # ------------------------------------------------------------------

    def _sanitize(self, data: dict) -> None:
        """Clean values before sending to Supabase (NaN, Inf, em-dash → None)."""
        for k, v in list(data.items()):
            if v is None:
                continue
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                data[k] = None
            elif v == NULL_VALUE:
                data[k] = None

    # ------------------------------------------------------------------
    # Shared utilities (consolidated from all scripts)
    # ------------------------------------------------------------------

    @staticmethod
    def safe_float(val) -> float | None:
        """Try to convert a value to float, return None on failure.

        Handles em-dash null markers, percentage signs, and NaN/Inf.
        """
        if val is None or val == "" or val == NULL_VALUE:
            return None
        try:
            cleaned = str(val).strip().rstrip("%")
            f = float(cleaned)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except (ValueError, TypeError):
            return None

    @staticmethod
    def extract_ticker(val: str) -> str:
        """Extract ticker from a HYPERLINK formula or plain text.

        Handles both =HYPERLINK("url", "TICKER") formulas and plain strings.
        """
        val = str(val).strip()
        if not val:
            return ""
        match = re.search(r'=HYPERLINK\([^,]+,\s*"([^"]+)"\)', val)
        if match:
            return match.group(1).strip().upper()
        return val.strip().upper()
