"""Bluesky heartbeat — reply to mentions + search for AI-in-finance posts.

Runs every 4 hours via ``.github/workflows/bluesky-heartbeat.yml``.

Phase 1 — Mentions (always on)
    Reply to unread mention/reply notifications. No approval gate; post goes
    straight up and an audit issue (``bluesky-posted``) is filed.

Phase 2 — Search engagement (``--no-search`` to disable)
    For each seeded search query (AI stock picking, AI fund manager, etc.),
    fetch recent matching posts, run the three-theme classifier, and reply
    to the best matches until we hit the per-run cap.

Phase 3 — Equity targeting (``--no-equity`` to disable)
    Pull the top swarm-consensus tickers from the latest
    ``consensus_snapshots`` row, search Bluesky for posts about each
    (cashtag + bare ticker), classify for genuine equity discussion, and
    reply with a casual "the AlphaMolt agents agree on $TICKER too"
    note plus a clickable link to the dated /consensus/<date> permalink.

Auto-posts everything. Dedup + rate limits tracked in a GitHub issue
labelled ``bluesky-ledger``.

Env vars:
    BLUESKY_HANDLE         default alphamolt.bsky.social
    BLUESKY_APP_PASSWORD   required — Bluesky app-specific password
    ANTHROPIC_API_KEY      required — drafter + classifier
    SUPABASE_URL           required for phase 3 (consensus tickers)
    SUPABASE_SERVICE_KEY   required for phase 3
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
    EQUITY_SEARCH_LIMIT_PER_QUERY,
    EQUITY_TARGET_TOP_N,
    FAILED_LABEL,
    OWN_HANDLE,
    POSTED_LABEL,
    SEARCH_QUERIES,
    classify_bsky_themes,
    classify_ticker_post,
    consensus_share_url,
    draft_equity_reply,
    draft_mention_reply,
    draft_reply_to_post,
    equity_search_queries,
    fetch_consensus_targets,
    get_or_create_ledger,
    update_ledger,
)
from social_personality import (
    detect_hostility,
    generate_apology,
    get_relationship,
    is_silenced,
    maybe_refresh_summary,
    record_engagement,
    record_hostility,
    relationship_block,
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
        author = n.get("author_handle") or ""
        log.info(
            "  [%s] from @%s: %s",
            n.get("reason"), author,
            _first_line(n.get("text", "")),
        )

        # Silence gate
        if is_silenced(ledger, author):
            rel = get_relationship(ledger, author) or {}
            log.info("SILENCED — skip @%s (status=%s)",
                     author, rel.get("status", "?"))
            stats["skipped"] += 1
            processed.add(uri)
            continue

        # Hostility gate
        try:
            hostility = detect_hostility(n.get("text", ""))
        except Exception as exc:
            log.warning("hostility detection failed: %s", exc)
            hostility = {"hostile": False, "severity": "none", "reason": ""}

        if hostility["hostile"]:
            severity = hostility["severity"]
            log.info("HOSTILITY %s on @%s: %s",
                     severity.upper(), author, hostility["reason"])
            if severity == "mild":
                record_hostility(
                    ledger, author,
                    excerpt=n.get("text", ""),
                    ref=uri or "",
                    severity=severity,
                    apologized=False,
                )
                log.info("muted @%s — no apology sent", author)
                stats["skipped"] += 1
                processed.add(uri)
                continue

            # severity == "strong" → apologize once
            rel = get_relationship(ledger, author) or {}
            what_we_said = ""
            threads = rel.get("recent_threads") or []
            if threads:
                what_we_said = threads[-1].get("our_excerpt", "")
            try:
                apology = generate_apology(
                    author,
                    what_we_said=what_we_said,
                    their_response=n.get("text", ""),
                    platform="Bluesky",
                    char_cap=240,
                )
            except Exception as exc:
                log.error("apology generation failed: %s", exc)
                apology = ""

            if not apology:
                record_hostility(
                    ledger, author,
                    excerpt=n.get("text", ""),
                    ref=uri or "",
                    severity=severity,
                    apologized=False,
                )
                stats["skipped"] += 1
                processed.add(uri)
                continue

            if args.dry_run:
                log.info("DRY RUN — would apologize to @%s: %s",
                         author, apology)
                record_hostility(
                    ledger, author,
                    excerpt=n.get("text", ""),
                    ref=uri or "",
                    severity=severity,
                    apologized=True,
                )
                processed.add(uri)
                continue

            result = client.reply(
                text=apology,
                parent_uri=uri,
                parent_cid=n.get("cid"),
                root_uri=n.get("reply_root_uri") or uri,
                root_cid=n.get("reply_root_cid") or n.get("cid"),
            )
            record_hostility(
                ledger, author,
                excerpt=n.get("text", ""),
                ref=uri or "",
                severity=severity,
                apologized=bool(result),
            )
            if result:
                log.info("apology posted: %s", result.get("uri"))
                processed.add(uri)
                replied.add(uri)
                replies_this_run += 1
                replies_today += 1
                stats["posted"] += 1
                if gh:
                    _file_posted_audit(
                        gh, phase="apology", notif_or_post=n,
                        draft=apology, result=result,
                    )
            else:
                log.error("apology reply failed on %s", uri)
                stats["failed"] += 1
            continue

        # Normal draft path with memory injection
        memory = relationship_block(ledger, author)
        if memory:
            log.info("injecting memory for @%s", author)

        try:
            draft = draft_mention_reply(n, memory_block=memory)
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

        # Relationship bookkeeping + cold-start summary
        was_new = get_relationship(ledger, author) is None
        record_engagement(
            ledger, author,
            ref=uri or "",
            their_excerpt=n.get("text", ""),
            our_excerpt=draft,
        )
        try:
            samples = client.get_author_recent_texts(author, limit=5) \
                if was_new else [n.get("text", "")]
            maybe_refresh_summary(
                ledger, author, samples,
                platform="Bluesky", force=was_new,
            )
        except Exception as exc:
            log.warning("summary update failed for @%s: %s", author, exc)

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
        author = post.get("author_handle") or ""
        log.info(
            "  considering %s by @%s (via %r): %s",
            (uri or "")[-12:], author,
            post.get("_discovered_via"),
            _first_line(post.get("text", "")),
        )

        # Silence gate — never engage with anyone we've apologized to / muted
        if is_silenced(ledger, author):
            rel = get_relationship(ledger, author) or {}
            log.info("SILENCED — skip @%s (status=%s)",
                     author, rel.get("status", "?"))
            stats["skipped"] += 1
            continue

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

        memory = relationship_block(ledger, author)
        if memory:
            log.info("injecting memory for @%s", author)
        try:
            draft = draft_reply_to_post(post, memory_block=memory)
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

        # Relationship bookkeeping + cold-start summary
        was_new = get_relationship(ledger, author) is None
        record_engagement(
            ledger, author,
            ref=uri or "",
            their_excerpt=post.get("text", ""),
            our_excerpt=draft,
        )
        try:
            samples = client.get_author_recent_texts(author, limit=5) \
                if was_new else [post.get("text", "")]
            maybe_refresh_summary(
                ledger, author, samples,
                platform="Bluesky", force=was_new,
            )
        except Exception as exc:
            log.warning("summary update failed for @%s: %s", author, exc)

        if gh:
            _file_posted_audit(
                gh, phase="search", notif_or_post=post,
                draft=draft, result=result, themes=themes,
            )

    ledger["replied_to_uris"] = sorted(replied)
    daily[today] = replies_today
    return stats


# ---------------------------------------------------------------------------
# Phase 3 — Equity targeting (top swarm-consensus tickers)
# ---------------------------------------------------------------------------


def _process_equity_targeting(
    client: BlueskyClient,
    gh: GitHubIssuer | None,
    ledger: dict,
    args: argparse.Namespace,
) -> dict:
    stats = {
        "posted": 0, "skipped": 0, "failed": 0,
        "classified": 0, "tickers": 0,
    }

    try:
        targets, snapshot_date = fetch_consensus_targets(
            limit=EQUITY_TARGET_TOP_N
        )
    except Exception as exc:
        log.error("consensus fetch failed — skipping equity phase: %s", exc)
        return stats

    if not targets:
        log.info("equity targeting: no consensus snapshot available — skipping")
        return stats

    share_url = consensus_share_url(snapshot_date)
    log.info(
        "equity targeting: %d tickers from snapshot %s — share_url=%s",
        len(targets), snapshot_date, share_url,
    )
    stats["tickers"] = len(targets)

    replied = set(ledger.get("replied_to_uris", []))
    today = _today()
    daily = ledger.setdefault("daily_reply_count", {})
    replies_today = daily.get(today, 0)

    replies_this_run = 0
    for target in targets:
        if replies_this_run >= MAX_REPLIES_PER_RUN:
            log.info("reply cap reached this run — stopping equity phase")
            break
        if replies_today >= MAX_REPLIES_PER_DAY:
            log.info("daily reply cap reached — stopping")
            break

        ticker = target["ticker"]
        company_name = target.get("company_name") or ticker
        log.info(
            "equity target: %s (%s) — %d/%d agents (%.1f%%)",
            ticker, company_name,
            target.get("num_agents") or 0,
            target.get("total_agents") or 0,
            float(target.get("pct_agents") or 0),
        )

        seen: set[str] = set()
        candidates: list[dict] = []
        for q in equity_search_queries(ticker):
            posts = client.search_posts(
                q, limit=EQUITY_SEARCH_LIMIT_PER_QUERY
            )
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
                p["_target_ticker"] = ticker
                candidates.append(p)

        log.info("  %s candidates (dedup): %d", ticker, len(candidates))

        for post in candidates:
            if replies_this_run >= MAX_REPLIES_PER_RUN:
                break
            if replies_today >= MAX_REPLIES_PER_DAY:
                break

            uri = post.get("uri")
            author = post.get("author_handle") or ""
            log.info(
                "    considering %s by @%s: %s",
                (uri or "")[-12:], author,
                _first_line(post.get("text", "")),
            )

            if is_silenced(ledger, author):
                rel = get_relationship(ledger, author) or {}
                log.info("    SILENCED — skip @%s (status=%s)",
                         author, rel.get("status", "?"))
                stats["skipped"] += 1
                continue

            try:
                relevant = classify_ticker_post(post, ticker, company_name)
                stats["classified"] += 1
            except Exception as exc:
                log.error("    ticker classifier failed: %s", exc)
                continue

            if not relevant:
                log.info("    SKIP — not genuinely about %s", ticker)
                stats["skipped"] += 1
                continue

            memory = relationship_block(ledger, author)
            if memory:
                log.info("    injecting memory for @%s", author)
            try:
                draft = draft_equity_reply(
                    post,
                    ticker=ticker,
                    company_name=company_name,
                    num_agents=int(target.get("num_agents") or 0),
                    total_agents=int(target.get("total_agents") or 0),
                    pct_agents=target.get("pct_agents"),
                    memory_block=memory,
                )
            except Exception as exc:
                log.error("    draft_equity_reply failed: %s", exc)
                stats["failed"] += 1
                continue

            if not draft:
                log.info("    SKIP — drafter returned nothing substantive")
                stats["skipped"] += 1
                continue

            if args.dry_run:
                log.info(
                    "    DRY RUN — would reply: %s [+ link to %s]",
                    draft, share_url,
                )
                stats["posted"] += 1
                replied.add(uri)
                replies_this_run += 1
                # Only one reply per ticker per run — move on.
                break

            result = client.reply(
                text=draft,
                parent_uri=uri,
                parent_cid=post.get("cid"),
                root_uri=post.get("root_uri") or uri,
                root_cid=post.get("root_cid") or post.get("cid"),
                link_url=share_url,
                link_label="View the AlphaMolt swarm consensus",
            )
            if not result:
                log.error("    reply failed on %s", uri)
                stats["failed"] += 1
                continue

            log.info("    replied: %s", result.get("uri"))
            replied.add(uri)
            replies_this_run += 1
            replies_today += 1
            stats["posted"] += 1

            was_new = get_relationship(ledger, author) is None
            record_engagement(
                ledger, author,
                ref=uri or "",
                their_excerpt=post.get("text", ""),
                our_excerpt=draft,
            )
            try:
                samples = client.get_author_recent_texts(author, limit=5) \
                    if was_new else [post.get("text", "")]
                maybe_refresh_summary(
                    ledger, author, samples,
                    platform="Bluesky", force=was_new,
                )
            except Exception as exc:
                log.warning("    summary update failed for @%s: %s", author, exc)

            if gh:
                _file_posted_audit(
                    gh, phase="equity", notif_or_post=post,
                    draft=draft, result=result, ticker=ticker,
                    share_url=share_url,
                )
            # One reply per ticker per run keeps it from reading like a
            # rapid-fire bot run on the same symbol.
            break

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
    ticker: str | None = None,
    share_url: str | None = None,
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
    if ticker:
        body_parts.append(f"**Target ticker:** {ticker}")
    if share_url:
        body_parts.append(f"**Appended link:** {share_url}")
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
    parser.add_argument("--no-equity", action="store_true")
    args = parser.parse_args()

    log.info(
        "Bluesky heartbeat starting (dry_run=%s mentions=%s search=%s equity=%s)",
        args.dry_run, not args.no_mentions, not args.no_search,
        not args.no_equity,
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
    equity_stats = {
        "posted": 0, "skipped": 0, "failed": 0,
        "classified": 0, "tickers": 0,
    }

    if not args.no_mentions:
        mention_stats = _process_mentions(client, gh, ledger, args)
    if not args.no_search:
        search_stats = _process_search(client, gh, ledger, args)
    if not args.no_equity:
        equity_stats = _process_equity_targeting(client, gh, ledger, args)

    if gh and ledger_issue is not None:
        update_ledger(gh, ledger_issue, ledger)
        log.info("ledger saved")

    log.info(
        "HEARTBEAT_DONE — mentions: posted=%d skipped=%d failed=%d | "
        "search: posted=%d skipped=%d failed=%d classified=%d | "
        "equity: posted=%d skipped=%d failed=%d classified=%d tickers=%d",
        mention_stats["posted"], mention_stats["skipped"], mention_stats["failed"],
        search_stats["posted"], search_stats["skipped"], search_stats["failed"],
        search_stats["classified"],
        equity_stats["posted"], equity_stats["skipped"], equity_stats["failed"],
        equity_stats["classified"], equity_stats["tickers"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
