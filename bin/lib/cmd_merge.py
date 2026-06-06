"""`kb merge <p1> <p2> [--into "Name"]` -- merge pages into one (old pages
soft-deleted). Verbatim heredoc body in _merge_body.py; see _heredoc_runner."""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_merge_body.py", argv, root)
