"""Named Entity Recognition for Athena.

Extracts people, organizations, tools, concepts from page content
using SpaCy with custom technical domain patterns.

Usage:
    from entity_extraction import extract_entities_from_page, extract_all_entities
"""

import os
import re
import json
import sqlite3

try:
    import spacy
    HAS_SPACY = True
except ImportError:
    HAS_SPACY = False

# Module-level model cache
_nlp = None


def _load_model():
    """Load SpaCy model with custom patterns. Cached after first load."""
    global _nlp
    if _nlp is not None:
        return _nlp
    if not HAS_SPACY:
        return None

    try:
        _nlp = spacy.load('en_core_web_sm')
    except OSError:
        # Model not installed — try downloading
        try:
            import subprocess, sys, shutil
            # Frozen binary: sys.executable is the kb binary, not python, so
            # `-m spacy` would fail; resolve a real python3 on PATH.
            py = sys.executable
            if getattr(sys, "frozen", False) or "__compiled__" in globals():
                py = shutil.which("python3") or shutil.which("python") or sys.executable
            subprocess.run(
                [py, '-m', 'spacy', 'download', 'en_core_web_sm'],
                capture_output=True, timeout=120
            )
            _nlp = spacy.load('en_core_web_sm')
        except (subprocess.TimeoutExpired, OSError, ImportError):
            return None

    # Add custom entity patterns for technical domains
    ruler = _nlp.add_pipe('entity_ruler', before='ner')
    patterns = [
        # Course codes
        {'label': 'COURSE', 'pattern': [{'TEXT': {'REGEX': r'^CS\d{3}[A-Z]?$'}}]},
        {'label': 'COURSE', 'pattern': [{'TEXT': {'REGEX': r'^CME\d{3}$'}}]},
        {'label': 'COURSE', 'pattern': [{'TEXT': {'REGEX': r'^MIT\s?\d'}}]},
        # Common AI/ML tools (not always caught by NER)
        {'label': 'TOOL', 'pattern': [{'LOWER': 'pytorch'}]},
        {'label': 'TOOL', 'pattern': [{'LOWER': 'tensorflow'}]},
        {'label': 'TOOL', 'pattern': [{'LOWER': 'langchain'}]},
        {'label': 'TOOL', 'pattern': [{'LOWER': 'obsidian'}]},
        {'label': 'TOOL', 'pattern': [{'LOWER': 'supabase'}]},
        {'label': 'TOOL', 'pattern': [{'LOWER': 'docker'}]},
        {'label': 'TOOL', 'pattern': [{'LOWER': 'kubernetes'}]},
        # AI models
        {'label': 'MODEL', 'pattern': [{'LOWER': {'IN': ['gpt-4', 'gpt-4o', 'claude', 'llama', 'gemini', 'bert', 'whisper']}}]},
    ]
    ruler.add_patterns(patterns)
    return _nlp


def _ensure_tables(conn):
    """Create entity tables if they don't exist."""
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS extracted_entities (
            entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            normalized TEXT NOT NULL,
            mention_count INTEGER DEFAULT 0,
            UNIQUE(normalized, entity_type)
        );

        CREATE TABLE IF NOT EXISTS page_entities (
            page_id TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (page_id, entity_id)
        );
        CREATE INDEX IF NOT EXISTS idx_page_entities_entity ON page_entities(entity_id);
    ''')
    conn.commit()


# Map SpaCy labels to our entity types
_TYPE_MAP = {
    'PERSON': 'person',
    'ORG': 'org',
    'GPE': 'org',      # geopolitical entities → org
    'PRODUCT': 'tool',
    'TOOL': 'tool',
    'MODEL': 'model',
    'COURSE': 'course',
    'WORK_OF_ART': 'concept',
    'EVENT': 'concept',
    'LAW': 'concept',
}

# Entities to skip (too generic)
_SKIP = {
    'ai', 'ml', 'the', 'api', 'llm', 'pdf', 'url', 'cli', 'html',
    'css', 'sql', 'gpu', 'cpu', 'ram', 'ssd', 'one', 'two', 'first',
    'today', 'yesterday', 'monday', 'tuesday', 'january', 'february',
}


def extract_entities_from_text(text, max_length=5000):
    """Extract named entities from text using SpaCy.

    Returns list of (name, entity_type, normalized_key) tuples.
    """
    nlp = _load_model()
    if nlp is None:
        return []

    # Truncate for performance
    text = text[:max_length]

    # Strip markdown formatting
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[#*_`|>~]', ' ', text)
    text = re.sub(r'https?://\S+', '', text)

    doc = nlp(text)
    entities = []
    seen = set()

    for ent in doc.ents:
        name = ent.text.strip()
        label = ent.label_

        # Filter
        if len(name) < 2 or name.lower() in _SKIP:
            continue
        if label not in _TYPE_MAP:
            continue

        entity_type = _TYPE_MAP[label]
        normalized = name.lower().strip()

        if normalized in seen:
            continue
        seen.add(normalized)

        entities.append((name, entity_type, normalized))

    return entities


def extract_all_entities(conn, kb_root):
    """Extract entities from all pages and store in database.

    Returns (extracted_count, entity_count).
    """
    if not HAS_SPACY:
        return 0, 0, 'SpaCy not installed. Run: pip install athena-brain[ner]'

    nlp = _load_model()
    if nlp is None:
        return 0, 0, 'SpaCy model not available'

    _ensure_tables(conn)

    # Clear existing extractions
    conn.execute('DELETE FROM page_entities')
    conn.execute('DELETE FROM extracted_entities')

    pages = conn.execute('SELECT page_id, title, rel_path FROM pages').fetchall()
    extracted_pages = 0

    for pid, title, rel_path in pages:
        filepath = os.path.join(kb_root, rel_path)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except (IOError, OSError):
            continue

        # Parse body (skip frontmatter)
        if content.startswith('---'):
            end = content.find('\n---', 3)
            if end != -1:
                body = content[end + 4:]
            else:
                body = content
        else:
            body = content

        # Also include title for entity extraction
        full_text = f"{title}. {body}"
        entities = extract_entities_from_text(full_text)

        if not entities:
            continue

        for name, entity_type, normalized in entities:
            # Upsert entity
            conn.execute('''
                INSERT INTO extracted_entities (name, entity_type, normalized, mention_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(normalized, entity_type) DO UPDATE SET
                    mention_count = mention_count + 1,
                    name = CASE WHEN LENGTH(excluded.name) > LENGTH(name) THEN excluded.name ELSE name END
            ''', (name, entity_type, normalized))

            # Get entity_id
            row = conn.execute(
                'SELECT entity_id FROM extracted_entities WHERE normalized = ? AND entity_type = ?',
                (normalized, entity_type)
            ).fetchone()
            if row:
                conn.execute(
                    'INSERT OR REPLACE INTO page_entities (page_id, entity_id, count) VALUES (?, ?, 1)',
                    (pid, row[0])
                )

        extracted_pages += 1

    conn.commit()
    entity_count = conn.execute('SELECT COUNT(*) FROM extracted_entities').fetchone()[0]
    return extracted_pages, entity_count, None
