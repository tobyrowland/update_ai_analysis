"""Moltbook approval handler.

Invoked by ``.github/workflows/moltbook-approve.yml`` on ``issues: labeled``.
Parses the issue body, posts the reply to Moltbook, solves the post-comment
verification challenge with Claude Haiku, then comments on the issue with the
result and closes it.

Env vars:
    MOLTBOOK_API_KEY    (required)
    ANTHROPIC_API_KEY   (required — math challenge solver)
    GITHUB_TOKEN        (required)
    GITHUB_EVENT_PATH   (set by GitHub Actions)
    GITHUB_REPOSITORY   (set by GitHub Actions)
"""

from __future__ import annotations

import json
import logging
import os

from moltbook_lib import (
    APPROVE_LABEL,
    GitHubIssuer,
    MoltbookClient,
    REJECT_LABEL,
    extract_meta,
    extract_reply,
    post_and_verify,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("moltbook-approve")


def _load_event() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise RuntimeError("GITHUB_EVENT_PATH not set")
    with open(path) as f:
        return json.load(f)


def _handle_reject(gh: GitHubIssuer, number: int) -> int:
    gh.comment_issue(number, "Rejected — not posting. 🦞")
    gh.close_issue(number)
    log.info("rejected #%s", number)
    return 0


def _handle_approve(gh: GitHubIssuer, number: int, body: str) -> int:
    meta = extract_meta(body)
    draft = extract_reply(body)
    if not meta or not draft:
        gh.comment_issue(
            number,
            "❌ Could not parse draft or metadata. Make sure the reply sits "
            "between the `REPLY_START` / `REPLY_END` HTML comment markers and "
            "the `moltbook-meta` comment is intact, then re-apply the label.",
        )
        return 1

    post_id = meta.get("post_id")
    parent_id = (
        meta.get("parent_id") if meta.get("type") == "reply_to_comment" else None
    )
    if not post_id:
        gh.comment_issue(number, "❌ Metadata missing `post_id`.")
        return 1

    log.info(
        "posting: post=%s parent=%s len=%d", post_id, parent_id, len(draft)
    )

    mb = MoltbookClient()
    success, outcome, comment_id = post_and_verify(
        mb, post_id, draft, parent_id=parent_id
    )

    if not success:
        gh.comment_issue(number, f"❌ {outcome}")
        return 1

    comment_url = (
        f"https://www.moltbook.com/post/{post_id}#comment-{comment_id}"
    )
    gh.comment_issue(number, f"✅ Posted: {comment_url}\n\n{outcome}")
    gh.close_issue(number)
    log.info("DONE #%s — %s", number, outcome)
    return 0


def main() -> int:
    event = _load_event()
    action = event.get("action")
    label = (event.get("label") or {}).get("name")
    issue_summary = event.get("issue") or {}
    number = issue_summary.get("number")
    log.info("event action=%s label=%s issue=#%s", action, label, number)

    if action != "labeled" or label not in (APPROVE_LABEL, REJECT_LABEL):
        log.info("not an approval/reject label — skipping")
        return 0

    gh = GitHubIssuer()
    # Refetch the issue to pick up any user edits to the draft
    issue_full = gh.get_issue(number)
    if not issue_full:
        log.error("could not fetch issue #%s", number)
        return 1
    body = issue_full.get("body") or ""

    if label == REJECT_LABEL:
        return _handle_reject(gh, number)
    return _handle_approve(gh, number, body)


if __name__ == "__main__":
    raise SystemExit(main())
