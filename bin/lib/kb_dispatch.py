"""kb dispatcher core: resolve the vault root and route a subcommand to its
native Python handler.

The Bash->Python port is complete: PORTED maps every real subcommand to a
handler, so kb runs identically on macOS, Linux, and Windows with no Bash at
runtime. Unknown commands are reported natively (main()). The original Bash
script is retained as bin/kb-legacy purely as the frozen parity oracle that
tests/test_kb_parity.py compares each handler against; it is never executed in
production.

Handler contract:  handle(argv: list[str], root: str) -> int   (process exit code)
  argv -- the arguments AFTER the subcommand (already shifted)
  root -- absolute vault root (the directory containing bin/ and CLAUDE.md)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Dict, List

import kb_commands
import cmd_add
import cmd_add_content
import cmd_analyze
import cmd_attach
import cmd_backfill_assets
import cmd_bases
import cmd_batch
import cmd_canvas
import cmd_capture_deep
import cmd_config
import cmd_create
import cmd_delete
import cmd_detach
import cmd_doctor
import cmd_entities
import cmd_export
import cmd_help
import cmd_index
import cmd_install
import cmd_insight
import cmd_journal
import cmd_lint
import cmd_merge
import cmd_migrate_raws
import cmd_move
import cmd_purge
import cmd_query
import cmd_refresh_wiki
import cmd_reflect
import cmd_retry_assets
import cmd_remove
import cmd_rename
import cmd_report_bug
import cmd_request_feature
import cmd_schema_check
import cmd_search
import cmd_see_also
import cmd_topics
import cmd_trash
import cmd_undo
import cmd_ungroup

Handler = Callable[[List[str], str], int]


def vault_root() -> str:
    """Resolve the vault root (the directory containing bin/ and CLAUDE.md).

    Source mode: bin/kb lives at <root>/bin/kb, so __file__ pins the root.
    Frozen mode (Nuitka/PyInstaller): __file__ points inside the bundle and is
    useless, so resolve via the established KB_ROOT contract, then walk up from
    CWD to the vault (the CLAUDE.md marker) — mirrors server/athena/__main__.py.
    """
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        env = os.environ.get("KB_ROOT")
        if env and os.path.isdir(env):
            return str(Path(env).resolve())
        cwd = Path.cwd().resolve()
        for cand in (cwd, *cwd.parents):
            if (cand / "CLAUDE.md").is_file():
                return str(cand)
        return str(cwd)
    return str(Path(__file__).resolve().parent.parent.parent)


# ── kb_commands-backed handlers ──────────────────────────────────────────
# These reproduce a Bash arm that shelled out to bin/lib/kb_commands.py. We now
# call the SAME functions IN-PROCESS (kb_stats/_format_stats, kb_list/_format_
# list, kb_rules*) and replicate kb_commands.__main__'s print logic, so output
# stays byte-identical (validated by tests/test_kb_parity.py) AND the handler
# survives compilation into a single binary (no sibling-.py re-exec).

_LIST_FILTERS = {
    "--insights": "insight", "--topics": "topic", "--entities": "entity",
    "--repos": "repo", "--papers": "paper", "--videos": "video",
    "--webpages": "webpage", "--images": "image", "--comparisons": "comparison",
    "--journal": "journal", "--urls": "urls", "--projects": "project",
}


def handle_stats(argv: List[str], root: str) -> int:
    kb_commands._format_stats(kb_commands.kb_stats(root))
    return 0


def handle_list(argv: List[str], root: str) -> int:
    filter_val = ""
    recent = False
    for arg in argv:
        if arg in _LIST_FILTERS:
            filter_val = _LIST_FILTERS[arg]
        elif arg == "--recent":
            recent = True
        elif arg in ("--help", "-h"):
            print("Usage: kb list [--insights|--topics|--repos|--papers|--videos|"
                  "--webpages|--journal|--urls|--projects|--recent]")
            return 0
        else:
            sys.stderr.write(f"Unknown filter: {arg}\n")
            return 1
    kb_commands._format_list(
        kb_commands.kb_list(root, filter_type=filter_val or None, recent=recent)
    )
    return 0


def handle_rules(argv: List[str], root: str) -> int:
    rules_section = ""
    rules_add = ""
    shift_next = ""
    for arg in argv:
        if arg in ("--help", "-h"):
            print('Usage: kb rules [--section name] [add "rule"]')
            return 0
        elif arg == "--section":
            shift_next = "section"
        elif arg == "add":
            shift_next = "add"
        else:
            if shift_next == "section":
                rules_section = arg
                shift_next = ""
            elif shift_next == "add":
                rules_add = arg
                shift_next = ""
            elif not rules_add and arg:
                rules_add = arg
    if rules_add:
        result = kb_commands.kb_rules_add(root, rules_add)
        if result["status"] == "added":
            print(f"Added to {result['section']}:")
            print(f"  - {result['rule']}")
        else:
            print(f"Error: {result.get('message', 'Unknown error')}")
    else:
        kb_commands._format_rules(
            kb_commands.kb_rules(root, section=rules_section or None)
        )
    return 0


# subcommand -> handler. The port is complete: every real subcommand maps to a
# native Python handler, so kb runs identically on macOS, Linux, and Windows with
# no Bash at runtime. Unknown commands are handled natively in main().
PORTED: Dict[str, Handler] = {
    "stats": handle_stats,
    "list": handle_list,
    "rules": handle_rules,
    "journal": cmd_journal.handle,
    "search": cmd_search.handle,
    "query": cmd_query.handle,
    "trash": cmd_trash.handle,
    "index": cmd_index.handle,
    "doctor": cmd_doctor.handle,
    "lint": cmd_lint.handle,
    "remove": cmd_remove.handle,
    "delete": cmd_delete.handle,
    "undo": cmd_undo.handle,
    # ── Phase 2: non-network grouping / synthesis / maintenance ──
    "create": cmd_create.handle,
    "move": cmd_move.handle,
    "ungroup": cmd_ungroup.handle,
    "merge": cmd_merge.handle,
    "rename": cmd_rename.handle,
    "refresh-wiki": cmd_refresh_wiki.handle,
    "bases": cmd_bases.handle,
    "canvas": cmd_canvas.handle,
    "attach": cmd_attach.handle,
    "detach": cmd_detach.handle,
    "purge": cmd_purge.handle,
    "reflect": cmd_reflect.handle,
    "insight": cmd_insight.handle,
    "topics": cmd_topics.handle,
    "entities": cmd_entities.handle,
    "see-also": cmd_see_also.handle,
    "analyze": cmd_analyze.handle,
    "migrate-raws": cmd_migrate_raws.handle,
    "schema-check": cmd_schema_check.handle,
    "report-bug": cmd_report_bug.handle,
    "config": cmd_config.handle,
    "export": cmd_export.handle,
    # ── Phase 3: network / ingest commands ──
    "add": cmd_add.handle,
    "add-content": cmd_add_content.handle,
    "batch": cmd_batch.handle,
    "capture-deep": cmd_capture_deep.handle,
    "backfill-assets": cmd_backfill_assets.handle,
    "retry-assets": cmd_retry_assets.handle,
    "request-feature": cmd_request_feature.handle,
    "feature": cmd_request_feature.handle,
    # ── Final: cross-platform install + native top-level help ──
    "install": cmd_install.handle,
    "help": cmd_help.handle,
    "--help": cmd_help.handle,
    "-h": cmd_help.handle,
}


def main(argv: List[str]) -> int:
    root = vault_root()
    cmd = argv[0] if argv else "help"
    rest = argv[1:]
    handler = PORTED.get(cmd)
    if handler is None:
        # Every real subcommand is now a native Python handler (Phase 3
        # complete), so anything unmatched is a genuinely-unknown command --
        # handled natively, identically to the Bash `*)` default arm. The
        # dispatcher no longer execs bin/kb-legacy at runtime; that script
        # survives only as the frozen parity oracle in tests/test_kb_parity.py.
        return cmd_help.unknown(cmd)
    return handler(rest, root)
