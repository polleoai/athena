"""`kb see-also` -- show / generate keyword-based "See Also" cross-references.

Lifted verbatim from the `see-also)` heredoc in bin/kb-legacy. Top-level
`sys.exit(N)` -> `return N` (keeping the `conn.close()` that preceded each).
The `--generate` path mutates wiki pages, so it is state-mutating; the parity
test covers the deterministic no-index path.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from typing import List


def handle(argv: List[str], root: str) -> int:
    sys.path.insert(0, os.path.join(root, "bin", "lib"))
    from search import _ensure_db, _db_path

    kb_root = root
    args = argv

    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        print("No search index. Run: kb index")
        return 1

    conn = _ensure_db(kb_root)

    # Check if keywords table has data
    try:
        kw_count = conn.execute('SELECT COUNT(*) FROM keywords').fetchone()[0]
    except sqlite3.OperationalError:
        kw_count = 0

    if kw_count == 0:
        print("No keywords extracted. Run: kb index")
        conn.close()
        return 1

    if args and args[0] not in ('--all', '--generate'):
        # Show See Also for a specific page
        page_name = ' '.join(args)
        row = conn.execute(
            'SELECT page_id, title FROM pages WHERE title LIKE ? OR rel_path LIKE ?',
            (f'%{page_name}%', f'%{page_name}%')
        ).fetchone()
        if not row:
            print(f"Page not found: {page_name}")
            conn.close()
            return 1

        pid, title = row
        print(f"See Also for: {title}\n")

        # Top 10 keywords
        keywords = conn.execute(
            'SELECT keyword, score FROM keywords WHERE page_id = ? AND is_top = 1 ORDER BY rank',
            (pid,)).fetchall()
        if keywords:
            print(f"  Keywords: {', '.join(k for k, _ in keywords)}\n")

        # Find pages sharing keywords
        related = conn.execute('''
            SELECT p.title, COUNT(*) as shared,
                   GROUP_CONCAT(k2.keyword, ', ') as concepts
            FROM keywords k1
            JOIN keywords k2 ON k1.keyword = k2.keyword AND k1.page_id != k2.page_id
            JOIN pages p ON k2.page_id = p.page_id
            WHERE k1.page_id = ?
            GROUP BY p.title
            HAVING COUNT(*) >= 2
            ORDER BY shared DESC
            LIMIT 10
        ''', (pid,)).fetchall()

        if related:
            for r_title, shared, concepts in related:
                concept_list = concepts.split(', ')[:5]
                print(f"  [[{r_title}]]")
                print(f"    {shared} shared concepts: {', '.join(concept_list)}")
                print()
        else:
            print("  No strongly related pages found (need 2+ shared keywords)")

    elif '--generate' in args:
        # Generate See Also sections and write to wiki pages
        print("Generating See Also cross-references...\n")

        pages = conn.execute('SELECT page_id, title, rel_path FROM pages').fetchall()
        updated = 0

        for pid, title, rel_path in pages:
            related = conn.execute('''
                SELECT p.title, COUNT(*) as shared,
                       GROUP_CONCAT(k2.keyword, ', ') as concepts
                FROM keywords k1
                JOIN keywords k2 ON k1.keyword = k2.keyword AND k1.page_id != k2.page_id
                JOIN pages p ON k2.page_id = p.page_id
                WHERE k1.page_id = ?
                GROUP BY p.title
                HAVING COUNT(*) >= 2
                ORDER BY shared DESC
                LIMIT 5
            ''', (pid,)).fetchall()

            if not related:
                continue

            filepath = os.path.join(kb_root, rel_path)
            if not os.path.exists(filepath):
                continue

            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            # Check if See Also section already exists
            if '## See Also' in content:
                continue

            # Build See Also section
            see_also = '\n## See Also\n\n'
            see_also += '*Related by shared concepts (auto-generated):*\n\n'
            for r_title, shared, concepts in related:
                concept_list = concepts.split(', ')[:3]
                see_also += f'- [[{r_title}]] — {", ".join(concept_list)}\n'

            # Append to file
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write(see_also)
            updated += 1

        print(f"  Updated {updated} pages with See Also sections.")

    else:
        # Show all page keyword counts
        rows = conn.execute('''
            SELECT p.title, COUNT(*) as kw_count,
                   GROUP_CONCAT(CASE WHEN k.is_top THEN k.keyword END, ', ') as top_kw
            FROM pages p
            JOIN keywords k ON p.page_id = k.page_id
            GROUP BY p.title
            ORDER BY kw_count DESC
            LIMIT 20
        ''').fetchall()

        print(f"Top 20 pages by keyword count:\n")
        for title, count, top_kw in rows:
            kw_list = [k for k in (top_kw or '').split(', ') if k][:5]
            print(f"  [{count:2d}] {title}")
            if kw_list:
                print(f"        {', '.join(kw_list)}")

    conn.close()
    return 0
