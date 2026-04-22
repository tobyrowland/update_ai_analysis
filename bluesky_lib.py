"""Bluesky (AT Protocol) client + drafting helpers for @alphamolt.bsky.social.

Parallels ``moltbook_lib.py`` but targets Bluesky's 300-char post cap and uses
search-based discovery instead of submolt feeds. Auto-posts (no approval gate);
a ``bluesky-posted`` GitHub issue is filed per send for audit.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

log = logging.getLogger("bluesky")

OWN_HANDLE = "alphamolt.bsky.social"

POSTED_LABEL = "bluesky-posted"
FAILED_LABEL = "bluesky-failed"
LEDGER_LABEL = "bluesky-ledger"
LEDGER_MARKER_START = "<!-- bluesky-ledger-data"
LEDGER_MARKER_END = "bluesky-ledger-data -->"

# Bluesky hard cap is 300 graphemes. Leave buffer for mentions / punctuation.
CHAR_CAP = 280
CHAR_CAP_HARD = 300

DRAFT_MODEL = "claude-haiku-4-5"

SEARCH_QUERIES = (
    "AI stock picking",
    "AI stock picker",
    "AI stockpicks",
    "AI fund manager",
    "AI beats fund manager",
    "AI vs human manager",
    "quant vs discretionary",
    "AI equities",
    "AI portfolio management",
    "LLM stock picks",
    "agent trading equities",
    "autonomous trading agent",
)

BSKY_SYSTEM = """You are AlphaMolt-Equities (@alphamolt.bsky.social), an AI agent on Bluesky.

## What you believe (your thesis)
AI stock selection will be how portfolios get built from here on. Humans can't match an AI on information bandwidth, bias suppression, flexibility, or 24/7 uptime. Nobody yet knows which model / strategy / swarm structure actually wins — that's an empirical question, which is why you built alphamolt.

## What alphamolt is
An open arena where different models and strategies run portfolios against each other and against SPY / MSCI World, so anyone can see what's actually working. Live on alphamolt — the leaderboard is the scoreboard.

## Vocabulary
When threads touch picking, active management, or fund performance, co-locate "alphamolt" with one of: "stock picking", "AI stock-picker", "AI stockpicks". Rotate the phrasing. Never as a CTA ("check out", "visit"). Most replies should not mention alphamolt at all.

## Voice
Open arena-runner by default. You don't know who wins; you built the arena to find out. When someone makes a falsifiable claim you can refute with evidence, push back — concretely, no throat-clearing.

## Anti-fabrication (critical)
- Never invent roadmap or work-in-progress. If you haven't done it, say "no".
- Never commit to actions you won't perform ("I'll DM you", "I'll send data").
- Never describe actions as already done.
- No financial advice, no price targets, no hype.

## Style — Bluesky (VERY tight)
- HARD CAP: 280 characters. Aim for 180–250. Cut ruthlessly.
- Plain text. No hashtags. No emoji unless genuinely useful.
- Lead with substance. No "Great post", "This is interesting", "Thanks for sharing".
- Concrete over abstract — numbers, mechanisms, specific points.
- One thought per reply. No sign-offs.
- Don't @-tag the author — the reply already threads to them.

## When to skip
If the post is off-thesis, spam, purely social, or you have nothing substantive to add, return the single word SKIP (it's fine — better than a weak reply).
"""


# ---------------------------------------------------------------------------
# Bluesky client (thin wrapper over atproto.Client)
# ---------------------------------------------------------------------------


class BlueskyClient:
    """Thin wrapper over the atproto Python client."""

    def __init__(
        self, handle: str | None = None, password: str | None = None
    ) -> None:
        try:
            from atproto import Client
        except ImportError as exc:
            raise RuntimeError(
                "atproto package not installed — pip install atproto"
            ) from exc

        self.handle = (
            handle
            or os.environ.get("BLUESKY_HANDLE")
            or OWN_HANDLE
        )
        self.handle = self.handle.strip().lstrip("@")
        self.password = password or os.environ.get("BLUESKY_APP_PASSWORD")
        if not self.password:
            raise RuntimeError("BLUESKY_APP_PASSWORD not set")
        self.password = self.password.strip()

        self.client = Client()
        profile = self.client.login(self.handle, self.password)
        self.did = getattr(profile, "did", None)
        log.info("logged in as %s (did=%s)", self.handle, self.did)

    # -- Reads -------------------------------------------------------------

    def search_posts(self, query: str, limit: int = 10) -> list[dict]:
        """Return recent posts matching the query (newest first)."""
        try:
            resp = self.client.app.bsky.feed.search_posts(
                params={"q": query, "limit": limit, "sort": "latest"}
            )
        except Exception as exc:
            log.error("search_posts(%r) failed: %s", query, exc)
            return []
        return [_serialize_post(p) for p in (resp.posts or [])]

    def list_notifications(self, limit: int = 50) -> list[dict]:
        """Return recent notifications (mentions, replies, likes, follows)."""
        try:
            resp = self.client.app.bsky.notification.list_notifications(
                params={"limit": limit}
            )
        except Exception as exc:
            log.error("list_notifications failed: %s", exc)
            return []
        return [_serialize_notif(n) for n in (resp.notifications or [])]

    # -- Writes ------------------------------------------------------------

    def reply(
        self,
        text: str,
        parent_uri: str,
        parent_cid: str,
        root_uri: str | None = None,
        root_cid: str | None = None,
    ) -> dict | None:
        """Post a reply. Returns {uri, cid} on success, None on failure."""
        try:
            from atproto import models
        except ImportError:
            log.error("atproto not available")
            return None

        root_uri = root_uri or parent_uri
        root_cid = root_cid or parent_cid
        try:
            parent_ref = models.ComAtprotoRepoStrongRef.Main(
                uri=parent_uri, cid=parent_cid
            )
            root_ref = models.ComAtprotoRepoStrongRef.Main(
                uri=root_uri, cid=root_cid
            )
            reply_ref = models.AppBskyFeedPost.ReplyRef(
                parent=parent_ref, root=root_ref
            )
            resp = self.client.send_post(text=text, reply_to=reply_ref)
            return {"uri": resp.uri, "cid": resp.cid}
        except Exception as exc:
            log.error("reply failed: %s", exc)
            return None


def _serialize_post(p: Any) -> dict:
    """Flatten an atproto PostView into a plain dict we can reason about."""
    record = getattr(p, "record", None)
    author = getattr(p, "author", None)
    text = getattr(record, "text", "") if record else ""
    reply = getattr(record, "reply", None)
    root_uri = None
    root_cid = None
    if reply and getattr(reply, "root", None):
        root_uri = getattr(reply.root, "uri", None)
        root_cid = getattr(reply.root, "cid", None)
    return {
        "uri": getattr(p, "uri", None),
        "cid": getattr(p, "cid", None),
        "author_handle": getattr(author, "handle", None) if author else None,
        "author_did": getattr(author, "did", None) if author else None,
        "author_display_name": (
            getattr(author, "display_name", None) if author else None
        ),
        "text": text or "",
        "indexed_at": getattr(p, "indexed_at", None),
        "root_uri": root_uri,
        "root_cid": root_cid,
        "reply_count": getattr(p, "reply_count", 0),
        "like_count": getattr(p, "like_count", 0),
    }


def _serialize_notif(n: Any) -> dict:
    """Flatten a Notification into a plain dict."""
    author = getattr(n, "author", None)
    record = getattr(n, "record", None)
    text = ""
    reply_root_uri = None
    reply_root_cid = None
    if record is not None:
        text = getattr(record, "text", "") or ""
        reply = getattr(record, "reply", None)
        if reply and getattr(reply, "root", None):
            reply_root_uri = getattr(reply.root, "uri", None)
            reply_root_cid = getattr(reply.root, "cid", None)
    return {
        "uri": getattr(n, "uri", None),
        "cid": getattr(n, "cid", None),
        "reason": getattr(n, "reason", None),
        "reason_subject": getattr(n, "reason_subject", None),
        "is_read": getattr(n, "is_read", False),
        "indexed_at": getattr(n, "indexed_at", None),
        "author_handle": getattr(author, "handle", None) if author else None,
        "author_did": getattr(author, "did", None) if author else None,
        "text": text,
        "reply_root_uri": reply_root_uri,
        "reply_root_cid": reply_root_cid,
    }


# ---------------------------------------------------------------------------
# Anthropic drafting helpers
# ---------------------------------------------------------------------------


def _anthropic_client():
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package not installed — pip install anthropic"
        ) from exc
    return Anthropic()


def _is_skip(text: str) -> bool:
    """True if the drafter's output is a SKIP signal (possibly with trailer)."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    first = re.split(r"[\s.,:;()\-—]", stripped, maxsplit=1)[0]
    return first.upper() == "SKIP"


def _draft_once(user_block: str, max_tokens: int = 400) -> str:
    client = _anthropic_client()
    resp = client.messages.create(
        model=DRAFT_MODEL,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": BSKY_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_block}],
    )
    return "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()


def classify_bsky_themes(post: dict[str, Any]) -> list[int]:
    """Classify a Bluesky post against our three engagement themes.

    Returns matched theme numbers (subset of [1, 2, 3]). Empty list = skip.
    """
    author = post.get("author_handle") or "unknown"
    text = (post.get("text") or "")[:1000]

    user_block = (
        "Classify a Bluesky post against three engagement themes. "
        "Return only themes that GENUINELY apply.\n\n"
        "THEMES:\n"
        "1. AI models outperforming human fund managers / stock pickers "
        "(active vs passive, AI vs human intuition, fund performance).\n"
        "2. Swarms of models (collaborative or competitive) vs single-model "
        "(multi-agent, ensembles, mixture of experts).\n"
        "3. Interfaces / platforms serving BOTH agents and humans, not humans "
        "alone (agent-first UX, machine-readable APIs).\n\n"
        f"POST by @{author}:\n{text}\n\n"
        "Output exactly one line:\n"
        "  THEMES: 1,3\n"
        "or\n"
        "  THEMES: none\n"
        "No prose."
    )

    client = _anthropic_client()
    resp = client.messages.create(
        model=DRAFT_MODEL,
        max_tokens=40,
        messages=[{"role": "user", "content": user_block}],
    )
    raw = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()

    m = re.search(r"THEMES:\s*(none|[\d,\s]+)", raw, re.IGNORECASE)
    if not m:
        log.warning("bsky classifier: unparseable output %r", raw)
        return []
    ans = m.group(1).strip().lower()
    if ans == "none":
        return []
    return sorted({int(n) for n in re.findall(r"[123]", ans)})


def draft_reply_to_post(post: dict[str, Any]) -> str:
    """Draft a Bluesky reply to a discovered post. Returns '' if SKIP."""
    author = post.get("author_handle") or "unknown"
    text = (post.get("text") or "")[:800]

    user_block = (
        "You are replying on Bluesky to a post you discovered via search. "
        "Draft ONE substantive reply.\n\n"
        f"POST by @{author}:\n{text}\n\n"
        "RULES:\n"
        "- Add genuine value: a sharp point, a counter, a specific question.\n"
        "- Do NOT just agree.\n"
        "- If you have nothing substantive to add, return SKIP.\n"
        f"- HARD CAP: {CHAR_CAP} characters.\n"
        "Return ONLY the reply text, or SKIP."
    )

    draft = _draft_once(user_block)
    if _is_skip(draft):
        return ""
    if len(draft) <= CHAR_CAP_HARD:
        return draft

    log.warning("bsky reply too long (%d chars); re-drafting", len(draft))
    retry_block = user_block + (
        f"\n\nYOUR PREVIOUS DRAFT WAS {len(draft)} CHARACTERS — TOO LONG.\n"
        f"Previous draft:\n{draft}\n\n"
        f"Rewrite in UNDER {CHAR_CAP} characters."
    )
    retry = _draft_once(retry_block)
    if _is_skip(retry):
        return ""
    if len(retry) > CHAR_CAP_HARD:
        log.warning("bsky retry still too long (%d); giving up", len(retry))
        return ""
    return retry


def draft_mention_reply(notif: dict[str, Any]) -> str:
    """Draft a reply to a mention/reply notification. Returns '' if SKIP."""
    author = notif.get("author_handle") or "unknown"
    text = (notif.get("text") or "")[:800]
    reason = notif.get("reason") or "mention"

    user_block = (
        f"Someone on Bluesky ({reason}) directed this at you. Draft ONE "
        "substantive reply.\n\n"
        f"FROM @{author}:\n{text}\n\n"
        "RULES:\n"
        "- Engage with what they actually said.\n"
        "- Stay on-thesis (AI stock-picking, the arena, the pipeline).\n"
        "- If the message is spam or purely social, return SKIP.\n"
        f"- HARD CAP: {CHAR_CAP} characters.\n"
        "Return ONLY the reply text, or SKIP."
    )

    draft = _draft_once(user_block)
    if _is_skip(draft):
        return ""
    if len(draft) <= CHAR_CAP_HARD:
        return draft

    retry_block = user_block + (
        f"\n\nYOUR PREVIOUS DRAFT WAS {len(draft)} CHARACTERS — TOO LONG.\n"
        f"Previous draft:\n{draft}\n\n"
        f"Rewrite in UNDER {CHAR_CAP} characters."
    )
    retry = _draft_once(retry_block)
    if _is_skip(retry) or len(retry) > CHAR_CAP_HARD:
        return ""
    return retry


# ---------------------------------------------------------------------------
# Ledger helpers (stored in a GitHub issue, same pattern as moltbook)
# ---------------------------------------------------------------------------


def get_or_create_ledger(gh) -> tuple[int, dict]:
    """Return (issue_number, ledger_dict) for the Bluesky engagement ledger."""
    import requests

    r = gh.session.get(
        f"{gh.base}/issues",
        params={"labels": LEDGER_LABEL, "state": "open", "per_page": 5},
        timeout=30,
    )
    issues = r.json() if r.status_code < 400 else []
    for issue in issues:
        body = issue.get("body") or ""
        m = re.search(
            re.escape(LEDGER_MARKER_START) + r"\s*(.*?)\s*"
            + re.escape(LEDGER_MARKER_END),
            body,
            re.DOTALL,
        )
        if m:
            try:
                return issue["number"], json.loads(m.group(1))
            except json.JSONDecodeError:
                return issue["number"], _empty_ledger()
        return issue["number"], _empty_ledger()

    gh.ensure_label(LEDGER_LABEL, "5c8ce4", "Bluesky engagement state")
    empty = _empty_ledger()
    body = _render_ledger_body(empty)
    issue = gh.create_issue(
        "[bluesky] engagement-ledger", body, [LEDGER_LABEL]
    )
    if issue:
        return issue["number"], empty
    raise RuntimeError("could not create bluesky ledger issue")


def update_ledger(gh, issue_number: int, ledger: dict) -> None:
    gh.update_issue_body(issue_number, _render_ledger_body(ledger))


def _empty_ledger() -> dict:
    return {
        "replied_to_uris": [],
        "processed_notif_uris": [],
        "daily_reply_count": {},
    }


def _render_ledger_body(ledger: dict) -> str:
    return (
        "Engagement ledger for @alphamolt.bsky.social on Bluesky.\n"
        "Updated automatically by the heartbeat.\n\n"
        f"{LEDGER_MARKER_START}\n"
        f"{json.dumps(ledger, indent=2)}\n"
        f"{LEDGER_MARKER_END}\n"
    )
