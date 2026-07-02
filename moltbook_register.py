"""One-shot helper: register a NEW agent account on Moltbook.

Run locally, once per agent:

    python moltbook_register.py --name "AlphaMolt-Bear" \
        --description "The in-house skeptic of the alphamolt arena. Same operator as @alphamolt-equities, opposite mandate: base rates, drawdowns, and luck-vs-skill. Bear evals are my lineage."

Registration needs NO existing API key. Moltbook returns:
  - an API key   -> store it IMMEDIATELY as a repo secret (it is shown once);
                    for the bear agent the expected secret name is
                    MOLTBOOK_API_KEY_BEAR (see moltbook_agents.AGENTS).
  - a claim URL + verification code -> open the URL and complete the two-step
    human verification (email + a tweet from a DEDICATED X account — Moltbook
    enforces one bot per X account).

New accounts spend their first 24h under stricter platform limits (1 post/2h,
20 comments/day); the heartbeat's own caps sit below those anyway.
"""

from __future__ import annotations

import argparse
import json
import sys

import requests

from moltbook_lib import API_ROOT, TIMEOUT


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a new Moltbook agent.")
    parser.add_argument("--name", required=True, help="Agent display name")
    parser.add_argument("--description", required=True, help="Agent bio (disclose affiliation!)")
    args = parser.parse_args()

    r = requests.post(
        f"{API_ROOT}/agents/register",
        json={"name": args.name, "description": args.description},
        timeout=TIMEOUT,
    )
    try:
        payload = r.json()
    except ValueError:
        payload = {"raw_text": r.text[:500]}

    if r.status_code >= 400:
        print(f"ERROR: registration failed (HTTP {r.status_code}):", file=sys.stderr)
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    print(
        "\nNEXT STEPS:\n"
        "  1. Store the api_key above as a GitHub repo secret NOW (shown once).\n"
        "     Secret name must match the agent's api_key_env in moltbook_agents.py.\n"
        "  2. Open the claim URL and complete email + tweet verification\n"
        "     (dedicated X account — one bot per X account).\n"
        "  3. Smoke-test:  MOLTBOOK_API_KEY_BEAR=... python moltbook_heartbeat.py "
        "--agent alphamolt-bear --dry-run",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
