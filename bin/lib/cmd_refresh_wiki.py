"""`kb refresh-wiki <page>` -- re-process the raw source + rewrite the wiki body.
Verbatim heredoc body in _refresh_wiki_body.py; see _heredoc_runner."""
from __future__ import annotations

from typing import List

from _heredoc_runner import run_body


def handle(argv: List[str], root: str) -> int:
    return run_body("_refresh_wiki_body.py", argv, root)
