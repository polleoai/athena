"""`kb delete <page>` -- the canonical deletion verb. Trashes the wiki page
AND its raw source(s).

Ports the `delete)` arm of bin/kb-legacy, which is a thin forwarder:

    delete)
      if [ "$#" -lt 1 ]; then echo "Usage: kb delete <page> [--yes]"; exit 1; fi
      "$0" remove "$@" --with-raw

So delete == remove with `--with-raw` appended (which also suppresses remove's
deprecation note). `$#` there is the post-subcommand arg count, i.e. len(argv).
"""
from __future__ import annotations

from typing import List

import cmd_remove


def handle(argv: List[str], root: str) -> int:
    if len(argv) < 1:
        print("Usage: kb delete <page> [--yes]")
        return 1
    return cmd_remove.handle([*argv, "--with-raw"], root)
