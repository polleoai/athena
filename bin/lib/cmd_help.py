"""`kb help` / `kb --help` / `kb -h` (and bare `kb`) -- the top-level usage text,
plus the unknown-command error path.

Lifted verbatim from the `help|--help|-h)` arm and the `*)` default arm of
bin/kb-legacy's main dispatch `case "$cmd"`. The Bash `echo` lines become a
single multi-line print so stdout is byte-identical (each `echo` emits its line
+ a trailing newline; `print` does the same).

Two entry points:
  handle()         -- the help text (routed for help / --help / -h / no-args)
  unknown(cmd)     -- the `*)` arm: "Unknown command: <cmd>" + hint, exit 1
"""
from __future__ import annotations

from typing import List

# Verbatim from bin/kb-legacy's help arm (lines 8850-8897). Kept as one literal
# so a diff against the Bash echoes is a straight line-for-line comparison.
_HELP = """\
kb — Athena: Your Second Brain

Commands:
  kb install                Install all dependencies
  kb add <url>              Capture and ingest a URL
  kb add <url1> <url2> ...  Capture multiple URLs
  kb batch                  Process all URLs in inbox/url-new.txt
  kb rename <page> --to      Rename a page and update all references
  kb refresh-wiki <page>     Re-process raw → rewrite wiki body (snapshot to trash)
  kb delete <page>           Trash a page AND its raw source (recoverable via kb undo)
  kb create <name> [--topic]  Create an empty hub/group page
  kb attach <p1> --to        Add page to hub WITHOUT removing other memberships (multi-hub)
  kb detach <p1> --from      Remove page from one hub only (page itself preserved)
  kb move <p1> [p2] --into   Move pages into a hub (auto-removes from old hub)
  kb ungroup <hub>           Dissolve a hub (children become independent)
  kb merge <p1> <p2> ...    Merge pages into one (old pages deleted)
  kb undo                   Restore last trashed files
  kb trash                  List items in trash
  kb purge                  Permanently delete trash older than 30 days
  kb journal "text"           Write a learning journal entry
  kb insight "Title"          Save a polished finding as a wiki page
  kb reflect                  AI proposes insights from journal patterns
  kb report-bug "desc"       Report a bug to upstream Athena repo
  kb request-feature "desc"  Submit a feature request
  kb lint                   Health check — find broken links, orphans, tag issues
  kb list                   List all wiki pages
  kb list --insights        List insight pages
  kb list --topics          List topic pages
  kb list --repos           List repo pages
  kb list --recent          Last 20 additions
  kb list --journal         List journal entries
  kb search <topic>         Find sources about a topic
  kb query <question>       Answer a question from your KB
  kb stats                  Show knowledge base counts
  kb help                   Show this help

Organize:
  kb create "My Topic" --topic                             # create empty hub
  kb move "Page A" "Page B" --into "My Topic"          # relocate (auto-detach from old)
  kb attach "Multi-Topic Paper" --to "AI Security"        # multi-hub: keep all memberships
  kb detach "Page" --from "Old Topic"                   # leave one hub, keep others
  kb ungroup "My Hub"                                      # dissolve hub
  kb merge "Page A" "Page B" --into "Combined"         # fuse into one page
  kb delete "Page"                                         # trash whole page (wiki + raw)

In an AI session (Claude Code, Codex), you can also use natural language:
  'kb add https://...'  or  'ingest this URL'
  'kb search agents'    or  'what do I have about agents?'"""


def handle(argv: List[str], root: str) -> int:
    print(_HELP)
    return 0


def unknown(cmd: str) -> int:
    """The Bash `*)` default arm: report the unknown command and exit 1."""
    print(f"Unknown command: {cmd}")
    print("Run 'kb help' for usage.")
    return 1
