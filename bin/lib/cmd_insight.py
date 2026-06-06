"""`kb insight "Title" ["body"]` / `kb insight --list` -- save a polished
finding as a permanent wiki page in wiki/insights/.

The legacy `insight)` arm parsed flags in Bash and delegated to
bin/lib/wiki_page.py, post-processing its JSON in inline `python3 -c` filters.
We reproduce the same flag parsing and the same JSON handling, calling the same
module -- so stdout/exit are byte-identical.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import List


def _wiki_page(root: str, extra: List[str]) -> str:
    module = os.path.join(root, "bin", "lib", "wiki_page.py")
    proc = subprocess.run(
        [sys.executable, module, "insight", "--vault", root, *extra],
        cwd=root, capture_output=True, text=True,
    )
    return proc.stdout


def handle(argv: List[str], root: str) -> int:
    title = ""
    body = ""
    do_list = False
    for arg in argv:
        if arg in ("--help", "-h"):
            print('Usage: kb insight "Title" "body text"')
            print("       kb insight --list")
            print("")
            print("Save a polished finding as a permanent wiki page in "
                  "wiki/insights/.")
            return 0
        elif arg in ("--list", "-l"):
            do_list = True
        else:
            if not title:
                title = arg
            elif not body:
                body = arg

    if do_list:
        out = _wiki_page(root, ["--list"])
        try:
            data = json.loads(out)
        except (ValueError, json.JSONDecodeError):
            data = {}
        titles = data.get("insights", [])
        if not titles:
            print('No insights yet. Create one: kb insight "Title"')
        else:
            print(f"Insights ({len(titles)}):")
            for t in titles:
                print(f"  - {t}")
        return 0

    if not title:
        print("Error: need a title")
        print('Usage: kb insight "Title" ["body text"]')
        return 1

    extra = ["--title", title]
    if body:
        extra += ["--body", body]
    result = _wiki_page(root, extra)
    try:
        status = json.loads(result).get("status", "")
    except (ValueError, json.JSONDecodeError):
        status = ""

    if status == "created":
        print(f"Created: wiki/insights/{title}.md")
        print("")
        if not body:
            print(f'In your AI session, say: kb fill-insight "{title}"')
            print("The AI will read related pages and draft the insight with "
                  "citations.")
    elif status == "exists":
        print(f"Error: insight already exists: {title}")
        print("  Edit it directly or use a different title.")
        return 1
    else:
        try:
            msg = json.loads(result).get("message", "Unknown error")
        except (ValueError, json.JSONDecodeError):
            msg = "Unknown error"
        print(f"Error: {msg}")
        return 1
    return 0
