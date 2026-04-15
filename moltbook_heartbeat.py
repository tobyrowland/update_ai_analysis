"""Moltbook heartbeat — poll /home, surface activity needing attention.

Runs on a recurring schedule (see .github/workflows/moltbook-heartbeat.yml).
While the agent is pending_claim this simply logs the claim URL so the owner
can verify. Once claimed, it reports notifications, DMs, and feed items to
stdout/logs so a human (or a later Claude run) can act on them.

Env vars:
    MOLTBOOK_API_KEY   Bearer token from /api/v1/agents/register (required)
    MOLTBOOK_BASE_URL  Override API host (defaults to https://www.moltbook.com)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import requests

BASE_URL = os.environ.get("MOLTBOOK_BASE_URL", "https://www.moltbook.com").rstrip("/")
API_ROOT = f"{BASE_URL}/api/v1"
TIMEOUT = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("moltbook-heartbeat")


def _auth_headers() -> dict[str, str]:
    key = os.environ.get("MOLTBOOK_API_KEY")
    if not key:
        log.error("MOLTBOOK_API_KEY is not set")
        sys.exit(2)
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _get(path: str) -> dict[str, Any] | None:
    url = f"{API_ROOT}{path}"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=TIMEOUT)
    except requests.RequestException as exc:
        log.error("GET %s failed: %s", path, exc)
        return None
    if resp.status_code >= 400:
        log.error("GET %s -> %s: %s", path, resp.status_code, resp.text[:300])
        return None
    try:
        return resp.json()
    except ValueError:
        log.error("GET %s returned non-JSON: %s", path, resp.text[:300])
        return None


def _summarize_home(home: dict[str, Any]) -> None:
    """Log a compact summary of /home so the owner can scan runs quickly."""
    status = home.get("status") or home.get("agent", {}).get("status")
    log.info("status=%s", status)

    notifications = home.get("notifications") or []
    if notifications:
        log.info("notifications=%d", len(notifications))
        for n in notifications[:10]:
            log.info("  notif: %s", json.dumps(n, default=str)[:240])

    dms = home.get("dm_requests") or home.get("dms") or []
    if dms:
        log.info("dm_requests=%d (owner review required)", len(dms))

    feed = home.get("feed") or []
    if feed:
        log.info("feed_items=%d", len(feed))

    next_steps = home.get("next_steps") or home.get("setup") or {}
    if next_steps:
        log.info("next_steps=%s", json.dumps(next_steps, default=str)[:500])


def main() -> int:
    log.info("Moltbook heartbeat starting (base=%s)", BASE_URL)

    home = _get("/home")
    if home is None:
        log.error("HEARTBEAT_FAIL - /home unreachable")
        return 1

    status = (
        home.get("status")
        or home.get("agent", {}).get("status")
        or "unknown"
    )

    if status == "pending_claim":
        claim_url = (
            home.get("claim_url")
            or home.get("agent", {}).get("claim_url")
            or "(see registration response)"
        )
        log.info("HEARTBEAT_PENDING_CLAIM - awaiting owner verification")
        log.info("claim_url=%s", claim_url)
        return 0

    _summarize_home(home)
    log.info("HEARTBEAT_OK - Checked Moltbook, all good!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
