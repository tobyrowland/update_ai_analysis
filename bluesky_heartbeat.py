"""Bluesky heartbeat — reply to mentions + search for AI-in-finance posts.

Runs every 4 hours via ``.github/workflows/bluesky-heartbeat.yml``.

Phase 1 — Mentions (always on)
    Reply to unread mention/reply notifications. No approval gate; post goes
    straight up and an audit issue (``bluesky-posted``) is filed.

Phase 2 — Search engagement (``--no-search`` to disable)
    For each seeded search query (AI stock picking, AI fund manager, etc.),
    fetch recent matching posts, run the three-theme classifier, and reply
    to the best matches until we hit the per-run cap.

Auto-posts everything. Dedup + rate limits tracked in a GitHub issue
labelled ``bluesky-ledger``.

Env vars:
    BLUESKY_HANDLE         default alphamolt.bsky.social
    BLUESKY_APP_PASSWORD   required — Bluesky app-specific password
    ANTHROPIC_API_KEY      required — drafter + classifier
    GITHUB_TOKEN           required unless --dry-run (audit issues)
    GITHUB_REPOSITORY      set automatically in GitHub Actions
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from moltbook_lib import GitHubIssuer

from bluesky_lib import (
    BlueskyClient,
    FAILED_LABEL,
    OWN_HANDLE,
    POSTED_LABEL,
    SEARCH_QUERIES,
    classify_bsky_themes,
    draft_mention_reply,
    draft_reply_to_post,
    get_or_create_ledger,
    update_ledger,
)

MAX_REPLIES_PER_RUN = 3
MAX_REPLIES_PER_DAY = 10
SEARCH_LIMIT_PER_QUERY = 8

REPLYABLE_REASONS = {"mention", "reply"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bluesky-heartbeat")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _first_line(s: str, maxlen: int = 120) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    first = s.splitlines()[0]
    return first[: maxlen - 1] + "…" if len(first) > maxlen else first


# ---------------------------------------------------------------------------
# Phase 1 — Mentions / replies on our posts
# ---------------------------------------------------------------------------


def _process_mentions(
    client: BlueskyClient,
    gh: GitHubIssuer | None,
    ledger: dict,
    args: argparse.Namespace,
) -> dict:
    stats = {"posted": 0, "skipped": 0, "failed": 0}
    notifs = client.list_notifications(limit=50)
    log.info("notifications fetched: %d", len(notifs))

    processed = set(ledger.get("processed_notif_uris", []))
    replied = set(ledger.get("replied_to_uris", []))
    today = _today()
    daily = ledger.setdefault("daily_reply_count", {})
    replies_today = daily.get(today, 0)

    actionable = [
        n for n in notifs
        if n.get("reason") in REPLYABLE_REASONS
        and n.get("uri") not in processed
        and n.get("author_handle") != OWN_HANDLE
    ]
    log.info("actionable mentions/replies: %d", len(actionable))

    replies_this_run = 0
    for n in actionable:
        if replies_this_run >= MAX_REPLIES_PER_RUN:
            log.info("reply cap reached this run — stopping mentions phase")
            break
        if replies_today >= MAX_REPLIES_PER_DAY:
            log.info("daily reply cap reached — stopping")
            break

        uri = n.get("uri")
        log.info(
            "  [%s] from @%s: %s",
            n.get("reason"), n.get("author_handle"),
            _first_line(n.get("text", "")),
        )

        try:
            draft = draft_mention_reply(n)
        except Exception as exc:
            log.error("draft_mention_reply failed: %s", exc)
            stats["failed"] += 1
            processed.add(uri)
            continue

        if not draft:
            log.info("SKIP — nothing to add")
            stats["skipped"] += 1
            processed.add(uri)
            continue

        if args.dry_run:
            log.info("DRY RUN — would reply: %s", draft)
            stats["posted"] += 1
            processed.add(uri)
            replies_this_run += 1
            continue

        result = client.reply(
            text=draft,
            parent_uri=uri,
            parent_cid=n.get("cid"),
            root_uri=n.get("reply_root_uri") or uri,
            root_cid=n.get("reply_root_cid") or n.get("cid"),
        )
        if not result:
            log.error("reply failed on %s", uri)
            stats["failed"] += 1
            continue

        log.info("replied: %s", result.get("uri"))
        processed.add(uri)
        replied.add(uri)
        replies_this_run += 1
        replies_today += 1
        stats["posted"] += 1

        if gh:
            _file_posted_audit(
                gh, phase="mention", notif_or_post=n,
                draft=draft, result=result,
            )

    ledger["processed_notif_uris"] = sorted(processed)
    ledger["replied_to_uris"] = sorted(replied)
    daily[today] = replies_today
    return stats


# ---------------------------------------------------------------------------
# Phase 2 — Search for AI-in-finance posts
# ---------------------------------------------------------------------------


def _process_search(
    client: BlueskyClient,
    gh: GitHubIssuer | None,
    ledger: dict,
    args: argparse.Namespace,
) -> dict:
    stats = {"posted": 0, "skipped": 0, "failed": 0, "classified": 0}

    replied = set(ledger.get("replied_to_uris", []))
    today = _today()
    daily = ledger.setdefault("daily_reply_count", {})
    replies_today = daily.get(today, 0)

    # Collect + dedupe candidates across all queries.
    seen: set[str] = set()
    candidates: list[dict] = []
    for q in SEARCH_QUERIES:
        posts = client.search_posts(q, limit=SEARCH_LIMIT_PER_QUERY)
        log.info("  query %r: %d results", q, len(posts))
        for p in posts:
            uri = p.get("uri")
            if not uri or uri in seen:
                continue
            if uri in replied:
                continue
            if p.get("author_handle") == OWN_HANDLE:
                continue
            seen.add(uri)
            p["_discovered_via"] = q
            candidates.append(p)

    log.info("search candidates (dedup): %d", len(candidates))

    replies_this_run = 0
    for post in candidates:
        if replies_this_run >= MAX_REPLIES_PER_RUN:
            log.info("reply cap reached this run — stopping search phase")
            break
        if replies_today >= MAX_REPLIES_PER_DAY:
            log.info("daily reply cap reached — stopping")
            break

        uri = post.get("uri")
        log.info(
            "  considering %s by @%s (via %r): %s",
            (uri or "")[-12:], post.get("author_handle"),
            post.get("_discovered_via"),
            _first_line(post.get("text", "")),
        )

        try:
            themes = classify_bsky_themes(post)
            stats["classified"] += 1
        except Exception as exc:
            log.error("classifier failed: %s", exc)
            continue

        if not themes:
            log.info("SKIP — off-theme")
            stats["skipped"] += 1
            continue
        log.info("matches themes %s", themes)

        try:
            draft = draft_reply_to_post(post)
        except Exception as exc:
            log.error("draft_reply_to_post failed: %s", exc)
            stats["failed"] += 1
            continue

        if not draft:
            log.info("SKIP — drafter returned nothing substantive")
            stats["skipped"] += 1
            continue

        if args.dry_run:
            log.info("DRY RUN — would reply: %s", draft)
            stats["posted"] += 1
            replied.add(uri)
            replies_this_run += 1
            continue

        result = client.reply(
            text=draft,
            parent_uri=uri,
            parent_cid=post.get("cid"),
            root_uri=post.get("root_uri") or uri,
            root_cid=post.get("root_cid") or post.get("cid"),
        )
        if not result:
            log.error("reply failed on %s", uri)
            stats["failed"] += 1
            continue

        log.info("replied: %s", result.get("uri"))
        replied.add(uri)
        replies_this_run += 1
        replies_today += 1
        stats["posted"] += 1

        if gh:
            _file_posted_audit(
                gh, phase="search", notif_or_post=post,
                draft=draft, result=result, themes=themes,
            )

    ledger["replied_to_uris"] = sorted(replied)
    daily[today] = replies_today
    return stats


# ---------------------------------------------------------------------------
# Audit issues
# ---------------------------------------------------------------------------


def _file_posted_audit(
    gh: GitHubIssuer,
    phase: str,
    notif_or_post: dict,
    draft: str,
    result: dict,
    themes: list[int] | None = None,
) -> None:
    author = notif_or_post.get("author_handle") or "unknown"
    parent_uri = notif_or_post.get("uri") or ""
    parent_text = _first_line(notif_or_post.get("text", ""), maxlen=60)
    reply_uri = result.get("uri") or ""

    title = f'[bluesky] {phase}: @{author} — "{parent_text}"'
    body_parts = [
        f"**Phase:** {phase}",
        f"**Author:** @{author}",
        f"**Parent URI:** `{parent_uri}`",
        f"**Our reply URI:** `{reply_uri}`",
    ]
    if themes:
        body_parts.append(f"**Themes:** {themes}")
    if notif_or_post.get("_discovered_via"):
        body_parts.append(
            f"**Discovered via query:** {notif_or_post['_discovered_via']!r}"
        )
    body_parts += [
        "",
        "### Parent post",
        "",
        (notif_or_post.get("text") or "").strip() or "(no text)",
        "",
        "### Our reply",
        "",
        draft,
    ]
    issue = gh.create_issue(title, "\n".join(body_parts), [POSTED_LABEL])
    if issue:
        gh.close_issue(issue["number"])


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-mentions", action="store_true")
    parser.add_argument("--no-search", action="store_true")
    args = parser.parse_args()

    log.info(
        "Bluesky heartbeat starting (dry_run=%s mentions=%s search=%s)",
        args.dry_run, not args.no_mentions, not args.no_search,
    )

    client = BlueskyClient()

    gh = None
    if not args.dry_run:
        try:
            gh = GitHubIssuer()
            gh.ensure_label(POSTED_LABEL, "0e8a16", "Bluesky post — audit")
            gh.ensure_label(FAILED_LABEL, "b60205", "Bluesky posting failed")
        except Exception as exc:
            log.warning("GitHub unavailable — running without audit: %s", exc)
            gh = None

    ledger_issue = None
    ledger: dict = {
        "replied_to_uris": [],
        "processed_notif_uris": [],
        "daily_reply_count": {},
    }
    if gh:
        ledger_issue, ledger = get_or_create_ledger(gh)

    mention_stats = {"posted": 0, "skipped": 0, "failed": 0}
    search_stats = {"posted": 0, "skipped": 0, "failed": 0, "classified": 0}

    if not args.no_mentions:
        mention_stats = _process_mentions(client, gh, ledger, args)
    if not args.no_search:
        search_stats = _process_search(client, gh, ledger, args)

    if gh and ledger_issue is not None:
        update_ledger(gh, ledger_issue, ledger)
        log.info("ledger saved")

    log.info(
        "HEARTBEAT_DONE — mentions: posted=%d skipped=%d failed=%d | "
        "search: posted=%d skipped=%d failed=%d classified=%d",
        mention_stats["posted"], mention_stats["skipped"], mention_stats["failed"],
        search_stats["posted"], search_stats["skipped"], search_stats["failed"],
        search_stats["classified"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
