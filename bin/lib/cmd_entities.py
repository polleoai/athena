"""`kb entities` -- extract / list named entities (needs SpaCy).

Lifted verbatim from the `entities)` heredoc in bin/kb-legacy. Top-level
`sys.exit(N)` -> `return N`. Full path needs the search index + SpaCy; the
parity test covers the deterministic missing-dep / no-index surface.
"""
from __future__ import annotations

import os
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))

    kb_root = root
    args = argv

    try:
        from entity_extraction import extract_all_entities, HAS_SPACY
    except ImportError:
        print("Entity extraction requires SpaCy.")
        print("Install: pip install athena-brain[ner]")
        return 1

    if not HAS_SPACY:
        print("SpaCy not installed. Run: pip install athena-brain[ner]")
        return 1

    from search import _ensure_db, _db_path

    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        print("No search index. Run: kb index")
        return 1

    conn = _ensure_db(kb_root)

    if '--extract' in args:
        print("Extracting entities from all pages...\n")
        pages, entities, err = extract_all_entities(conn, kb_root)
        if err:
            print(f"  Error: {err}")
        else:
            print(f"  Extracted {entities} entities from {pages} pages\n")

    # Show entities
    try:
        rows = conn.execute('''
            SELECT name, entity_type, mention_count
            FROM extracted_entities
            ORDER BY mention_count DESC
            LIMIT 30
        ''').fetchall()
    except Exception:
        rows = []

    if rows:
        print(f"  Top entities ({len(rows)} shown):\n")
        current_type = None
        for name, etype, count in rows:
            if etype != current_type:
                current_type = etype
                print(f"  [{etype}]")
            print(f"    {name} ({count} mentions)")
    else:
        print("  No entities found. Run: kb entities --extract")

    conn.close()
    return 0
