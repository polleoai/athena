"""`kb backfill-assets [--page <slug>]` -- walk existing raw webpages with
hot-linked images and download them locally, rewriting markdown in place.

Ports the `backfill-assets)` arm of bin/kb-legacy, which was a single
`python3 - "$KB_ROOT" "$@"` heredoc. The body is kept verbatim in
_backfill_assets_body.py and run via _heredoc_runner.
"""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_backfill_assets_body.py", argv, root)
