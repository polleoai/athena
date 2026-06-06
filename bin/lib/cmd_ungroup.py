"""`kb ungroup <hub>` -- dissolve a hub. Verbatim heredoc body in
_ungroup_body.py; see _heredoc_runner."""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_ungroup_body.py", argv, root)
