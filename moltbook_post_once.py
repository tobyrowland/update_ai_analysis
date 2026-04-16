"""One-off Moltbook post tool. Triggered via workflow_dispatch.

Usage:
    python moltbook_post_once.py --submolt agents --title "..." --content-file post.md
    python moltbook_post_once.py --submolt agents --title "..." --content "inline text"

Requires MOLTBOOK_API_KEY and ANTHROPIC_API_KEY env vars.
"""

from __future__ import annotations

import argparse
import logging
import sys

from moltbook_lib import MoltbookClient, create_post_and_verify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("moltbook-post")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post once to Moltbook")
    parser.add_argument("--submolt", required=True, help="Submolt name")
    parser.add_argument("--title", required=True, help="Post title")
    parser.add_argument("--content", help="Post body (inline)")
    parser.add_argument("--content-file", help="Read post body from file")
    args = parser.parse_args()

    if args.content_file:
        with open(args.content_file) as f:
            content = f.read()
    elif args.content:
        content = args.content
    else:
        print("ERROR: provide --content or --content-file", file=sys.stderr)
        return 2

    log.info("posting to m/%s: %s (%d chars)", args.submolt, args.title[:60], len(content))

    client = MoltbookClient()
    success, outcome, post_id = create_post_and_verify(
        client, args.submolt, args.title, content
    )

    if success:
        url = f"https://www.moltbook.com/post/{post_id}"
        log.info("SUCCESS: %s — %s", url, outcome)
        print(url)
        return 0
    else:
        log.error("FAILED: %s", outcome)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
