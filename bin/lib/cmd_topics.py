"""`kb topics` -- auto-categorize pages into topics (needs BERTopic).

Lifted verbatim from the `topics)` heredoc in bin/kb-legacy. Top-level
`sys.exit(N)` -> `return N`. Full path needs the search index + BERTopic; the
parity test covers the deterministic missing-dep / no-index surface.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))

    kb_root = root

    try:
        from topic_model import discover_topics, HAS_BERTOPIC
    except ImportError:
        print("Auto-categorization requires BERTopic.")
        print("Install: pip install athena-brain[topics]")
        return 1

    if not HAS_BERTOPIC:
        print("BERTopic not installed. Run: pip install athena-brain[topics]")
        return 1

    from search import _ensure_db, _db_path

    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        print("No search index. Run: kb index")
        return 1

    conn = _ensure_db(kb_root)
    print("Discovering topics...\n")

    topics, note = discover_topics(conn, kb_root)
    if note:
        print(f"  Note: {note}\n")

    if topics:
        print(f"  Found {len(topics)} topics:\n")
        for t in topics:
            kw = ', '.join(t['keywords'][:5])
            print(f"  Topic {t['topic_id']}: {t['label']} ({t['page_count']} pages)")
            print(f"    Keywords: {kw}")
            for p in t['pages'][:3]:
                print(f"      {p}")
            if t['page_count'] > 3:
                print(f"      ... and {t['page_count'] - 3} more")
            print()
    else:
        print("  No topics discovered.")

    conn.close()
    return 0
