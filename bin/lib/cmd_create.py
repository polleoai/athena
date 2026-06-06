"""`kb create <name> [--topic|--insight|--project] [--desc text] [--goal text]`
-- create a hub/group page.

The legacy `create)` arm parsed flags in Bash, optionally prompted for
confirmation (interactive), then delegated to bin/lib/kb_commands.py create. We
reproduce the same arg parsing and the same confirmation prompt, then call
kb_commands.kb_create IN-PROCESS (replicating its __main__ print logic) -- so
output is byte-identical AND the handler compiles into a single binary with no
sibling-.py re-exec.
"""
from __future__ import annotations

from typing import List

import kb_commands


def handle(argv: List[str], root: str) -> int:
    name = ""
    ctype = "topic"
    desc = ""
    goal = ""
    confirmed = False
    shift_next = ""
    for arg in argv:
        if arg in ("--help", "-h"):
            print("Usage: kb create <name> [--topic|--insight|--project] "
                  "[--desc text] [--goal text]")
            return 0
        elif arg == "--topic":
            ctype = "topic"
        elif arg == "--insight":
            ctype = "insight"
        elif arg == "--project":
            ctype = "project"
        elif arg in ("--yes", "-y"):
            confirmed = True
        elif arg in ("--desc", "-d"):
            shift_next = "desc"
        elif arg == "--goal":
            shift_next = "goal"
        else:
            if shift_next == "desc":
                desc = arg
                shift_next = ""
            elif shift_next == "goal":
                goal = arg
                shift_next = ""
            elif not name:
                name = arg

    if not name:
        print("Error: need a name")
        print("Usage: kb create <name> [--topic|--insight|--project]")
        return 1

    if not confirmed:
        print(f"Create: {name} (type: {ctype})")
        try:
            answer = input("Proceed? (y/N) ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            print("Cancelled.")
            return 0

    result = kb_commands.kb_create(root, name, page_type=ctype,
                                   description=desc, goal=goal)
    if result["status"] == "created":
        print(f"Created: {result['file_path']}")
    elif result["status"] == "exists":
        print(f"Error: {result['message']}")
    else:
        print(f"Error: {result.get('message', 'Unknown error')}")
    return 0
