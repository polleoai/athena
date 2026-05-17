"""Hybrid search engine for Athena Personal.

Three signals merged via Reciprocal Rank Fusion (RRF):
  1. BM25 — SQLite FTS5 with column weights (title > summary > tags > body)
  2. Vector — sqlite-vec chunk embeddings via Ollama (optional)
  3. Graph — wikilink traversal from frontmatter related: + body [[links]]

Storage: .athena/search.db (SQLite, schema portable to PostgreSQL + pgvector)
All tables include vault_id for future Athena Team compatibility.

Usage:
    from search import build_index, search, search_or_grep, index_status
"""

import os
import re
import json
import hashlib
import sqlite3
import time


# ── Constants ──────────────────────────────────────────────────────

ATHENA_DIR = '.athena'
DB_NAME = 'search.db'
SCHEMA_VERSION = '3'  # v3: keywords table for concept extraction
DEFAULT_VAULT_ID = 'local'
RRF_K = 60  # standard constant from the original RRF paper

# Embedding provider priority: fastembed (pip, zero friction) > Ollama (separate app)
# fastembed: BAAI/bge-small-en-v1.5 — 384 dims, 130MB, MTEB 62.17
# Ollama: nomic-embed-text — 768 dims, 270MB, MTEB 62.28
FASTEMBED_MODEL = 'BAAI/bge-small-en-v1.5'
FASTEMBED_DIMENSIONS = 384
OLLAMA_MODEL = 'nomic-embed-text'
OLLAMA_DIMENSIONS = 768


# ── Path and hash helpers ──────────────────────────────────────────

def _db_path(kb_root):
    """Return the path to the search database."""
    return os.path.join(kb_root, ATHENA_DIR, DB_NAME)


def _validate_path(kb_root, rel_path):
    """Ensure a relative path resolves within kb_root."""
    full = os.path.realpath(os.path.join(kb_root, rel_path))
    root = os.path.realpath(kb_root)
    return full.startswith(root + os.sep) or full == root


def _page_id(vault_id, rel_path):
    """Deterministic page ID from vault + path. Maps to UUID in Postgres."""
    return hashlib.sha256(f'{vault_id}:{rel_path}'.encode()).hexdigest()[:16]


def _chunk_id(page_id, chunk_index):
    """Deterministic chunk ID."""
    return hashlib.sha256(f'{page_id}:chunk:{chunk_index}'.encode()).hexdigest()[:16]


def _content_hash(content):
    """SHA256 of file content for change detection."""
    return hashlib.sha256(content.encode('utf-8', errors='replace')).hexdigest()


# ── Database setup ─────────────────────────────────────────────────

def _ensure_db(kb_root):
    """Open or create the search database. Returns a sqlite3.Connection.

    If the schema version doesn't match, drops and recreates all tables.
    If the DB is corrupt, deletes and recreates it.
    """
    athena_dir = os.path.join(kb_root, ATHENA_DIR)
    os.makedirs(athena_dir, exist_ok=True)
    db_file = _db_path(kb_root)

    try:
        conn = sqlite3.connect(db_file)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
    except sqlite3.DatabaseError:
        # Corrupt DB — delete and recreate
        if os.path.exists(db_file):
            os.remove(db_file)
        conn = sqlite3.connect(db_file)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')

    # Load sqlite-vec
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, OSError) as e:
        pass  # sqlite-vec not available — vector search disabled

    # Check schema version
    try:
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key='schema_version'"
        ).fetchone()
        if row and row[0] == SCHEMA_VERSION:
            return conn
    except sqlite3.OperationalError:
        pass  # Tables don't exist yet

    # Create or recreate schema
    _create_schema(conn)
    return conn


def _create_schema(conn):
    """Create all tables. Drops existing tables if present."""
    conn.executescript('''
        DROP TABLE IF EXISTS edges;
        DROP TABLE IF EXISTS vec_chunks;
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS page_fts;
        DROP TABLE IF EXISTS pages;
        DROP TABLE IF EXISTS index_meta;

        CREATE TABLE pages (
            page_id      TEXT PRIMARY KEY,
            vault_id     TEXT NOT NULL DEFAULT 'local',
            rel_path     TEXT NOT NULL,
            title        TEXT NOT NULL,
            summary      TEXT,
            source_type  TEXT,
            tags         TEXT,
            url          TEXT,
            date_added   TEXT,
            content_hash TEXT NOT NULL,
            indexed_at   TEXT NOT NULL,
            UNIQUE(vault_id, rel_path)
        );

        CREATE VIRTUAL TABLE page_fts USING fts5(
            page_id UNINDEXED,
            title,
            summary,
            tags,
            body,
            tokenize='porter unicode61'
        );

        CREATE TABLE chunks (
            chunk_id    TEXT PRIMARY KEY,
            page_id     TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
            vault_id    TEXT NOT NULL DEFAULT 'local',
            chunk_index INTEGER NOT NULL,
            heading     TEXT,
            chunk_text  TEXT NOT NULL,
            token_count INTEGER
        );

        CREATE TABLE edges (
            source_id  TEXT NOT NULL,
            target_id  TEXT NOT NULL,
            vault_id   TEXT NOT NULL DEFAULT 'local',
            edge_type  TEXT NOT NULL DEFAULT 'wikilink',
            weight     REAL NOT NULL DEFAULT 1.0,
            PRIMARY KEY (source_id, target_id, edge_type)
        );
        CREATE INDEX idx_edges_target ON edges(target_id);

        CREATE TABLE keywords (
            page_id  TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
            keyword  TEXT NOT NULL,
            score    REAL NOT NULL,
            rank     INTEGER NOT NULL,
            is_top   BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (page_id, keyword)
        );
        CREATE INDEX idx_keywords_keyword ON keywords(keyword);
        CREATE INDEX idx_keywords_page ON keywords(page_id);

        CREATE TABLE index_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

    ''')
    # Insert schema version via parameterized query (not string formatting)
    conn.execute(
        "INSERT OR REPLACE INTO index_meta VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,)
    )

    # Create vec_chunks if sqlite-vec is loaded.
    # Dimension is determined by which embedding provider is available.
    # Security: dims comes from _get_embedding_dimensions() which returns only
    # hardcoded integer constants (384 or 768), never user input.
    dims = _get_embedding_dimensions()
    if dims not in (384, 768, 1024):
        raise ValueError(f'Invalid embedding dimensions: {dims}')
    # Static DDL per dimension — no string concatenation with untrusted input
    ddl_map = {
        384: 'CREATE VIRTUAL TABLE vec_chunks USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[384])',
        768: 'CREATE VIRTUAL TABLE vec_chunks USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[768])',
        1024: 'CREATE VIRTUAL TABLE vec_chunks USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[1024])',
    }
    try:
        conn.execute(ddl_map[dims])
    except sqlite3.OperationalError:
        pass  # sqlite-vec not loaded — vector search disabled

    conn.commit()


# ── Frontmatter parsing ────────────────────────────────────────────

def _parse_frontmatter(filepath):
    """Parse YAML frontmatter from a markdown file.

    Returns (metadata_dict, body_text). If no frontmatter, returns ({}, full_text).
    Handles both inline tags: [a, b] and block tags with - a lines.
    """
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except (IOError, OSError):
        return {}, ''

    if not content.startswith('---'):
        return {}, content

    end = content.find('\n---', 3)
    if end == -1:
        return {}, content

    yaml_block = content[4:end]
    body = content[end + 4:].strip()
    meta = {}

    lines = yaml_block.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()

            # Check for block list (next lines start with -)
            if value == '' and i + 1 < len(lines) and lines[i + 1].strip().startswith('-'):
                items = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith('-'):
                    item = lines[i].strip().lstrip('- ').strip().strip('"').strip("'")
                    items.append(item)
                    i += 1
                meta[key] = items
                continue
            # Inline list: [a, b, c]
            elif value.startswith('[') and value.endswith(']'):
                items = [v.strip().strip('"').strip("'")
                         for v in value[1:-1].split(',') if v.strip()]
                meta[key] = items
            else:
                meta[key] = value.strip('"').strip("'")
        i += 1

    return meta, body


# ── Text processing ────────────────────────────────────────────────

def _strip_markdown(text):
    """Remove markdown syntax for plain-text indexing.

    Strips: headings, bold/italic, links, wikilinks, code fences,
    Dataview blocks, HTML comments, image refs, horizontal rules.
    """
    # Remove code fences and their content
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove Dataview blocks
    text = re.sub(r'```dataview[\s\S]*?```', '', text)
    # Remove HTML comments
    text = re.sub(r'<!--[\s\S]*?-->', '', text)
    # Remove image refs
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Convert wikilinks to plain text: [[Page|Display]] -> Display, [[Page]] -> Page
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    # Convert markdown links to text: [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    # Remove headings markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
    # Remove horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Remove table formatting
    text = re.sub(r'\|', ' ', text)
    text = re.sub(r'^[-:| ]+$', '', text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_wikilinks(text):
    """Extract wikilink targets from markdown text.

    Handles [[Page Name]] and [[Page Name|Display Text]].
    Returns deduplicated list of page names.
    """
    links = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', text)
    seen = set()
    result = []
    for link in links:
        name = link.strip()
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _chunk_page(body):
    """Split page body at ## headings into chunks.

    Returns list of (heading, chunk_text) tuples.
    If no headings, the entire body is one chunk with heading=None.
    """
    if not body.strip():
        return [(None, '')]

    # Split at ## or ### headings
    parts = re.split(r'^(#{2,3}\s+.+)$', body, flags=re.MULTILINE)

    chunks = []
    current_heading = None
    current_text = []

    for part in parts:
        heading_match = re.match(r'^#{2,3}\s+(.+)$', part.strip())
        if heading_match:
            # Save previous chunk
            text = '\n'.join(current_text).strip()
            if text or current_heading:
                chunks.append((current_heading, text))
            current_heading = heading_match.group(1).strip()
            current_text = []
        else:
            current_text.append(part)

    # Save last chunk
    text = '\n'.join(current_text).strip()
    if text or current_heading:
        chunks.append((current_heading, text))

    # If nothing was split, return entire body as one chunk
    if not chunks:
        chunks = [(None, body.strip())]

    return chunks


def _estimate_tokens(text):
    """Rough token count estimate (~4 chars per token)."""
    return len(text) // 4


# ── Page indexing ──────────────────────────────────────────────────

def _index_page(conn, kb_root, filepath, vault_id=DEFAULT_VAULT_ID):
    """Index a single wiki page into pages, page_fts, and chunks tables.

    Returns True if the page was indexed (new or changed), False if unchanged.
    """
    rel_path = os.path.relpath(filepath, kb_root)
    if not _validate_path(kb_root, rel_path):
        return False

    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            raw_content = f.read()
    except (IOError, OSError):
        return False

    content_hash = _content_hash(raw_content)
    pid = _page_id(vault_id, rel_path)

    # Check if already indexed with same content
    row = conn.execute(
        'SELECT content_hash FROM pages WHERE page_id = ?', (pid,)
    ).fetchone()
    if row and row[0] == content_hash:
        return False  # Unchanged

    # Parse frontmatter
    meta, body = _parse_frontmatter(filepath)
    title = meta.get('title', os.path.basename(filepath).replace('.md', ''))
    summary = meta.get('summary', '')
    source_type = meta.get('source_type', '')
    tags = meta.get('tags', [])
    if isinstance(tags, str):
        tags = [tags]
    tags_json = json.dumps(tags)
    tags_text = ' '.join(t.replace('-', ' ') for t in tags)
    url = meta.get('url', '')
    date_added = meta.get('date_added', '')
    now = time.strftime('%Y-%m-%dT%H:%M:%S')

    # Strip markdown for FTS indexing
    body_plain = _strip_markdown(body)

    # Remove old data for this page
    _remove_page(conn, pid)

    # Insert page
    conn.execute('''
        INSERT INTO pages (page_id, vault_id, rel_path, title, summary,
                          source_type, tags, url, date_added, content_hash, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (pid, vault_id, rel_path, title, summary, source_type,
          tags_json, url, date_added, content_hash, now))

    # Insert into FTS
    conn.execute('''
        INSERT INTO page_fts (page_id, title, summary, tags, body)
        VALUES (?, ?, ?, ?, ?)
    ''', (pid, title, summary, tags_text, body_plain))

    # Chunk the page and insert chunks
    chunks = _chunk_page(body)
    for idx, (heading, chunk_text) in enumerate(chunks):
        cid = _chunk_id(pid, idx)
        plain_chunk = _strip_markdown(chunk_text)
        token_count = _estimate_tokens(plain_chunk)
        conn.execute('''
            INSERT INTO chunks (chunk_id, page_id, vault_id, chunk_index,
                               heading, chunk_text, token_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (cid, pid, vault_id, idx, heading, plain_chunk, token_count))

    return True


def _remove_page(conn, page_id):
    """Remove a page and all associated data (chunks, edges, embeddings)."""
    conn.execute('DELETE FROM page_fts WHERE page_id = ?', (page_id,))
    try:
        conn.execute('DELETE FROM vec_chunks WHERE chunk_id IN '
                     '(SELECT chunk_id FROM chunks WHERE page_id = ?)', (page_id,))
    except sqlite3.OperationalError:
        pass  # vec_chunks table might not exist
    conn.execute('DELETE FROM chunks WHERE page_id = ?', (page_id,))
    conn.execute('DELETE FROM edges WHERE source_id = ? OR target_id = ?',
                 (page_id, page_id))
    conn.execute('DELETE FROM pages WHERE page_id = ?', (page_id,))


# ── Graph building ─────────────────────────────────────────────────

def _build_graph(conn, kb_root, vault_id=DEFAULT_VAULT_ID):
    """Build the wikilink graph from frontmatter related: and body [[links]].

    Returns the number of edges created.
    """
    # Clear existing edges
    conn.execute('DELETE FROM edges WHERE vault_id = ?', (vault_id,))

    # Build page name → page_id map
    rows = conn.execute('SELECT page_id, rel_path, title FROM pages WHERE vault_id = ?',
                        (vault_id,)).fetchall()
    name_to_id = {}
    for pid, rel_path, title in rows:
        # Map by title
        name_to_id[title] = pid
        # Map by filename (without .md)
        basename = os.path.basename(rel_path).replace('.md', '')
        name_to_id[basename] = pid

    edge_count = 0

    for pid, rel_path, title in rows:
        filepath = os.path.join(kb_root, rel_path)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except (IOError, OSError):
            continue

        meta, body = _parse_frontmatter(filepath)

        # Frontmatter related: links (bidirectional, weight 2.0)
        related = meta.get('related', [])
        if isinstance(related, str):
            related = [related]
        for link in related:
            # Strip wikilink syntax: "[[Page Name]]" → "Page Name"
            link_name = link.strip().strip('"').strip("'")
            link_name = re.sub(r'^\[\[|\]\]$', '', link_name)
            target_id = name_to_id.get(link_name)
            if target_id and target_id != pid:
                # Bidirectional
                try:
                    conn.execute(
                        'INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)',
                        (pid, target_id, vault_id, 'related', 2.0))
                    conn.execute(
                        'INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)',
                        (target_id, pid, vault_id, 'related', 2.0))
                    edge_count += 2
                except sqlite3.IntegrityError:
                    pass

        # Body wikilinks (directed, weight 1.0)
        body_links = _extract_wikilinks(body)
        for link_name in body_links:
            target_id = name_to_id.get(link_name)
            if target_id and target_id != pid:
                try:
                    conn.execute(
                        'INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)',
                        (pid, target_id, vault_id, 'wikilink', 1.0))
                    edge_count += 1
                except sqlite3.IntegrityError:
                    pass

    conn.commit()
    return edge_count


# ── Embedding providers ────────────────────────────────────────────
#
# Priority: fastembed (pip, zero friction) > Ollama (separate app) > none
# fastembed is optional: user installs with pip install athena-brain[search]
# Ollama is optional: user installs separately
# If neither is available, vector search is disabled (BM25 + graph still work).

# Module-level cache for the fastembed model (expensive to load)
_fastembed_model = None


def _get_embedding_dimensions():
    """Return embedding dimensions based on available provider."""
    if _fastembed_available():
        return FASTEMBED_DIMENSIONS  # 384
    if _ollama_available():
        return OLLAMA_DIMENSIONS  # 768
    return FASTEMBED_DIMENSIONS  # default for schema creation


def _fastembed_available():
    """Check if fastembed is installed."""
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _fastembed_embed(texts):
    """Embed a list of texts using fastembed. Returns list of vectors.

    Uses BAAI/bge-small-en-v1.5 (384 dims, 130MB, auto-downloads on first use).
    The model is cached after first load for performance.
    """
    global _fastembed_model
    try:
        if _fastembed_model is None:
            from fastembed import TextEmbedding
            # Pin cache to ~/.cache/fastembed — fastembed's default lands in
            # $TMPDIR (/var/folders/.../T/) on macOS, which the OS purges
            # periodically, leaving broken symlinks that fail load with
            # ONNXRuntimeError NO_SUCHFILE. ~/.cache survives reboots and
            # temp cleanups.
            cache_dir = os.path.expanduser("~/.cache/fastembed")
            os.makedirs(cache_dir, exist_ok=True)
            _fastembed_model = TextEmbedding(FASTEMBED_MODEL, cache_dir=cache_dir)
        return list(_fastembed_model.embed(texts))
    except (ImportError, OSError, RuntimeError):
        return []


def _fastembed_embed_single(text):
    """Embed a single text using fastembed. Returns vector or None."""
    results = _fastembed_embed([text])
    if results and len(results) > 0:
        return results[0].tolist()
    return None


def _ollama_available():
    """Check if Ollama is running and reachable on localhost.

    Security: Ollama binds to localhost:11434 without TLS by default.
    HTTP over loopback is safe — traffic never leaves the machine.
    """
    import http.client
    try:
        conn = http.client.HTTPConnection('127.0.0.1', 11434, timeout=2)
        conn.request('GET', '/api/tags', headers={'Accept': 'application/json'})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status == 200
    except (OSError, TimeoutError, http.client.HTTPException):
        return False


def _ollama_embed_single(text):
    """Get embedding vector from local Ollama. Returns list of floats or None.

    Security: Uses http.client with hardcoded 127.0.0.1:11434. No user-controlled
    URLs. HTTP over loopback only.
    """
    import http.client
    try:
        payload = json.dumps({'model': OLLAMA_MODEL, 'input': text}).encode('utf-8')
        conn = http.client.HTTPConnection('127.0.0.1', 11434, timeout=30)
        conn.request('POST', '/api/embed', body=payload,
                     headers={'Content-Type': 'application/json'})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        embeddings = data.get('embeddings', [])
        if embeddings and len(embeddings) > 0:
            return embeddings[0]
        return None
    except (OSError, TimeoutError, http.client.HTTPException,
            json.JSONDecodeError, KeyError, IndexError):
        return None


def _embed_text(text):
    """Embed a single text using the best available provider.

    Priority: fastembed > Ollama > None
    """
    if _fastembed_available():
        return _fastembed_embed_single(text)
    if _ollama_available():
        return _ollama_embed_single(text)
    return None


def _embedding_provider_name():
    """Return the name of the active embedding provider."""
    if _fastembed_available():
        return f'fastembed ({FASTEMBED_MODEL})'
    if _ollama_available():
        return f'Ollama ({OLLAMA_MODEL})'
    return 'none'


def _embed_all_chunks(conn, kb_root, vault_id=DEFAULT_VAULT_ID):
    """Embed all chunks that don't have embeddings yet.

    Uses fastembed (pip, batch mode) if available, falls back to Ollama (one-by-one).
    Returns (embedded_count, skipped_count, provider_name).
    """
    # Check if vec_chunks table exists
    try:
        conn.execute('SELECT COUNT(*) FROM vec_chunks').fetchone()
    except sqlite3.OperationalError:
        return 0, 0, 'none'  # sqlite-vec not loaded

    provider = _embedding_provider_name()
    if provider == 'none':
        return 0, 0, 'none'

    # Find chunks without embeddings
    rows = conn.execute('''
        SELECT c.chunk_id, c.heading, c.chunk_text, p.title, p.summary
        FROM chunks c
        JOIN pages p ON c.page_id = p.page_id
        WHERE c.chunk_id NOT IN (SELECT chunk_id FROM vec_chunks)
        AND c.vault_id = ?
    ''', (vault_id,)).fetchall()

    if not rows:
        return 0, 0, provider

    # Build embedding texts
    chunk_ids = []
    embed_texts = []
    for chunk_id, heading, chunk_text, title, summary in rows:
        parts = [title]
        if heading:
            parts.append(heading)
        if chunk_text:
            words = chunk_text.split()[:500]
            parts.append(' '.join(words))
        chunk_ids.append(chunk_id)
        embed_texts.append('\n\n'.join(parts))

    embedded = 0
    skipped = 0

    # Batch embedding with fastembed (much faster than one-by-one)
    if _fastembed_available():
        try:
            vectors = _fastembed_embed(embed_texts)
            for i, vector in enumerate(vectors):
                try:
                    conn.execute(
                        'INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)',
                        (chunk_ids[i], json.dumps(vector.tolist()))
                    )
                    embedded += 1
                except (sqlite3.OperationalError, sqlite3.IntegrityError):
                    skipped += 1
            conn.commit()
            return embedded, skipped, provider
        except (ImportError, OSError, RuntimeError):
            pass  # Fall through to Ollama

    # One-by-one embedding with Ollama
    for i, embed_text in enumerate(embed_texts):
        vector = _ollama_embed_single(embed_text)

        if vector:
            try:
                conn.execute(
                    'INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)',
                    (chunk_ids[i], json.dumps(vector))
                )
                embedded += 1
            except (sqlite3.OperationalError, sqlite3.IntegrityError):
                skipped += 1
        else:
            skipped += 1

        # Commit every 50 embeddings
        if embedded % 50 == 0 and embedded > 0:
            conn.commit()

    conn.commit()
    return embedded, skipped, provider


# ── Keyword extraction ─────────────────────────────────────────────

def _extract_candidate_phrases(text, max_ngram=3):
    """Extract candidate keyphrases (1-3 word n-grams) from text.

    Filters out stop words, short words, and pure numbers.
    Returns deduplicated list of lowercase phrases.
    """
    # Clean text
    text = re.sub(r'```[\s\S]*?```', '', text)  # remove code blocks
    text = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', text)  # wikilinks to text
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)  # md links to text
    text = re.sub(r'[#*_`|>~]', '', text)  # strip markdown
    text = re.sub(r'https?://\S+', '', text)  # strip URLs
    text = re.sub(r'[^a-zA-Z0-9\s-]', ' ', text)  # keep alphanumeric + hyphens
    text = text.lower()

    words = text.split()
    # Filter individual words
    stop = _STOP_WORDS | {'also', 'new', 'used', 'using', 'based', 'key',
                          'first', 'two', 'three', 'one', 'may', 'can',
                          'including', 'provides', 'supports', 'features',
                          'source', 'open', 'free', 'available', 'designed'}
    words = [w for w in words if len(w) > 2 and w not in stop and not w.isdigit()]

    phrases = set()
    for n in range(1, max_ngram + 1):
        for i in range(len(words) - n + 1):
            phrase = ' '.join(words[i:i + n])
            if len(phrase) > 3:  # skip very short phrases
                phrases.add(phrase)

    return list(phrases)


def _extract_keywords_for_page(page_title, page_body, top_n=50):
    """Extract top N keywords using KeyBERT-style cosine similarity.

    Embeds the full page and each candidate phrase, ranks by similarity.
    Returns list of (keyword, score) tuples.
    """
    if not _fastembed_available():
        return []

    # Build the page embedding text
    page_text = f"{page_title}\n\n{page_body[:2000]}"
    page_vec = _fastembed_embed_single(page_text)
    if page_vec is None:
        return []

    # Extract candidates
    candidates = _extract_candidate_phrases(page_body)
    if not candidates:
        return []

    # Limit candidates to avoid slow embedding (batch the most promising)
    # Pre-filter: keep candidates that appear more than once or are multi-word
    word_freq = {}
    body_lower = page_body.lower()
    scored_candidates = []
    for phrase in candidates:
        freq = body_lower.count(phrase)
        word_count = len(phrase.split())
        # Prefer: multi-word phrases, frequent terms
        prescore = freq * (1 + word_count * 0.5)
        scored_candidates.append((phrase, prescore))

    scored_candidates.sort(key=lambda x: x[1], reverse=True)
    top_candidates = [c[0] for c in scored_candidates[:200]]  # embed top 200

    if not top_candidates:
        return []

    # Batch embed candidates
    try:
        candidate_vecs = _fastembed_embed(top_candidates)
    except (ImportError, OSError, RuntimeError):
        return []

    if not candidate_vecs:
        return []

    # Compute cosine similarity between page and each candidate
    results = []
    for i, vec in enumerate(candidate_vecs):
        vec_list = vec.tolist() if hasattr(vec, 'tolist') else list(vec)
        sim = _cosine_similarity(page_vec, vec_list)
        results.append((top_candidates[i], sim))

    # Sort by similarity, deduplicate overlapping phrases
    results.sort(key=lambda x: x[1], reverse=True)

    # Remove near-duplicate phrases (e.g., "machine learning" and "machine")
    final = []
    seen_words = set()
    for phrase, score in results:
        words = set(phrase.split())
        # Skip if >70% of words already covered by a higher-ranked phrase
        if seen_words and len(words & seen_words) / len(words) > 0.7:
            continue
        final.append((phrase, score))
        seen_words.update(words)
        if len(final) >= top_n:
            break

    return final


def _cosine_similarity(a, b):
    """Pure Python cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _extract_all_keywords(conn, kb_root, vault_id=DEFAULT_VAULT_ID, top_n=50):
    """Extract keywords for all pages that don't have them yet.

    Returns (extracted_count, skipped_count).
    """
    if not _fastembed_available():
        return 0, 0

    # Find pages without keywords
    rows = conn.execute('''
        SELECT p.page_id, p.title, p.rel_path
        FROM pages p
        WHERE p.vault_id = ?
        AND p.page_id NOT IN (SELECT DISTINCT page_id FROM keywords)
    ''', (vault_id,)).fetchall()

    extracted = 0
    skipped = 0

    for page_id, title, rel_path in rows:
        filepath = os.path.join(kb_root, rel_path)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except (IOError, OSError):
            skipped += 1
            continue

        _, body = _parse_frontmatter(filepath)
        keywords = _extract_keywords_for_page(title, body, top_n)

        if keywords:
            for rank, (keyword, score) in enumerate(keywords, 1):
                is_top = rank <= 10
                conn.execute(
                    'INSERT OR REPLACE INTO keywords (page_id, keyword, score, rank, is_top) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (page_id, keyword, round(score, 4), rank, is_top)
                )
            extracted += 1
        else:
            skipped += 1

        # Commit every 10 pages
        if extracted % 10 == 0 and extracted > 0:
            conn.commit()

    conn.commit()
    return extracted, skipped


# ── BM25 search ────────────────────────────────────────────────────

# Common English stop words — filtered from BM25 queries to prevent
# generic terms drowning out specific ones like "cs229"
_STOP_WORDS = frozenset({
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'must',
    'i', 'me', 'my', 'we', 'our', 'you', 'your', 'he', 'she', 'it', 'they',
    'this', 'that', 'these', 'those', 'what', 'which', 'who', 'whom',
    'and', 'or', 'but', 'if', 'of', 'at', 'by', 'for', 'with', 'about',
    'to', 'from', 'in', 'on', 'not', 'no', 'so', 'up', 'out',
    'show', 'find', 'get', 'give', 'tell', 'list', 'where', 'how', 'all',
    'like', 'just', 'also', 'more', 'some', 'any', 'each', 'every', 'both',
    'here', 'there', 'when', 'then', 'than', 'very', 'too', 'only',
    'want', 'know', 'see', 'look', 'make', 'take', 'let', 'try', 'use',
    'link', 'links', 'page', 'pages', 'file', 'files', 'note', 'notes',
    'please', 'thanks', 'help', 'need', 'related', 'about', 'using',
})


def _bm25_search(conn, query, top_k=20):
    """Search using FTS5 BM25 with column weights.

    Weights: title=10, summary=5, tags=3, body=1.
    Runs two queries: AND (all terms must match) and OR (any term matches).
    AND results are scored higher to prioritize exact matches.
    Stop words are filtered to prevent common words from drowning out specific terms.
    Returns list of (page_id, score) tuples, sorted best first.
    """
    # Escape FTS5 special characters
    safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
    if not safe_query:
        return []

    # Filter stop words, keep meaningful terms
    all_terms = safe_query.split()
    terms = [t for t in all_terms if t.lower() not in _STOP_WORDS]
    if not terms:
        terms = all_terms  # If ALL words are stop words, use them anyway

    page_scores = {}

    # Query 1: AND — all meaningful terms must match (highest relevance)
    if len(terms) > 1:
        and_query = ' AND '.join(terms)
        try:
            rows = conn.execute('''
                SELECT page_id, bm25(page_fts, 10.0, 5.0, 3.0, 1.0) AS score
                FROM page_fts
                WHERE page_fts MATCH ?
                ORDER BY score
                LIMIT ?
            ''', (and_query, top_k)).fetchall()
            for pid, score in rows:
                page_scores[pid] = -score * 5.0  # 5x boost for pages matching ALL terms
        except sqlite3.OperationalError:
            pass

    # Query 2: OR — any term matches (broader recall)
    or_query = ' OR '.join(terms)
    try:
        rows = conn.execute('''
            SELECT page_id, bm25(page_fts, 10.0, 5.0, 3.0, 1.0) AS score
            FROM page_fts
            WHERE page_fts MATCH ?
            ORDER BY score
            LIMIT ?
        ''', (or_query, top_k * 2)).fetchall()
        for pid, score in rows:
            or_score = -score
            if pid in page_scores:
                page_scores[pid] += or_score  # Add to AND score
            else:
                page_scores[pid] = or_score
    except sqlite3.OperationalError:
        pass

    if not page_scores:
        return []

    # IDF-weighted title bonus: rarer query terms in titles get bigger boosts.
    # "cs229" in 3 titles → big bonus. "course" in 20+ titles → small bonus.
    import math
    title_rows = conn.execute('SELECT page_id, title FROM pages').fetchall()
    total_titles = len(title_rows)
    terms_lower = [t.lower() for t in terms]

    # Compute term rarity across titles
    t_idf = {}
    for t in terms_lower:
        df = sum(1 for _, title in title_rows if t in title.lower())
        t_idf[t] = min(5.0, math.log(max(total_titles, 1) / max(df, 1)) + 1.0)

    for pid, title in title_rows:
        if pid not in page_scores:
            continue
        title_lower = title.lower()
        weighted = sum(t_idf[t] for t in terms_lower if t in title_lower)
        if weighted > 0:
            max_possible = sum(t_idf[t] for t in terms_lower)
            bonus = (weighted / max_possible) * page_scores[pid] * 3.0
            page_scores[pid] += bonus

    results = sorted(page_scores.items(), key=lambda x: x[1], reverse=True)
    return results[:top_k]


# ── Vector search ──────────────────────────────────────────────────

def _vector_search(conn, query, top_k=20):
    """Search using sqlite-vec chunk embeddings.

    Embeds the query via fastembed or Ollama, then KNN against vec_chunks.
    Returns list of (page_id, score) tuples (deduplicated by page, best chunk wins).
    """
    # Check if vec_chunks table exists and has data
    try:
        count = conn.execute('SELECT COUNT(*) FROM vec_chunks').fetchone()[0]
        if count == 0:
            return []
    except sqlite3.OperationalError:
        return []

    # Embed the query using best available provider
    query_vec = _embed_text(query)
    if not query_vec:
        return []

    # KNN search — two steps because sqlite-vec virtual tables can't JOIN
    try:
        knn_rows = conn.execute('''
            SELECT chunk_id, distance
            FROM vec_chunks
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        ''', (json.dumps(query_vec), top_k * 3)).fetchall()
    except sqlite3.OperationalError:
        return []

    if not knn_rows:
        return []

    # Resolve chunk_id → page_id
    page_scores = {}
    for chunk_id, distance in knn_rows:
        row = conn.execute(
            'SELECT page_id FROM chunks WHERE chunk_id = ?', (chunk_id,)
        ).fetchone()
        if not row:
            continue
        page_id = row[0]
        # sqlite-vec returns L2 distance; convert to similarity (lower distance = better)
        similarity = 1.0 / (1.0 + distance)
        if page_id not in page_scores or similarity > page_scores[page_id]:
            page_scores[page_id] = similarity

    # Sort by similarity descending
    results = sorted(page_scores.items(), key=lambda x: x[1], reverse=True)
    return results[:top_k]


# ── Graph search ───────────────────────────────────────────────────

def _graph_search(conn, seed_ids, max_depth=2, decay=0.5):
    """BFS from seed pages, scoring neighbors by traversal distance.

    Seeds come from BM25 top results. Graph discovers pages that
    keyword search missed but are connected to relevant pages.
    Returns list of (page_id, score) tuples for NEW pages (not in seeds).
    """
    if not seed_ids:
        return []

    seed_set = set(seed_ids)
    scores = {}

    # Assign seed scores based on their rank
    seed_scores = {}
    for i, sid in enumerate(seed_ids):
        seed_scores[sid] = 1.0 / (i + 1)

    # BFS traversal
    frontier = [(sid, seed_scores[sid], 0) for sid in seed_ids]
    visited = set(seed_ids)

    while frontier:
        next_frontier = []
        for node_id, node_score, depth in frontier:
            if depth >= max_depth:
                continue

            # Get neighbors (both directions)
            neighbors = conn.execute('''
                SELECT target_id, weight FROM edges WHERE source_id = ?
                UNION
                SELECT source_id, weight FROM edges WHERE target_id = ?
            ''', (node_id, node_id)).fetchall()

            for neighbor_id, weight in neighbors:
                neighbor_score = node_score * decay * weight
                if neighbor_id in seed_set:
                    continue  # Don't re-score seeds
                scores[neighbor_id] = scores.get(neighbor_id, 0.0) + neighbor_score
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    next_frontier.append((neighbor_id, neighbor_score, depth + 1))

        frontier = next_frontier

    results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return results


# ── RRF merge ──────────────────────────────────────────────────────

def _rrf_merge(ranked_lists, k=RRF_K):
    """Merge multiple ranked result lists using score-aware Reciprocal Rank Fusion.

    Standard RRF uses only rank position, ignoring score magnitude. This variant
    adds a score-ratio bonus: if the top BM25 result scores 20x higher than #5,
    that gap is preserved in the merged ranking. This prevents vector/graph signals
    from overriding a strong keyword match.

    Args:
        ranked_lists: list of (list_name, [(page_id, score)]) tuples
        k: RRF constant (default 60)

    Returns list of (page_id, rrf_score, signal_names) tuples.
    """
    scores = {}      # page_id → cumulative RRF score
    signals = {}     # page_id → set of signal names

    for list_name, ranked_list in ranked_lists:
        if not ranked_list:
            continue

        # Normalize scores within this list to [0, 1]
        max_score = max(s for _, s in ranked_list) if ranked_list else 1.0
        if max_score == 0:
            max_score = 1.0

        for rank, (page_id, raw_score) in enumerate(ranked_list, start=1):
            # Standard RRF component
            rrf_score = 1.0 / (k + rank)
            # Score-ratio bonus: preserves magnitude differences
            normalized = raw_score / max_score
            score_bonus = normalized * (1.0 / (k + 1))  # At most equal to rank-1 RRF

            scores[page_id] = scores.get(page_id, 0.0) + rrf_score + score_bonus
            if page_id not in signals:
                signals[page_id] = set()
            signals[page_id].add(list_name)

    results = [(pid, score, signals[pid])
               for pid, score in scores.items()]
    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ── Grep fallback ──────────────────────────────────────────────────

def _grep_fallback(kb_root, query):
    """Fall back to simple grep when no index exists."""
    results = []
    wiki_dir = os.path.join(kb_root, 'wiki')
    if not os.path.isdir(wiki_dir):
        return results

    query_lower = query.lower()
    for root, dirs, files in os.walk(wiki_dir):
        # Skip templates and dashboards
        dirs[:] = [d for d in dirs if d not in ('dashboards',)]
        for fname in files:
            if not fname.endswith('.md') or fname.startswith('_') or fname == '.gitkeep':
                continue
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                if query_lower in content.lower():
                    meta, _ = _parse_frontmatter(filepath)
                    results.append({
                        'title': meta.get('title', fname.replace('.md', '')),
                        'rel_path': os.path.relpath(filepath, kb_root),
                        'source_type': meta.get('source_type', ''),
                        'summary': meta.get('summary', '')[:100],
                        'score': 0.0,
                        'signals': ['grep'],
                    })
            except (IOError, OSError):
                continue

    return results


# ── Public API ─────────────────────────────────────────────────────

def build_index(kb_root, vault_id=DEFAULT_VAULT_ID):
    """Full rebuild of the search index.

    Scans all wiki pages, indexes them, builds the wikilink graph,
    and optionally embeds chunks via Ollama.

    Returns (success: bool, message: str).
    """
    wiki_dir = os.path.join(kb_root, 'wiki')
    if not os.path.isdir(wiki_dir):
        return False, f'Wiki directory not found: {wiki_dir}'

    conn = _ensure_db(kb_root)

    # Scan all wiki pages
    indexed = 0
    skipped = 0
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if d not in ('dashboards',)]
        for fname in files:
            if not fname.endswith('.md') or fname.startswith('_') or fname == '.gitkeep':
                continue
            filepath = os.path.join(root, fname)
            if _index_page(conn, kb_root, filepath, vault_id):
                indexed += 1
            else:
                skipped += 1

    conn.commit()

    # Remove stale pages (indexed but file no longer exists on disk)
    stale = 0
    indexed_pages = conn.execute(
        'SELECT page_id, rel_path FROM pages WHERE vault_id = ?', (vault_id,)
    ).fetchall()
    for pid, rel_path in indexed_pages:
        if not os.path.exists(os.path.join(kb_root, rel_path)):
            _remove_page(conn, pid)
            stale += 1
    if stale > 0:
        conn.commit()

    # Build wikilink graph
    edge_count = _build_graph(conn, kb_root, vault_id)

    # Embed chunks using best available provider (fastembed > Ollama)
    embedded, embed_skipped, provider = _embed_all_chunks(conn, kb_root, vault_id)

    # Extract keywords (KeyBERT-style, uses fastembed)
    kw_extracted, kw_skipped = _extract_all_keywords(conn, kb_root, vault_id)

    # Update metadata
    page_count = conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
    chunk_count = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO index_meta VALUES ('last_full_build', ?)",
                 (time.strftime('%Y-%m-%dT%H:%M:%S'),))
    conn.execute("INSERT OR REPLACE INTO index_meta VALUES ('page_count', ?)",
                 (str(page_count),))
    conn.commit()

    parts = [f'Indexed {indexed} pages ({skipped} unchanged), {chunk_count} chunks, {edge_count} edges']
    if stale > 0:
        parts.append(f'removed {stale} stale entries')
    if embedded > 0:
        parts.append(f'{embedded} embeddings via {provider}')
    elif provider == 'none':
        parts.append('(no embedding provider — install fastembed: pip install fastembed)')
    if kw_extracted > 0:
        kw_total = conn.execute('SELECT COUNT(*) FROM keywords').fetchone()[0]
        parts.append(f'{kw_extracted} pages keyworded ({kw_total} keywords)')
    conn.close()
    return True, '. '.join(parts)


def update_index(kb_root, changed_files=None, vault_id=DEFAULT_VAULT_ID):
    """Incremental index update.

    If changed_files is provided, re-indexes those specific files.
    Otherwise, scans all wiki pages and re-indexes those with changed content_hash.

    Returns (success: bool, message: str).
    """
    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        return build_index(kb_root, vault_id)

    conn = _ensure_db(kb_root)
    wiki_dir = os.path.join(kb_root, 'wiki')

    indexed = 0
    if changed_files:
        for filepath in changed_files:
            if os.path.exists(filepath) and filepath.endswith('.md'):
                if _index_page(conn, kb_root, filepath, vault_id):
                    indexed += 1
    else:
        for root, dirs, files in os.walk(wiki_dir):
            dirs[:] = [d for d in dirs if d not in ('dashboards',)]
            for fname in files:
                if not fname.endswith('.md') or fname.startswith('_') or fname == '.gitkeep':
                    continue
                filepath = os.path.join(root, fname)
                if _index_page(conn, kb_root, filepath, vault_id):
                    indexed += 1

    conn.commit()

    if indexed > 0:
        _build_graph(conn, kb_root, vault_id)
        _embed_all_chunks(conn, kb_root, vault_id)
        conn.commit()

    conn.close()
    return True, f'Updated {indexed} pages'


def search(kb_root, query, top_k=10, vault_id=DEFAULT_VAULT_ID):
    """Hybrid search: BM25 + vector + graph, merged via RRF.

    Returns (success: bool, results: list[dict]).
    Each result dict has: title, rel_path, source_type, summary, score, signals.
    """
    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        return False, 'No search index. Run: kb index'

    conn = _ensure_db(kb_root)

    # Signal 0: IDF-weighted title match — rarer terms matter more.
    # "cs229" appears in 3 titles → high IDF → strong signal.
    # "stanford" appears in 8+ titles → low IDF → weak signal.
    # This ensures specific identifiers dominate over generic terms.
    safe_terms = re.sub(r'[^\w\s]', ' ', query).split()
    content_terms = [t for t in safe_terms if t.lower() not in _STOP_WORDS]
    if not content_terms:
        content_terms = safe_terms

    title_rows = conn.execute(
        'SELECT page_id, title FROM pages WHERE vault_id = ?', (vault_id,)
    ).fetchall()
    total_titles = len(title_rows)

    # Compute IDF for each query term: how rare is it across all titles?
    term_idf = {}
    for t in content_terms:
        t_lower = t.lower()
        doc_freq = sum(1 for _, title in title_rows if t_lower in title.lower())
        if doc_freq == 0:
            term_idf[t_lower] = 1.0
        else:
            # IDF: rarer terms get higher weight. log(N/df) capped at 5.0
            import math
            term_idf[t_lower] = min(5.0, math.log(max(total_titles, 1) / doc_freq) + 1.0)

    title_matches = []
    for pid, title in title_rows:
        title_lower = title.lower()
        weighted_score = 0.0
        for t in content_terms:
            t_lower = t.lower()
            if t_lower in title_lower:
                weighted_score += term_idf.get(t_lower, 1.0)
        if weighted_score > 0:
            # Normalize by max possible score
            max_possible = sum(term_idf.get(t.lower(), 1.0) for t in content_terms)
            title_matches.append((pid, weighted_score / max_possible if max_possible > 0 else 0))
    title_matches.sort(key=lambda x: x[1], reverse=True)

    # Signal 1: BM25
    bm25_results = _bm25_search(conn, query, top_k=20)

    # Signal 2: Vector (if available)
    vector_results = _vector_search(conn, query, top_k=20)

    # Signal 3: Graph (seeded from BM25 top 5)
    bm25_seed_ids = [pid for pid, _ in bm25_results[:5]]
    graph_results = _graph_search(conn, bm25_seed_ids, max_depth=2)

    # Signal 4: Keyword matching — pages whose extracted keywords match query terms
    keyword_results = []
    try:
        kw_query_terms = [t.lower() for t in content_terms]
        # Query each term individually to avoid dynamic SQL construction
        kw_page_scores = {}
        for term in kw_query_terms:
            kw_rows = conn.execute(
                'SELECT page_id, score FROM keywords WHERE keyword = ?',
                (term,)
            ).fetchall()
            for pid, score in kw_rows:
                if pid not in kw_page_scores:
                    kw_page_scores[pid] = {'matches': 0, 'total_score': 0.0}
                kw_page_scores[pid]['matches'] += 1
                kw_page_scores[pid]['total_score'] += score
        keyword_results = [
            (pid, data['matches'] * data['total_score'])
            for pid, data in kw_page_scores.items()
        ]
        keyword_results.sort(key=lambda x: x[1], reverse=True)
        keyword_results = keyword_results[:20]
    except sqlite3.OperationalError:
        pass

    # Detect if query has specific identifiers (e.g., "cs229", "react", "llama3")
    # Specific identifiers are terms that appear in very few titles (high IDF).
    # When present, BM25+title should dominate over vector+graph.
    has_specific_id = any(term_idf.get(t.lower(), 0) >= 4.0 for t in content_terms)

    if has_specific_id:
        # Specific lookup: repeat BM25+title signals to give them 3x weight
        ranked_lists = [
            ('bm25', bm25_results),
            ('bm25_2', bm25_results),  # extra BM25 weight
            ('title', title_matches[:20]),
            ('title_2', title_matches[:20]),  # extra title weight
            ('title_3', title_matches[:20]),  # extra title weight
        ]
        if vector_results:
            ranked_lists.append(('vector', vector_results))
        if graph_results:
            ranked_lists.append(('graph', graph_results))
        if keyword_results:
            ranked_lists.append(('keyword', keyword_results))
    else:
        # Broad discovery: all signals equal
        ranked_lists = [('bm25', bm25_results)]
        if title_matches:
            ranked_lists.append(('title', title_matches[:20]))
        if vector_results:
            ranked_lists.append(('vector', vector_results))
        if graph_results:
            ranked_lists.append(('graph', graph_results))
        if keyword_results:
            ranked_lists.append(('keyword', keyword_results))

    merged = _rrf_merge(ranked_lists)

    # Enrich results with page metadata
    results = []
    for page_id, score, signal_set in merged[:top_k]:
        row = conn.execute('''
            SELECT title, rel_path, source_type, summary
            FROM pages WHERE page_id = ?
        ''', (page_id,)).fetchone()
        if row:
            results.append({
                'title': row[0],
                'rel_path': row[1],
                'source_type': row[2],
                'summary': (row[3] or '')[:120],
                'score': round(score, 4),
                'signals': sorted(set(s.split('_')[0] for s in signal_set)),
            })

    conn.close()
    return True, results


def search_or_grep(kb_root, query, top_k=10):
    """Search with graceful fallback to grep.

    Tries hybrid search first. If no index exists or search fails,
    falls back to grep and suggests running kb index.
    """
    try:
        ok, results = search(kb_root, query, top_k)
        if ok:
            return True, results
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        # Corrupt DB — delete it
        db_file = _db_path(kb_root)
        if os.path.exists(db_file):
            os.remove(db_file)

    # Fallback to grep
    results = _grep_fallback(kb_root, query)
    return True, results


def index_status(kb_root):
    """Return index statistics.

    Returns dict with page_count, chunk_count, edge_count, embedding_count,
    last_build, db_size_mb.
    """
    db_file = _db_path(kb_root)
    if not os.path.exists(db_file):
        return {'status': 'No index. Run: kb index'}

    conn = _ensure_db(kb_root)
    info = {}

    try:
        info['page_count'] = conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        info['chunk_count'] = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        info['edge_count'] = conn.execute('SELECT COUNT(*) FROM edges').fetchone()[0]
        try:
            info['embedding_count'] = conn.execute('SELECT COUNT(*) FROM vec_chunks').fetchone()[0]
        except sqlite3.OperationalError:
            info['embedding_count'] = 0
        try:
            info['keyword_count'] = conn.execute('SELECT COUNT(*) FROM keywords').fetchone()[0]
            info['pages_with_keywords'] = conn.execute('SELECT COUNT(DISTINCT page_id) FROM keywords').fetchone()[0]
        except sqlite3.OperationalError:
            info['keyword_count'] = 0
            info['pages_with_keywords'] = 0
        row = conn.execute("SELECT value FROM index_meta WHERE key='last_full_build'").fetchone()
        info['last_build'] = row[0] if row else 'never'
        info['db_size_mb'] = round(os.path.getsize(db_file) / (1024 * 1024), 2)
        info['embedding_provider'] = _embedding_provider_name()
    except sqlite3.OperationalError:
        info['status'] = 'Index corrupt. Run: kb index'
    finally:
        conn.close()

    return info
