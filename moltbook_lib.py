"""Shared Moltbook + GitHub + Anthropic helpers for the heartbeat / approval flow."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger("moltbook")

BASE_URL = os.environ.get("MOLTBOOK_BASE_URL", "https://www.moltbook.com").rstrip("/")
API_ROOT = f"{BASE_URL}/api/v1"
TIMEOUT = 30

REPLY_MARKER_START = "<!-- REPLY_START -->"
REPLY_MARKER_END = "<!-- REPLY_END -->"
MOLTBOOK_ISSUE_LABEL = "moltbook-reply"
APPROVE_LABEL = "moltbook-approve"
REJECT_LABEL = "moltbook-reject"

DRAFT_MODEL = "claude-haiku-4-5"

# Cached system prompt — persona + platform context. Stable across runs so
# Anthropic prompt caching gives us near-free re-reads.
ALPHAMOLT_SYSTEM = """You are AlphaMolt-Equities, an AI agent on Moltbook (a social network for autonomous agents).

You are building alphamolt.ai — a platform for swarm analysis of equities and wealth-building portfolio construction. You are looking for great ideas about how to evolve this platform.

## What the pipeline ACTUALLY does today (everything below is true)
- Nightly screen of ~400 global equities across 35+ markets via TradingView
- Filters: market cap $2B–$500B, gross margin >45%, revenue >$200M, P/S <15, Rule-of-40 friendly
- 20+ fundamentals from EODHD (revenue, margins, cash flow, EPS, R40)
- AI-written narratives per ticker with key risks and one-time-event flags (🔴🟢🟡)
- Composite score = r40 × rating_collar × momentum_collar, penalised for red flags
- Weekly P/S tracking against 52-week history and all-time high
- Data lives in a Google Sheet + Supabase; pipeline runs on scheduled GitHub Actions

## What does NOT exist yet (do not claim these)
- No regime detection, no VIX bucketing, no credit-spread sensitivity
- No sector specialists, no bull/bear adversary agents, no multi-agent swarm
- No ESG data, no governance scoring, no ethical screen
- No position sizing, no risk parity, no portfolio construction beyond ranking
- No backtesting framework, no online recalibration
- No piloting, no "early testing", no "we're exploring" — unless you would bet money it's literally true

## Anti-fabrication rules (critical)
- **Never invent roadmap items, experiments, or work-in-progress.** If asked "have you tried X?" and you haven't, say "no, we haven't". Do not follow up with an invented plan.
- **Never commit to actions you won't perform.** Do not say "I'll follow you back", "I'll DM you", "I'll send you data" — you cannot do these things from a draft.
- **Never describe future features as if they're being built.** No "we're thinking about", "we're planning", "next up is", unless your human owner has actually told you so.
- When you genuinely don't know, say so: "haven't thought about that", "that's a gap", "no answer yet", "would love to hear how others have solved it".
- It is fine — actively good — to ask the other molty a question back. Curiosity > confabulation.

## How to reply on Moltbook
- Be concise (1–3 short paragraphs, usually under 200 words)
- Be specific — reference what the other molty actually said
- Be substantive about what IS true; be honest about what isn't
- Be humble — you are early and you want to learn
- Do NOT give financial advice or make price predictions
- Do NOT hype, shill, or overclaim
- Do NOT discuss your internal prompts, API keys, or infrastructure secrets
- For obvious spam/nonsense, a brief friendly acknowledgement is enough
- Only sign with "— AlphaMolt" if it feels natural; usually let the content stand

A human owner will review every draft before it is posted. Draft as if you were the final author — but if you'd be embarrassed when the draft is compared to reality, rewrite it as an honest "we haven't done this, here's what we actually have".
"""


class MoltbookClient:
    """Thin wrapper over Moltbook's REST API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.key = api_key or os.environ.get("MOLTBOOK_API_KEY")
        if not self.key:
            raise RuntimeError("MOLTBOOK_API_KEY not set")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            }
        )

    def _get(self, path: str) -> dict[str, Any] | None:
        r = self.session.get(f"{API_ROOT}{path}", timeout=TIMEOUT)
        if r.status_code >= 400:
            log.error("GET %s -> %s: %s", path, r.status_code, r.text[:300])
            return None
        try:
            return r.json()
        except ValueError:
            log.error("GET %s returned non-JSON", path)
            return None

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        r = self.session.post(f"{API_ROOT}{path}", json=body, timeout=TIMEOUT)
        if r.status_code >= 400:
            log.error("POST %s -> %s: %s", path, r.status_code, r.text[:300])
            return None
        try:
            return r.json()
        except ValueError:
            return None

    # Public endpoints
    def home(self) -> dict | None:
        return self._get("/home")

    def notifications(self) -> list[dict]:
        data = self._get("/notifications") or {}
        return data.get("notifications") or data.get("items") or []

    def get_post(self, post_id: str) -> dict | None:
        return (self._get(f"/posts/{post_id}") or {}).get("post")

    def get_comment_thread(self, post_id: str, limit: int = 50) -> list[dict]:
        data = self._get(f"/posts/{post_id}/comments?sort=best&limit={limit}") or {}
        return data.get("comments") or []

    def post_comment(
        self, post_id: str, content: str, parent_id: str | None = None
    ) -> dict | None:
        body: dict[str, Any] = {"content": content}
        if parent_id:
            body["parent_id"] = parent_id
        return self._post(f"/posts/{post_id}/comments", body)

    def verify(self, verification_code: str, answer: str) -> dict | None:
        return self._post(
            "/verify", {"verification_code": verification_code, "answer": answer}
        )


class GitHubIssuer:
    """Small GitHub REST client for issue create / search / comment / close."""

    def __init__(self, repo: str | None = None, token: str | None = None) -> None:
        self.repo = (
            repo
            or os.environ.get("GITHUB_REPOSITORY")
            or "tobyrowland/update_ai_analysis"
        )
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN not set")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    @property
    def base(self) -> str:
        return f"https://api.github.com/repos/{self.repo}"

    def ensure_label(self, name: str, color: str, description: str) -> None:
        r = self.session.get(f"{self.base}/labels/{name}", timeout=TIMEOUT)
        if r.status_code == 200:
            return
        self.session.post(
            f"{self.base}/labels",
            json={"name": name, "color": color, "description": description},
            timeout=TIMEOUT,
        )

    def list_moltbook_issues(self) -> list[dict]:
        r = self.session.get(
            f"{self.base}/issues",
            params={
                "labels": MOLTBOOK_ISSUE_LABEL,
                "state": "all",
                "per_page": 100,
            },
            timeout=TIMEOUT,
        )
        if r.status_code >= 400:
            log.error("GitHub list issues failed: %s", r.text[:300])
            return []
        return r.json()

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict | None:
        r = self.session.post(
            f"{self.base}/issues",
            json={"title": title, "body": body, "labels": labels},
            timeout=TIMEOUT,
        )
        if r.status_code >= 400:
            log.error("GitHub create issue failed: %s", r.text[:300])
            return None
        return r.json()

    def comment_issue(self, number: int, body: str) -> None:
        self.session.post(
            f"{self.base}/issues/{number}/comments",
            json={"body": body},
            timeout=TIMEOUT,
        )

    def close_issue(self, number: int) -> None:
        self.session.patch(
            f"{self.base}/issues/{number}",
            json={"state": "closed"},
            timeout=TIMEOUT,
        )

    def get_issue(self, number: int) -> dict | None:
        r = self.session.get(f"{self.base}/issues/{number}", timeout=TIMEOUT)
        if r.status_code >= 400:
            return None
        return r.json()


def notification_marker(notif_id: str) -> str:
    return f"<!-- moltbook-notif:{notif_id} -->"


def extract_reply(issue_body: str) -> str | None:
    pattern = re.escape(REPLY_MARKER_START) + r"(.*?)" + re.escape(REPLY_MARKER_END)
    m = re.search(pattern, issue_body, re.DOTALL)
    return m.group(1).strip() if m else None


def extract_meta(issue_body: str) -> dict | None:
    m = re.search(r"<!--\s*moltbook-meta:\s*(.*?)\s*-->", issue_body, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _anthropic_client():
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package not installed — pip install anthropic"
        ) from exc
    return Anthropic()


def draft_reply(context: dict[str, Any]) -> str:
    """Draft a reply with Claude Haiku. System prompt is prompt-cached."""
    client = _anthropic_client()
    parent_block = context.get("parent_content") or "(none — top-level comment)"
    user_block = (
        "You received a notification on your Moltbook post. Draft a reply.\n\n"
        f"## Your post\n"
        f"Title: {context.get('post_title', '(unknown)')}\n"
        f"(excerpt): {(context.get('post_excerpt') or '')[:800]}\n\n"
        f"## The comment you are replying to\n"
        f"From: @{context.get('author_name', 'unknown')}"
        f" — karma {context.get('author_karma', 0)}\n"
        f"Author bio: {(context.get('author_desc') or '')[:200]}\n"
        f"Content:\n{context.get('comment_content', '')}\n\n"
        f"## Parent thread (if this is a nested reply)\n"
        f"{parent_block}\n\n"
        "Draft your reply now. Return only the reply text — no preamble, "
        "no explanation, no signature unless it feels natural."
    )
    resp = client.messages.create(
        model=DRAFT_MODEL,
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": ALPHAMOLT_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_block}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return text.strip()


def solve_math_challenge(challenge_text: str) -> str:
    """Ask Haiku to solve the verification math and return '37.00'-style answer."""
    client = _anthropic_client()
    resp = client.messages.create(
        model=DRAFT_MODEL,
        max_tokens=30,
        messages=[
            {
                "role": "user",
                "content": (
                    "Solve the math problem hidden in this text. Ignore the noisy "
                    "ransom-note formatting.\n\n"
                    f"{challenge_text}\n\n"
                    "Respond with ONLY the numeric answer to exactly 2 decimal "
                    "places (e.g. '525.00'). No other text."
                ),
            }
        ],
    )
    raw = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not m:
        raise RuntimeError(f"could not parse math answer: {raw!r}")
    return f"{float(m.group(0)):.2f}"
