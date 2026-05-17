"""Community detection for Athena knowledge base.

Builds a weighted graph from multiple signals (wikilinks, keywords, embeddings)
and runs Louvain community detection to discover natural page clusters.

Usage:
    from communities import detect_communities, get_community_suggestions
"""

import os
import sqlite3

try:
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False


def _ensure_tables(conn):
    """Create communities tables if they don't exist."""
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS communities (
            community_id INTEGER PRIMARY KEY,
            label TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            top_keywords TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS page_communities (
            page_id TEXT NOT NULL,
            community_id INTEGER NOT NULL,
            modularity REAL,
            PRIMARY KEY (page_id, community_id)
        );
    ''')
    conn.commit()


def build_graph(conn):
    """Build a weighted networkx graph from all available signals.

    Edge sources:
    1. Wikilinks (edges table) — weight from edge type
    2. Shared keywords — 0.1 per shared keyword
    3. Embedding similarity — top 5 nearest neighbors per page
    """
    if not HAS_NETWORKX:
        return None, 'networkx not installed. Run: pip install athena-brain[graph]'

    G = nx.Graph()

    # Add all pages as nodes
    pages = conn.execute('SELECT page_id, title, source_type FROM pages').fetchall()
    for pid, title, stype in pages:
        G.add_node(pid, title=title, source_type=stype)

    if not pages:
        return G, 'No pages in index'

    # Layer 1: Wikilink edges (strongest signal)
    edges = conn.execute('SELECT source_id, target_id, weight FROM edges').fetchall()
    for src, tgt, weight in edges:
        if G.has_node(src) and G.has_node(tgt):
            if G.has_edge(src, tgt):
                G[src][tgt]['weight'] += weight
            else:
                G.add_edge(src, tgt, weight=weight)

    # Layer 2: Shared keywords
    try:
        # Find page pairs sharing keywords
        kw_pairs = conn.execute('''
            SELECT k1.page_id, k2.page_id, COUNT(*) as shared
            FROM keywords k1
            JOIN keywords k2 ON k1.keyword = k2.keyword AND k1.page_id < k2.page_id
            GROUP BY k1.page_id, k2.page_id
            HAVING shared >= 2
        ''').fetchall()

        for pid1, pid2, shared in kw_pairs:
            if G.has_node(pid1) and G.has_node(pid2):
                kw_weight = shared * 0.1
                if G.has_edge(pid1, pid2):
                    G[pid1][pid2]['weight'] += kw_weight
                else:
                    G.add_edge(pid1, pid2, weight=kw_weight)
    except sqlite3.OperationalError:
        pass  # keywords table may not exist

    # Layer 3: Embedding similarity (top 5 neighbors per page)
    try:
        vec_count = conn.execute('SELECT COUNT(*) FROM vec_chunks').fetchone()[0]
        if vec_count > 0:
            # For each page, find the 5 most similar pages via chunk embeddings
            page_ids = [pid for pid, _, _ in pages]
            # This is expensive for large vaults — use a sample approach
            # Only compute for pages that don't already have edges
            isolated = [n for n in G.nodes() if G.degree(n) == 0]
            for pid in isolated[:50]:  # limit to avoid slow builds
                chunks = conn.execute(
                    'SELECT chunk_id FROM chunks WHERE page_id = ? LIMIT 1',
                    (pid,)
                ).fetchone()
                if not chunks:
                    continue
                try:
                    neighbors = conn.execute('''
                        SELECT c.page_id, vc.distance
                        FROM vec_chunks vc
                        JOIN chunks c ON vc.chunk_id = c.chunk_id
                        WHERE vc.embedding MATCH (
                            SELECT embedding FROM vec_chunks WHERE chunk_id = ?
                        )
                        AND c.page_id != ?
                        ORDER BY vc.distance
                        LIMIT 5
                    ''', (chunks[0], pid)).fetchall()
                    for neighbor_pid, distance in neighbors:
                        if G.has_node(neighbor_pid):
                            sim = 1.0 / (1.0 + distance)
                            if not G.has_edge(pid, neighbor_pid):
                                G.add_edge(pid, neighbor_pid, weight=sim)
                except sqlite3.OperationalError:
                    break  # vec query not supported

    except sqlite3.OperationalError:
        pass  # vec_chunks may not exist

    return G, None


def detect_communities(conn, resolution=1.0):
    """Run Louvain community detection on the knowledge graph.

    Returns list of communities, each a dict with:
    - community_id, pages (list of page_ids), label, top_keywords
    """
    if not HAS_NETWORKX:
        return [], 'networkx not installed. Run: pip install athena-brain[graph]'

    G, err = build_graph(conn)
    if err:
        return [], err

    if len(G.nodes()) < 3:
        return [], 'Too few pages for community detection'

    # Remove isolated nodes for cleaner communities
    isolates = list(nx.isolates(G))
    G.remove_nodes_from(isolates)

    if len(G.nodes()) < 3:
        return [], 'Too few connected pages for community detection'

    # Run Louvain
    try:
        communities_sets = louvain_communities(G, weight='weight', resolution=resolution, seed=42)
    except Exception as e:
        return [], f'Community detection failed: {e}'

    # Build community metadata
    _ensure_tables(conn)
    conn.execute('DELETE FROM communities')
    conn.execute('DELETE FROM page_communities')

    results = []
    for i, community_set in enumerate(sorted(communities_sets, key=len, reverse=True)):
        pages_in_community = list(community_set)
        if len(pages_in_community) < 2:
            continue

        # Get page titles
        titles = []
        for pid in pages_in_community:
            row = conn.execute('SELECT title FROM pages WHERE page_id = ?', (pid,)).fetchone()
            if row:
                titles.append(row[0])

        # Find defining keywords for this community
        # Query per-page to avoid dynamic SQL construction
        try:
            kw_counts = {}
            for pid in pages_in_community:
                kw_rows = conn.execute(
                    'SELECT keyword FROM keywords WHERE page_id = ? AND is_top = 1',
                    (pid,)
                ).fetchall()
                for (kw,) in kw_rows:
                    kw_counts[kw] = kw_counts.get(kw, 0) + 1
            top_kw = sorted(kw_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            label = ', '.join(kw for kw, _ in top_kw[:3]) if top_kw else f'Cluster {i + 1}'
            keywords_json = str([kw for kw, _ in top_kw])
        except sqlite3.OperationalError:
            label = f'Cluster {i + 1}'
            keywords_json = '[]'
            top_kw = []

        # Save to database
        conn.execute(
            'INSERT INTO communities (community_id, label, page_count, top_keywords) VALUES (?, ?, ?, ?)',
            (i, label, len(pages_in_community), keywords_json)
        )
        for pid in pages_in_community:
            conn.execute(
                'INSERT INTO page_communities (page_id, community_id) VALUES (?, ?)',
                (pid, i)
            )

        results.append({
            'community_id': i,
            'label': label,
            'page_count': len(pages_in_community),
            'pages': titles[:10],  # first 10 for display
            'top_keywords': [kw for kw, _ in top_kw],
        })

    conn.commit()
    return results, None


def get_community_suggestions(conn, kb_root):
    """Compare detected communities with existing topic pages.

    Returns suggestions for new topic pages.
    """
    # Get existing topic pages
    existing_topics = set()
    rows = conn.execute(
        "SELECT title FROM pages WHERE source_type = 'topic'"
    ).fetchall()
    for (title,) in rows:
        existing_topics.add(title.lower())

    # Get communities
    communities = conn.execute(
        'SELECT community_id, label, page_count, top_keywords FROM communities ORDER BY page_count DESC'
    ).fetchall()

    suggestions = []
    for cid, label, count, keywords_json in communities:
        # Check if a topic page already covers this community
        label_lower = label.lower()
        has_topic = any(
            topic in label_lower or label_lower in topic
            for topic in existing_topics
        )

        if not has_topic and count >= 3:
            suggestions.append({
                'community_id': cid,
                'suggested_name': label.title(),
                'page_count': count,
                'keywords': keywords_json,
                'reason': f'{count} pages cluster around: {label}',
            })

    return suggestions
