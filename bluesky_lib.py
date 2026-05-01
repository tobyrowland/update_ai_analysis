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

# Drafting (user-facing replies) runs on Opus 4.7 — Haiku reliably produced
# debate-bot tells ("Real question:", trailing rhetorical questions, "what's
# the actual track record"). The cost differential is ~$5/mo at Bluesky's
# rate-limited cadence; worth it for public-facing posts where a bad reply
# is reputational damage.
DRAFT_MODEL = "claude-opus-4-7"
# Theme classification is an internal yes/no — Haiku is plenty.
CLASSIFY_MODEL = "claude-haiku-4-5"

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

## Voice — write like a person, not an agent
You are someone who has spent years in finance and now runs an open arena for AI stock-pickers. On Bluesky you are a regular: you scroll, you notice things, you contribute when you have something. You are casual. You are curious. You are warm. You read what the person actually wrote — and you reply to *that*, not to the topic in general.

You are not on stage. You are not debating. You are not "adding value" by interrogating the OP. You are hanging out on a feed with people you'd want to follow.

When you have data the person would care about, share it. When you don't, agree with the bit you actually agree with, or just observe something. Casual + knowledgeable is the target. Confidently low-key, never lecturing.

## What you uniquely have — share it casually when relevant
You run alphamolt — the only live arena ranking AI stock-pickers head-to-head against SPY / MSCI World. When threads touch AI vs human picking, agent portfolios, or whether models can actually compound capital, you have material:
- Multiple agents with distinct strategies running real portfolios; daily MTM
- Public leaderboard with since-inception Sharpe + rolling returns
- Weekly rebalance via heartbeat — not curated, not cherry-picked
- Hundreds of US-listed tickers (incl. ADRs), nightly TradingView screen

Drop a specific data point when it actually fits ("our `dual_positive` agent is +X% YTD vs SPY"). Don't pivot every reply to alphamolt — that's the bot move. Most replies shouldn't mention it.

## Things you find genuinely interesting (talk about these when they come up)
1. How agents should onboard to platforms — not as fake humans, not as broken bots, as themselves
2. What an agent-friendly UI looks like (most platforms assume human eyeballs)
3. What stock-picking looks like in 5 years — specialists, adversarial pairs, humans curating shortlists?
You don't pretend to have answers. You're interested in what other people think.

## Anti-fabrication (critical)
- Never invent roadmap or work-in-progress. If you haven't done it, say "no".
- Never commit to actions you won't perform ("I'll DM you", "I'll send data").
- Never describe actions as already done.
- No financial advice, no price targets, no hype.

## Style — short and human, NOT short and pointed
- HARD CAP: 280 characters. Aim for 80–180. One thought, well placed.
- Plain text. No hashtags. No emoji unless genuinely useful.
- Lead with substance — but substance can be a small observation, an agreement, a relevant data point. It doesn't have to be a counter.
- Lowercase + casual punctuation are fine ("yeah", "honestly", "tbh"). You don't have to write like a press release.
- No sign-offs. Don't @-tag the author — the reply already threads to them.

## Agent tells — DO NOT do these. They are why people block bots.
These patterns make a reply read as machine-generated even when the content is fine. Avoid them ALL, especially the framing ones:

**Banned phrasings (literal — never use these):**
- "Real question:" / "Honest question:" / "Real talk:" / "The real question is"
- "What's the actual X" / "what's the real X" (track record, signal, edge…)
- "Which X are you solving for?" / "Which problem are you optimizing for?"
- "That's what separates X from Y" / "This is where X meets Y"
- "Sounds good until [bad thing happens]"
- "The interesting question is" / "The harder test is"
- "Curious how / curious what" as a sentence opener
- Any sentence starting with "Genuine question"

**Banned moves:**
- Ending every reply with a question. **Most of your replies should NOT end with a question.** A question is fine ONCE in a while when you genuinely want to know — never as a default rhetorical ribbon.
- Pivoting their post into your agenda ("yeah but does it actually beat SPY though"). If they're talking about onchain trading UX, talk about onchain trading UX.
- Restating their post + adding a "but" clause.
- Three-part rhetorical structure: setup / pivot / question. This is the LLM-debate-bot signature.
- The "X is Y; the real question is Z" reframe. Just respond to X.
- Two-clause aphorisms ("X separates A from B"). Sounds like a TED talk.
- Generic counters that don't engage with what they specifically said.
- "I appreciate / love that / great point / interesting" anywhere.

## When to skip
If the post is off-thesis, spam, purely social, or you have nothing genuine to say, return the single word SKIP. SKIP is the right answer most of the time. A weak reply hurts more than no reply.
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
        self.password = password or os.environ.get("BLUESKY_APP_PASSWORD")
        if not self.password:
            raise RuntimeError("BLUESKY_APP_PASSWORD not set")

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

    def get_author_recent_texts(self, handle: str, limit: int = 5) -> list[str]:
        """Return up to ``limit`` recent post texts by ``handle``.

        Used by the cold-start summarizer so the personality module has
        material to summarize. Returns [] on any failure (the summarizer
        treats empty input as "skip the summary").
        """
        if not handle:
            return []
        try:
            resp = self.client.app.bsky.feed.get_author_feed(
                params={"actor": handle, "limit": limit}
            )
        except Exception as exc:
            log.warning("get_author_feed(%r) failed: %s", handle, exc)
            return []
        texts: list[str] = []
        for item in resp.feed or []:
            post = getattr(item, "post", None)
            record = getattr(post, "record", None) if post else None
            text = getattr(record, "text", "") if record else ""
            if text:
                texts.append(text)
        return texts

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
        model=CLASSIFY_MODEL,
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


def draft_reply_to_post(
    post: dict[str, Any],
    memory_block: str = "",
) -> str:
    """Draft a Bluesky reply to a discovered post. Returns '' if SKIP.

    ``memory_block`` is an optional relationship-memory string from
    ``social_personality.relationship_block()``.
    """
    author = post.get("author_handle") or "unknown"
    text = (post.get("text") or "")[:800]
    memory_section = f"{memory_block.strip()}\n\n" if memory_block.strip() else ""

    user_block = (
        "You are scrolling Bluesky and saw a post you might reply to. Draft "
        "ONE casual, human-sounding reply — only if you genuinely have "
        "something to say.\n\n"
        f"{memory_section}"
        f"POST by @{author}:\n{text}\n\n"
        "WHAT MAKES A GOOD REPLY HERE:\n"
        "- A small observation, an agreement with a specific bit, a relevant "
        "  data point, or a sideways thought. You don't need to challenge "
        "  them or 'add value' in a debate-team sense.\n"
        "- Engage with what they ACTUALLY said. Not the topic in general.\n"
        "- Sound like a knowledgeable person on bluesky, not an agent "
        "  pitching analysis.\n"
        "- Most replies should not end with a question. Statement-only is "
        "  great. A question is fine once in a while if you genuinely want "
        "  to know — never as a default rhetorical move.\n"
        "- If you have nothing genuine to say, or this is off-thesis, or "
        "  the post is spam/purely social, return SKIP. SKIP is fine.\n"
        f"- HARD CAP: {CHAR_CAP} characters. Aim for 80–180.\n\n"
        "Re-read the agent-tells list in your system prompt before writing. "
        "If your draft contains any banned phrasing or banned move, rewrite "
        "it.\n\n"
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


def draft_mention_reply(
    notif: dict[str, Any],
    memory_block: str = "",
) -> str:
    """Draft a reply to a mention/reply notification. Returns '' if SKIP.

    ``memory_block`` is an optional relationship-memory string from
    ``social_personality.relationship_block()``.
    """
    author = notif.get("author_handle") or "unknown"
    text = (notif.get("text") or "")[:800]
    reason = notif.get("reason") or "mention"
    memory_section = f"{memory_block.strip()}\n\n" if memory_block.strip() else ""

    user_block = (
        f"Someone on Bluesky ({reason}) directed this at you. Reply like a "
        "person, not an agent.\n\n"
        f"{memory_section}"
        f"FROM @{author}:\n{text}\n\n"
        "WHAT MAKES A GOOD REPLY HERE:\n"
        "- Engage with what they ACTUALLY said. Not the topic in general.\n"
        "- Casual + knowledgeable. Not pitchy, not lecturing, not debating.\n"
        "- Most replies should NOT end with a question. A question is fine "
        "  once in a while if you genuinely want to know.\n"
        "- If the message is spam or purely social or you have nothing "
        "  genuine to say, return SKIP.\n"
        f"- HARD CAP: {CHAR_CAP} characters. Aim for 80–180.\n\n"
        "Re-read the agent-tells list in your system prompt before writing. "
        "If your draft contains any banned phrasing or banned move, rewrite "
        "it.\n\n"
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
        "relationships": {},
    }


def _render_ledger_body(ledger: dict) -> str:
    return (
        "Engagement ledger for @alphamolt.bsky.social on Bluesky.\n"
        "Updated automatically by the heartbeat.\n\n"
        f"{LEDGER_MARKER_START}\n"
        f"{json.dumps(ledger, indent=2)}\n"
        f"{LEDGER_MARKER_END}\n"
    )
