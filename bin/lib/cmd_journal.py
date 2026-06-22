"""`kb journal "..."` -- write a learning journal entry (one file per day).

Ports the `journal)` arm of bin/kb-legacy. The shell parsed --help / --recent /
interactive-stdin, called `wiki_page.py journal`, then post-processed the JSON.
All of that is reproduced here in pure Python; the underlying wiki_page helpers
are called directly instead of via a subprocess.

Note on the saved-path message: the legacy arm printed
`Saved to: wiki/journal/${DATE}.md` where DATE is the JSON `date` field
(YYYY-MM-DD) -- NOT the actual `Journal-<date>.md` filename. We reproduce the
legacy wording verbatim for byte-parity.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from wiki_page import create_journal_entry, list_recent_journal

    # ── Shell-level arg parse (mirrors the for/case loop) ──
    journal_text_parts: List[str] = []
    journal_recent = False
    project = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            print('Usage: kb journal "your insight or note"')
            print('       kb journal --project "Project Name" "your note"')
            print("       kb journal --recent")
            print("")
            print("Write a learning journal entry. One file per day in wiki/journal/.")
            print("With --project (or -p), the entry is written under")
            print("wiki/journal/<Project>/ and a new day file is seeded with a")
            print("retrospective template (Done/Data/Problems/Learnings/Tomorrow/KB-worthy).")
            print("In an AI session, the AI will auto-link your entry to related wiki pages.")
            return 0
        elif arg in ("--recent", "-r"):
            journal_recent = True
            i += 1
        elif arg in ("--project", "-p") and i + 1 < len(argv):
            project = argv[i + 1]
            i += 2
        else:
            journal_text_parts.append(arg)
            i += 1

    # JOURNAL_TEXT="$JOURNAL_TEXT $arg" then `sed 's/^ //'` -- leading space stripped.
    journal_text = " ".join(journal_text_parts)

    if journal_recent:
        entries = list_recent_journal(root)
        if not entries:
            print("No journal entries yet.")
        else:
            for e in entries:
                # Top-level entries keep the legacy byte-identical line; only
                # project entries get the "[project]" annotation appended.
                proj = e.get("project")
                if proj:
                    print(f"  {e['date']} ({e['count']} entries) [{proj}]")
                else:
                    print(f"  {e['date']} ({e['count']} entries)")
                for p in e["previews"]:
                    print(f"    - {p}")
        return 0

    # Interactive mode if no text: read stdin (Bash used `cat`).
    if not journal_text:
        print("Journal — write your entry (Ctrl-D to save, Ctrl-C to cancel):")
        try:
            journal_text = sys.stdin.read()
        except KeyboardInterrupt:
            return 0
        # `$(...)` strips trailing newlines.
        journal_text = journal_text.rstrip("\n")

    if not journal_text:
        print("Empty entry. Nothing saved.")
        return 0

    result = create_journal_entry(root, journal_text, project=project)
    date = result.get("date", "") or ""
    if project:
        # Project entries report the real project-relative path.
        print(f"Saved to: {result.get('file_path', '')}")
    else:
        # Legacy byte-identical wording for the no-project path.
        print(f"Saved to: wiki/journal/{date}.md")
    print("")
    print("In an AI session, say 'kb link-journal' to auto-link this entry to related pages.")
    return 0
