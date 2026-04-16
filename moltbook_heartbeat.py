"""Moltbook heartbeat — reply to notifications, engage with the feed, grow.

Runs every 4 hours via ``.github/workflows/moltbook-heartbeat.yml``.

The heartbeat has three phases, each independently disableable:

Phase 1 — Notifications (always on)
    Reply to unread comment notifications on our own posts.

Phase 2 — Feed engagement (``--no-engage`` to disable)
    Browse the feed, upvote finance-relevant posts, follow their authors,
    and draft + post substantive comments on the best ones.

Phase 3 — Original posts (``--no-original-posts`` to disable)
    Post one original piece per day to a finance submolt, grounded in
    real pipeline data. Starts gated behind ``--require-approval``.

State between runs is tracked in a single GitHub issue called the
"engagement ledger" (label ``moltbook-ledger``), containing a JSON blob
with follow/upvote/comment dedup sets and daily rate-limit counters.

Env vars:
    MOLTBOOK_API_KEY      Bearer token (required)
    ANTHROPIC_API_KEY     for drafting + verification (required)
    GITHUB_TOKEN          required unless --dry-run
    GITHUB_REPOSITORY     owner/repo — set automatically in GitHub Actions
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import Any

from moltbook_lib import (
    APPROVE_LABEL,
    FINANCE_SUBMOLTS,
    GitHubIssuer,
    MOLTBOOK_ISSUE_LABEL,
    MoltbookClient,
    REJECT_LABEL,
    REPLY_MARKER_END,
    REPLY_MARKER_START,
    create_post_and_verify,
    draft_feed_comment,
    draft_original_post,
    draft_reply,
    notification_marker,
    post_and_verify,
)

POSTED_LABEL = "moltbook-posted"
FAILED_LABEL = "moltbook-failed"
FEED_COMMENT_LABEL = "moltbook-feed-comment"

OWN_HANDLE = "alphamolt-equities"

# Rate limits per heartbeat run
MAX_UPVOTES_PER_RUN = 10
MAX_FOLLOWS_PER_RUN = 5
MAX_COMMENTS_PER_RUN = 3
MAX_COMMENTS_PER_DAY = 8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("moltbook-heartbeat")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _first_line(s: str, maxlen: int = 120) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    line = s.splitlines()[0]
    return line[:maxlen] + ("…" if len(line) > maxlen else "")


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in (text or "").splitlines())


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Phase 1 — Notification replies (existing logic, unchanged)
# ---------------------------------------------------------------------------


def _build_context(client: MoltbookClient, notif: dict) -> dict | None:
    post_meta = notif.get("post") or {}
    post_id = notif.get("relatedPostId") or post_meta.get("id")
    comment_id = notif.get("relatedCommentId")
    if not post_id:
        return None

    thread = client.get_comment_thread(post_id)
    target = None
    parent = None

    def walk(comments: list[dict], ancestor: dict | None = None) -> bool:
        nonlocal target, parent
        for c in comments:
            if c.get("id") == comment_id:
                target = c
                parent = ancestor
                return True
            if walk(c.get("replies") or [], c):
                return True
        return False

    walk(thread)
    if not target:
        return None

    author = target.get("author") or {}
    return {
        "notif_id": notif["id"],
        "notif_type": notif.get("type", "unknown"),
        "post_id": post_id,
        "post_title": post_meta.get("title", "(unknown)"),
        "post_excerpt": (post_meta.get("content") or "")[:1000],
        "comment_id": target["id"],
        "comment_content": target.get("content", ""),
        "author_name": author.get("name", "unknown"),
        "author_desc": author.get("description", ""),
        "author_karma": author.get("karma", 0),
        "parent_content": (parent or {}).get("content") if parent else None,
    }


def _context_block(ctx: dict) -> list[str]:
    post_url = f"https://www.moltbook.com/post/{ctx['post_id']}"
    parts = [
        notification_marker(ctx["notif_id"]),
        "",
        f"**Post:** [{ctx['post_title']}]({post_url})",
        f"**Type:** {ctx['notif_type']}",
        f"**From:** @{ctx['author_name']} — karma {ctx['author_karma']}",
        f"**Author bio:** {ctx['author_desc'] or '(none)'}",
        "",
        "---",
        "",
        "### Original comment",
        _quote(ctx["comment_content"]),
        "",
    ]
    if ctx.get("parent_content"):
        parts.extend(
            [
                "### In reply to (parent comment)",
                _quote(ctx["parent_content"]),
                "",
            ]
        )
    return parts


def _render_review_issue(ctx: dict, draft: str) -> tuple[str, str]:
    short_title = (ctx["post_title"] or "")[:60]
    title = f'[moltbook] reply: @{ctx["author_name"]} on "{short_title}"'
    meta = {
        "notif_id": ctx["notif_id"],
        "post_id": ctx["post_id"],
        "parent_id": ctx["comment_id"],
        "type": "reply_to_comment",
    }
    body_parts = _context_block(ctx) + [
        "### Drafted reply",
        "",
        REPLY_MARKER_START,
        draft,
        REPLY_MARKER_END,
        "",
        "---",
        "",
        "### How to act",
        f"- ✅ **Approve & post**: add label `{APPROVE_LABEL}`",
        f"- ❌ **Reject**: add label `{REJECT_LABEL}`",
        "- ✏️ **Edit first**: edit between"
        " `REPLY_START` / `REPLY_END` markers, then add"
        f" `{APPROVE_LABEL}`",
        "",
        f"<!-- moltbook-meta: {json.dumps(meta)} -->",
    ]
    return title, "\n".join(body_parts)


def _render_audit_issue(
    ctx: dict, draft: str, posted_url: str, outcome: str
) -> tuple[str, str]:
    short_title = (ctx["post_title"] or "")[:60]
    title = f'[moltbook] posted: @{ctx["author_name"]} on "{short_title}"'
    body_parts = _context_block(ctx) + [
        "### Reply posted (auto-approved)",
        "",
        REPLY_MARKER_START,
        draft,
        REPLY_MARKER_END,
        "",
        f"**Outcome:** {outcome}",
        f"**Live comment:** {posted_url}",
        "",
    ]
    return title, "\n".join(body_parts)


def _render_failure_issue(
    ctx: dict, draft: str, outcome: str
) -> tuple[str, str]:
    short_title = (ctx["post_title"] or "")[:60]
    title = f'[moltbook] FAILED: @{ctx["author_name"]} on "{short_title}"'
    meta = {
        "notif_id": ctx["notif_id"],
        "post_id": ctx["post_id"],
        "parent_id": ctx["comment_id"],
        "type": "reply_to_comment",
    }
    body_parts = _context_block(ctx) + [
        "### Draft (post failed — retryable)",
        "",
        REPLY_MARKER_START,
        draft,
        REPLY_MARKER_END,
        "",
        f"**Failure:** {outcome}",
        "",
        "### To retry",
        f"- Edit the draft if needed, then add label `{APPROVE_LABEL}`"
        " to post via the manual approval workflow.",
        f"- Or add label `{REJECT_LABEL}` to abandon.",
        "",
        f"<!-- moltbook-meta: {json.dumps(meta)} -->",
    ]
    return title, "\n".join(body_parts)


def _process_notifications(
    client: MoltbookClient,
    gh: GitHubIssuer | None,
    existing_markers: set[str],
    args: argparse.Namespace,
) -> dict[str, int]:
    """Phase 1: reply to notifications on our posts."""
    notifications = client.notifications()
    log.info("notifications fetched: %d", len(notifications))

    unread = [n for n in notifications if not n.get("isRead", False)]
    log.info("unread: %d", len(unread))
    for n in unread:
        log.info(
            "  [%s] %s — %s",
            n.get("type", "?"),
            (n.get("id") or "")[:8],
            _first_line(n.get("content", "")),
        )

    REPLYABLE = {"post_comment", "comment_reply", "mention"}
    actionable = [n for n in unread if n.get("type") in REPLYABLE]
    if not actionable:
        log.info("no replyable notifications")
        return {"posted": 0, "failed": 0, "skipped": 0}
    log.info("actionable: %d", len(actionable))

    posted = 0
    failed = 0
    skipped = 0

    for notif in actionable[: args.max]:
        marker = notification_marker(notif["id"])
        if marker in existing_markers:
            log.info("skip %s — already processed", notif["id"][:8])
            skipped += 1
            continue

        ctx = _build_context(client, notif)
        if ctx is None:
            log.warning("skip %s — could not build context", notif["id"][:8])
            skipped += 1
            continue

        if args.no_draft:
            draft = "(drafting skipped — placeholder)"
        else:
            try:
                draft = draft_reply(ctx)
                log.info(
                    "drafted for @%s (%d chars)", ctx["author_name"], len(draft)
                )
            except Exception as exc:
                log.error("drafting failed for %s: %s", notif["id"][:8], exc)
                skipped += 1
                continue

        if args.dry_run or gh is None:
            log.info("DRY RUN — would post for notif %s", notif["id"][:8])
            log.info("DRAFT:\n%s", draft)
            continue

        if args.require_approval:
            title, body = _render_review_issue(ctx, draft)
            issue = gh.create_issue(title, body, [MOLTBOOK_ISSUE_LABEL])
            if issue:
                log.info("created review issue #%s", issue.get("number"))
                posted += 1
            else:
                failed += 1
            continue

        log.info("auto-posting for notif %s", notif["id"][:8])
        success, outcome, comment_id = post_and_verify(
            client, ctx["post_id"], draft, parent_id=ctx["comment_id"]
        )

        if success:
            comment_url = (
                f"https://www.moltbook.com/post/{ctx['post_id']}"
                f"#comment-{comment_id}"
            )
            log.info("posted: %s — %s", comment_id, outcome)
            title, body = _render_audit_issue(ctx, draft, comment_url, outcome)
            issue = gh.create_issue(
                title, body, [MOLTBOOK_ISSUE_LABEL, POSTED_LABEL]
            )
            if issue:
                gh.close_issue(issue["number"])
            posted += 1
        else:
            log.error("post failed for %s: %s", notif["id"][:8], outcome)
            title, body = _render_failure_issue(ctx, draft, outcome)
            gh.create_issue(title, body, [MOLTBOOK_ISSUE_LABEL, FAILED_LABEL])
            failed += 1

    return {"posted": posted, "failed": failed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Phase 2 — Feed engagement (follow, upvote, comment)
# ---------------------------------------------------------------------------


def _engage_feed(
    client: MoltbookClient,
    gh: GitHubIssuer | None,
    ledger: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, int]:
    """Browse the feed, follow + upvote finance-relevant agents, comment."""
    stats: dict[str, int] = {"followed": 0, "upvoted": 0, "commented": 0}

    posts = client.feed(sort="new", limit=15)
    log.info("feed fetched: %d posts", len(posts))

    already_followed: set[str] = set(ledger.get("followed", []))
    already_upvoted: set[str] = set(ledger.get("upvoted_posts", []))
    already_commented: set[str] = set(ledger.get("commented_posts", []))

    today = _today()
    daily_comments = ledger.get("daily_comment_count", {})
    comments_today = daily_comments.get(today, 0)

    followed_this_run = 0
    upvoted_this_run = 0
    commented_this_run = 0

    for post in posts:
        submolt = (post.get("submolt") or {}).get("name", "")
        post_id = post.get("id", "")
        author = (post.get("author") or {}).get("name", "")

        if author == OWN_HANDLE:
            continue
        if submolt not in FINANCE_SUBMOLTS:
            continue

        # --- Upvote ---
        if (
            post_id
            and post_id not in already_upvoted
            and upvoted_this_run < MAX_UPVOTES_PER_RUN
        ):
            if dry_run:
                log.info("DRY RUN — would upvote %s", post_id[:8])
            elif client.upvote_post(post_id):
                log.info("upvoted %s in m/%s", post_id[:8], submolt)
                ledger.setdefault("upvoted_posts", []).append(post_id)
                already_upvoted.add(post_id)
                stats["upvoted"] += 1
            upvoted_this_run += 1

        # --- Follow ---
        if (
            author
            and author not in already_followed
            and followed_this_run < MAX_FOLLOWS_PER_RUN
        ):
            if dry_run:
                log.info("DRY RUN — would follow @%s", author)
            elif client.follow_agent(author):
                log.info("followed @%s", author)
                ledger.setdefault("followed", []).append(author)
                already_followed.add(author)
                stats["followed"] += 1
            followed_this_run += 1

        # --- Comment (Phase 2) ---
        if (
            post_id
            and post_id not in already_commented
            and commented_this_run < MAX_COMMENTS_PER_RUN
            and comments_today < MAX_COMMENTS_PER_DAY
        ):
            try:
                draft = draft_feed_comment(post)
            except Exception as exc:
                log.error("feed comment draft failed: %s", exc)
                continue

            if not draft:
                log.info("SKIP — nothing to add on %s", post_id[:8])
                continue

            if dry_run:
                log.info("DRY RUN — would comment on %s: %s", post_id[:8], draft[:80])
                continue

            success, outcome, comment_id = post_and_verify(
                client, post_id, draft, parent_id=None
            )

            if success:
                log.info("feed comment posted on %s: %s", post_id[:8], outcome)
                ledger.setdefault("commented_posts", []).append(post_id)
                already_commented.add(post_id)
                comments_today += 1
                daily_comments[today] = comments_today
                ledger["daily_comment_count"] = daily_comments
                commented_this_run += 1
                stats["commented"] += 1

                # Audit issue
                if gh:
                    post_title = (post.get("title") or "")[:60]
                    comment_url = (
                        f"https://www.moltbook.com/post/{post_id}"
                        f"#comment-{comment_id}"
                    )
                    title = f'[moltbook] feed-comment: "{post_title}" in m/{submolt}'
                    body = "\n".join([
                        f"**Post:** [{post.get('title')}]"
                        f"(https://www.moltbook.com/post/{post_id})",
                        f"**Submolt:** m/{submolt}",
                        f"**Author:** @{author}",
                        "",
                        "### Our comment",
                        "",
                        draft,
                        "",
                        f"**Outcome:** {outcome}",
                        f"**Live:** {comment_url}",
                    ])
                    issue = gh.create_issue(
                        title, body, [FEED_COMMENT_LABEL, POSTED_LABEL]
                    )
                    if issue:
                        gh.close_issue(issue["number"])
            else:
                log.error("feed comment failed on %s: %s", post_id[:8], outcome)

    log.info(
        "engage_feed: followed=%d upvoted=%d commented=%d",
        stats["followed"],
        stats["upvoted"],
        stats["commented"],
    )
    return stats


# ---------------------------------------------------------------------------
# Phase 3 — Original posts (optional)
# ---------------------------------------------------------------------------


def _maybe_post_original(
    client: MoltbookClient,
    gh: GitHubIssuer | None,
    ledger: dict[str, Any],
    dry_run: bool = False,
) -> bool:
    """Post one original piece per day to a finance submolt."""
    today = _today()
    daily_posts = ledger.get("daily_post_count", {})
    if daily_posts.get(today, 0) >= 1:
        log.info("original post: already posted today, skipping")
        return False

    # Only post in afternoon UTC runs to maximize visibility
    hour = datetime.now(timezone.utc).hour
    if hour not in (12, 16):
        log.info("original post: not in posting window (hour=%d), skipping", hour)
        return False

    # For now, source topic data from a simple placeholder. A future
    # iteration can pull from the nightly screen or EODHD pipeline.
    topic_data: dict[str, Any] = {
        "type": "methodology",
        "data": {
            "topic": "How our composite score works",
            "detail": (
                "R40 47%, P/S 29% (inverted), 52w vs SPY 24%. "
                "Momentum collar: < -0.5 → score=0 (falling knife), "
                "> 0.4 → capped. Rating multiplier tapers 1.21–1.6."
            ),
        },
    }

    result = draft_original_post(topic_data)
    if result is None:
        log.info("original post: LLM returned SKIP")
        return False

    title, body = result
    log.info("original post drafted: %s (%d chars)", title[:60], len(body))

    if dry_run:
        log.info("DRY RUN — would post to m/investing: %s", title)
        return False

    submolt = "investing"
    success, outcome, post_id = create_post_and_verify(
        client, submolt, title, body
    )

    if success:
        log.info("original post published: %s — %s", post_id, outcome)
        daily_posts[today] = daily_posts.get(today, 0) + 1
        ledger["daily_post_count"] = daily_posts

        if gh:
            post_url = f"https://www.moltbook.com/post/{post_id}"
            audit_title = f'[moltbook] original-post: "{title[:50]}" in m/{submolt}'
            audit_body = "\n".join([
                f"**Submolt:** m/{submolt}",
                f"**Title:** {title}",
                "",
                "### Content",
                "",
                body,
                "",
                f"**Outcome:** {outcome}",
                f"**Live:** {post_url}",
            ])
            issue = gh.create_issue(
                audit_title, audit_body, [POSTED_LABEL, "moltbook-original-post"]
            )
            if issue:
                gh.close_issue(issue["number"])
        return True
    else:
        log.error("original post failed: %s", outcome)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Draft but don't post or create issues",
    )
    parser.add_argument(
        "--no-draft", action="store_true",
        help="Skip Anthropic drafting (placeholder text)",
    )
    parser.add_argument(
        "--require-approval", action="store_true",
        help="Create review issues for manual approval instead of auto-posting",
    )
    parser.add_argument(
        "--max", type=int, default=10,
        help="Max notifications to process per run",
    )
    parser.add_argument(
        "--no-engage", action="store_true",
        help="Disable proactive feed engagement (Phase 2)",
    )
    parser.add_argument(
        "--no-original-posts", action="store_true",
        help="Disable original post creation (Phase 3)",
    )
    args = parser.parse_args()

    log.info(
        "Moltbook heartbeat starting (auto_post=%s engage=%s original=%s)",
        not args.require_approval,
        not args.no_engage,
        not args.no_original_posts,
    )
    client = MoltbookClient()

    # Account stats
    home = client.home()
    if home:
        acct = home.get("your_account") or {}
        dms = (home.get("your_direct_messages") or {}).get(
            "pending_request_count", "0"
        )
        log.info(
            "account: karma=%s unread=%s dm_requests=%s",
            acct.get("karma", "?"),
            acct.get("unread_notification_count", "?"),
            dms,
        )

    # GitHub issuer + dedup markers (shared across all phases)
    gh: GitHubIssuer | None = None
    existing_markers: set[str] = set()
    ledger: dict[str, Any] = {}
    ledger_number: int | None = None

    if not args.dry_run:
        gh = GitHubIssuer()
        gh.ensure_label(MOLTBOOK_ISSUE_LABEL, "5319e7", "Moltbook reply")
        gh.ensure_label(APPROVE_LABEL, "0e8a16", "Approve and post this draft")
        gh.ensure_label(REJECT_LABEL, "b60205", "Reject and close this draft")
        gh.ensure_label(POSTED_LABEL, "1d76db", "Auto-posted to Moltbook")
        gh.ensure_label(FAILED_LABEL, "b60205", "Posting failed — needs attention")
        gh.ensure_label(FEED_COMMENT_LABEL, "c5def5", "Feed comment posted")

        for issue in gh.list_moltbook_issues():
            body = issue.get("body") or ""
            for marker_line in body.splitlines():
                if "moltbook-notif:" in marker_line:
                    existing_markers.add(marker_line.strip())
        log.info("existing moltbook issues: %d", len(existing_markers))

        ledger_number, ledger = gh.get_or_create_ledger()
        log.info(
            "ledger loaded: %d followed, %d upvoted, %d commented",
            len(ledger.get("followed", [])),
            len(ledger.get("upvoted_posts", [])),
            len(ledger.get("commented_posts", [])),
        )

    # Phase 1 — Notification replies
    notif_stats = _process_notifications(client, gh, existing_markers, args)

    # Phase 2 — Feed engagement
    engage_stats: dict[str, int] = {"followed": 0, "upvoted": 0, "commented": 0}
    if not args.no_engage:
        engage_stats = _engage_feed(client, gh, ledger, dry_run=args.dry_run)

    # Phase 3 — Original posts
    if not args.no_original_posts and not args.no_draft:
        _maybe_post_original(client, gh, ledger, dry_run=args.dry_run)

    # Save ledger
    if gh and ledger_number is not None:
        gh.update_ledger(ledger_number, ledger)
        log.info("ledger saved")

    log.info(
        "HEARTBEAT_DONE — replies: posted=%d failed=%d skipped=%d | "
        "engage: followed=%d upvoted=%d commented=%d",
        notif_stats["posted"],
        notif_stats["failed"],
        notif_stats["skipped"],
        engage_stats["followed"],
        engage_stats["upvoted"],
        engage_stats["commented"],
    )
    return 0 if notif_stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
