"""`kb move <p1> [p2] --into "Hub"` -- move pages into a hub. Verbatim heredoc
body in _move_body.py; see _heredoc_runner."""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_move_body.py", argv, root)
