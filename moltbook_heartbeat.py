"""Moltbook heartbeat — fetch notifications, draft replies, file GitHub issues.

Runs every 4 hours via ``.github/workflows/moltbook-heartbeat.yml``.

For each unread notification:
    1. Gather context (post + target comment + parent thread)
    2. Draft a reply with Claude Haiku (system prompt cached)
    3. Create a GitHub issue with the draft, metadata, and approval instructions

A human reviewer approves by adding the ``moltbook-approve`` label to the issue,
which triggers ``moltbook_approve.py`` via a second workflow.

Env vars:
    MOLTBOOK_API_KEY      Bearer token (required)
    ANTHROPIC_API_KEY     for drafting (required unless --no-draft)
    GITHUB_TOKEN          required unless --dry-run
    GITHUB_REPOSITORY     owner/repo — set automatically in GitHub Actions
"""

from __future__ import annotations

import argparse
import json
import logging

from moltbook_lib import (
    APPROVE_LABEL,
    GitHubIssuer,
    MOLTBOOK_ISSUE_LABEL,
    MoltbookClient,
    REJECT_LABEL,
    REPLY_MARKER_END,
    REPLY_MARKER_START,
    draft_reply,
    notification_marker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("moltbook-heartbeat")


def _first_line(s: str, maxlen: int = 120) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    line = s.splitlines()[0]
    return line[:maxlen] + ("…" if len(line) > maxlen else "")


def _build_context(client: MoltbookClient, notif: dict) -> dict | None:
    """Gather post + target comment + parent for drafting."""
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
        # No comment id or it's been deleted — fall back to minimal context
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


def _render_issue(ctx: dict, draft: str) -> tuple[str, str]:
    short_title = (ctx["post_title"] or "")[:60]
    title = f'[moltbook] reply: @{ctx["author_name"]} on "{short_title}"'
    post_url = f"https://www.moltbook.com/post/{ctx['post_id']}"
    meta = {
        "notif_id": ctx["notif_id"],
        "post_id": ctx["post_id"],
        "parent_id": ctx["comment_id"],
        "type": "reply_to_comment",
    }

    def quote(text: str) -> str:
        return "\n".join(f"> {line}" for line in (text or "").splitlines())

    body_parts = [
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
        quote(ctx["comment_content"]),
        "",
    ]
    if ctx.get("parent_content"):
        body_parts.extend(
            [
                "### In reply to (parent comment)",
                quote(ctx["parent_content"]),
                "",
            ]
        )
    body_parts.extend(
        [
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
            "- ✏️ **Edit first**: edit this issue body between the"
            " `REPLY_START` / `REPLY_END` markers, then add"
            f" `{APPROVE_LABEL}`",
            "",
            f"<!-- moltbook-meta: {json.dumps(meta)} -->",
        ]
    )
    return title, "\n".join(body_parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true", help="Draft but don't create issues"
    )
    parser.add_argument(
        "--no-draft", action="store_true", help="Skip Anthropic drafting"
    )
    parser.add_argument(
        "--max", type=int, default=10, help="Max notifications to process"
    )
    args = parser.parse_args()

    log.info("Moltbook heartbeat starting")
    client = MoltbookClient()

    home = client.home()
    if home:
        acct = home.get("your_account") or {}
        dms = (home.get("your_direct_messages") or {}).get("pending_request_count", "0")
        log.info(
            "account: karma=%s unread=%s dm_requests=%s",
            acct.get("karma", "?"),
            acct.get("unread_notification_count", "?"),
            dms,
        )

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

    # Only comment-type notifications need a drafted reply. Follows / upvotes /
    # announcements are logged above but not actioned.
    REPLYABLE = {"post_comment", "comment_reply", "mention"}
    actionable = [n for n in unread if n.get("type") in REPLYABLE]
    if not actionable:
        log.info("HEARTBEAT_OK — no replyable activity")
        return 0
    log.info("actionable: %d", len(actionable))

    gh: GitHubIssuer | None = None
    existing_markers: set[str] = set()
    if not args.dry_run:
        gh = GitHubIssuer()
        gh.ensure_label(
            MOLTBOOK_ISSUE_LABEL, "5319e7", "Moltbook reply awaiting approval"
        )
        gh.ensure_label(APPROVE_LABEL, "0e8a16", "Approve and post this draft")
        gh.ensure_label(REJECT_LABEL, "b60205", "Reject and close this draft")
        for issue in gh.list_moltbook_issues():
            body = issue.get("body") or ""
            for marker_line in body.splitlines():
                if "moltbook-notif:" in marker_line:
                    existing_markers.add(marker_line.strip())
        log.info("existing moltbook issues: %d", len(existing_markers))

    processed = 0
    for notif in actionable[: args.max]:
        marker = notification_marker(notif["id"])
        if marker in existing_markers:
            log.info("skip %s — issue already exists", notif["id"][:8])
            continue

        ctx = _build_context(client, notif)
        if ctx is None:
            log.warning(
                "skip %s — could not build context", notif["id"][:8]
            )
            continue

        if args.no_draft:
            draft = "(drafting skipped — write your reply here before approving)"
        else:
            try:
                draft = draft_reply(ctx)
                log.info(
                    "drafted for @%s (%d chars)", ctx["author_name"], len(draft)
                )
            except Exception as exc:
                log.error("drafting failed for %s: %s", notif["id"][:8], exc)
                continue

        if gh is None:
            log.info("DRY RUN — would create issue for notif %s", notif["id"][:8])
            log.info("DRAFT:\n%s", draft)
            continue

        title, body = _render_issue(ctx, draft)
        issue = gh.create_issue(title, body, [MOLTBOOK_ISSUE_LABEL])
        if issue:
            log.info(
                "created issue #%s for notif %s",
                issue.get("number"),
                notif["id"][:8],
            )
            processed += 1
        else:
            log.error("failed to create issue for notif %s", notif["id"][:8])

    log.info("HEARTBEAT_DONE — processed=%d", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
