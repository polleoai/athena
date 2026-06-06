"""`kb retry-assets` -- re-attempt downloads from inbox/asset-retry.tsv.

Ports the `retry-assets)` arm of bin/kb-legacy, a single
`python3 - "$KB_ROOT" "$@"` heredoc. Body kept verbatim in
_retry_assets_body.py and run via _heredoc_runner.
"""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_retry_assets_body.py", argv, root)
