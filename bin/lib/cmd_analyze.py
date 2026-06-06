"""`kb analyze` -- knowledge-graph community detection (needs networkx).

Lifted verbatim from the `analyze)` heredoc in bin/kb-legacy. The heredoc's
top-level `sys.exit(N)` calls become `return N` to satisfy the handler contract;
control flow is otherwise unchanged. Requires the search index + networkx, so
the full path is exercised on hosts with those deps -- the parity test covers
the deterministic no-index / missing-dep surfaces.
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
        from communities import (detect_communities, get_community_suggestions,
                                  build_graph, HAS_NETWORKX)
    except ImportError:
        print("Community detection requires networkx.")
        print("Install: pip install athena-brain[graph]")
        return 1

    if not HAS_NETWORKX:
        print("networkx not installed. Run: pip install athena-brain[graph]")
        return 1

    from search import _ensure_db, _db_path

    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        print("No search index. Run: kb index")
        return 1

    conn = _ensure_db(kb_root)

    print("Analyzing knowledge graph...\n")

    # Build graph stats
    G, err = build_graph(conn)
    if err:
        print(f"Error: {err}")
        return 1

    print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print()

    # Detect communities
    communities, err = detect_communities(conn)
    if err:
        print(f"Error: {err}")
        return 1

    print(f"  Found {len(communities)} communities:\n")
    for c in communities:
        print(f"  [{c['page_count']:2d} pages] {c['label']}")
        for p in c['pages'][:5]:
            print(f"             {p}")
        if c['page_count'] > 5:
            print(f"             ... and {c['page_count'] - 5} more")
        print()

    # Suggestions
    if '--suggest' in args:
        suggestions = get_community_suggestions(conn, kb_root)
        if suggestions:
            print(f"  Suggested topic pages:\n")
            for s in suggestions:
                print(f"    kb create \"{s['suggested_name']}\" --topic")
                print(f"      {s['reason']}")
                print()
        else:
            print("  All communities have matching topic pages.")

    # Visualize
    if '--visualize' in args:
        from export_templates import render_relationship_graph
        import time

        nodes = [{'id': n, 'label': G.nodes[n].get('title', n)[:25],
                  'type': G.nodes[n].get('source_type', '')}
                 for n in list(G.nodes())[:30]]
        edges = [{'source': u, 'target': v} for u, v in list(G.edges())[:60]]

        chart = render_relationship_graph(nodes, edges, 'Knowledge Communities', max_nodes=30)
        export_dir = os.path.join(kb_root, 'wiki', 'exports')
        os.makedirs(export_dir, exist_ok=True)
        filepath = os.path.join(export_dir, 'knowledge-communities.md')
        with open(filepath, 'w') as f:
            f.write(f'---\ntitle: "Knowledge Communities"\ntags: [export, chart]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{chart}\n')
        print(f"  Visualization: wiki/exports/knowledge-communities.md")

    conn.close()
    return 0
