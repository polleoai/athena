"""Auto-categorization via BERTopic for Athena.

Clusters page embeddings to discover topics automatically.
Uses the same fastembed model already installed for search.

Usage:
    from topic_model import discover_topics, assign_topic
"""

import os
import json
import sqlite3
import time

try:
    from bertopic import BERTopic
    from fastembed import TextEmbedding
    HAS_BERTOPIC = True
except ImportError:
    HAS_BERTOPIC = False


def _ensure_tables(conn):
    """Create topics tables if they don't exist."""
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS topics (
            topic_id INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            keywords TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS page_topics (
            page_id TEXT NOT NULL,
            topic_id INTEGER NOT NULL,
            probability REAL NOT NULL,
            PRIMARY KEY (page_id, topic_id)
        );
    ''')
    conn.commit()


def discover_topics(conn, kb_root, min_topic_size=3):
    """Run BERTopic on all pages to discover topic clusters.

    Returns list of topic dicts with: topic_id, label, keywords, page_count, pages.
    """
    if not HAS_BERTOPIC:
        return [], 'BERTopic not installed. Run: pip install athena-brain[topics]'

    # Collect page texts and embeddings
    pages = conn.execute('SELECT page_id, title, summary, rel_path FROM pages').fetchall()
    if len(pages) < min_topic_size * 2:
        return [], f'Need at least {min_topic_size * 2} pages for topic modeling'

    docs = []
    page_ids = []
    for pid, title, summary, rel_path in pages:
        text = f"{title}. {summary or ''}"
        # Add keyword context
        kw_rows = conn.execute(
            'SELECT keyword FROM keywords WHERE page_id = ? AND is_top = 1 ORDER BY rank LIMIT 5',
            (pid,)
        ).fetchall()
        if kw_rows:
            text += '. Keywords: ' + ', '.join(kw for (kw,) in kw_rows)
        docs.append(text)
        page_ids.append(pid)

    # Use fastembed for embeddings (same model as search). Cache pinned to
    # ~/.cache/fastembed to survive macOS $TMPDIR purges — see search.py
    # _fastembed_embed for full rationale.
    import os
    cache_dir = os.path.expanduser("~/.cache/fastembed")
    os.makedirs(cache_dir, exist_ok=True)
    embedding_model = TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir=cache_dir)
    embeddings = list(embedding_model.embed(docs))

    # Run BERTopic
    import numpy as np
    embeddings_array = np.array([e.tolist() if hasattr(e, 'tolist') else list(e) for e in embeddings])

    topic_model = BERTopic(
        min_topic_size=min_topic_size,
        nr_topics='auto',
        verbose=False,
    )
    topics_assigned, probs = topic_model.fit_transform(docs, embeddings_array)

    # Save to database
    _ensure_tables(conn)
    conn.execute('DELETE FROM topics')
    conn.execute('DELETE FROM page_topics')

    topic_info = topic_model.get_topic_info()
    results = []

    for _, row in topic_info.iterrows():
        tid = int(row['Topic'])
        if tid == -1:
            continue  # skip outlier topic

        count = int(row['Count'])
        # Get topic keywords
        topic_words = topic_model.get_topic(tid)
        keywords = [w for w, _ in topic_words[:10]] if topic_words else []
        label = ', '.join(keywords[:3]) if keywords else f'Topic {tid}'

        conn.execute(
            'INSERT OR REPLACE INTO topics (topic_id, label, keywords, page_count) VALUES (?, ?, ?, ?)',
            (tid, label, json.dumps(keywords), count)
        )

        # Get pages in this topic
        topic_pages = []
        for i, assigned_topic in enumerate(topics_assigned):
            if assigned_topic == tid:
                prob = float(probs[i]) if probs is not None else 0.5
                conn.execute(
                    'INSERT OR REPLACE INTO page_topics (page_id, topic_id, probability) VALUES (?, ?, ?)',
                    (page_ids[i], tid, round(prob, 3))
                )
                page_row = conn.execute('SELECT title FROM pages WHERE page_id = ?', (page_ids[i],)).fetchone()
                if page_row:
                    topic_pages.append(page_row[0])

        results.append({
            'topic_id': tid,
            'label': label,
            'keywords': keywords,
            'page_count': count,
            'pages': topic_pages[:10],
        })

    # Count outliers
    outlier_count = sum(1 for t in topics_assigned if t == -1)

    conn.commit()
    return results, f'{outlier_count} pages unassigned (outliers)' if outlier_count > 0 else None
