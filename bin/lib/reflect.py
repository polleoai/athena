"""Reflect engine for Athena Personal.

Gathers journal entries, session logs, existing insights, and related wiki
pages, then produces a structured report for the LLM to analyze and propose
new insights.

The LLM does the actual pattern-finding — this module provides the raw
material in a format optimized for that analysis.

Usage:
    from reflect import build_reflect_report
    report = build_reflect_report(kb_root, days=7)
"""

import os
import re
import glob
import datetime
import json


# ── Journal parsing ──────────────────────────────────────────────

def _parse_journal_entries(filepath):
    """Parse a journal markdown file into individual timestamped entries."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract date from filename: "Journal — YYYY-MM-DD.md" or "YYYY-MM-DD.md"
    basename = os.path.splitext(os.path.basename(filepath))[0]
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
    date_str = date_match.group(1) if date_match else basename

    entries = []
    # Pattern: ### HH:MM followed by content until next ### or --- or EOF
    parts = re.split(r'### (\d{2}:\d{2})\n\n', content)
    # parts[0] is frontmatter/header, then alternating: time, content
    for i in range(1, len(parts) - 1, 2):
        time_str = parts[i]
        body = parts[i + 1].strip().rstrip('-').strip()
        if body:
            entries.append({
                'date': date_str,
                'time': time_str,
                'text': body,
                'source_file': filepath,
            })

    return entries


def gather_journal_entries(kb_root, days=7, project=None):
    """Read journal entries from the last N days.

    When ``project`` is set, only the per-project subdir
    ``wiki/journal/<safe(project)>/`` is scanned; otherwise the top-level
    ``wiki/journal/`` is scanned (unchanged behavior).

    Returns list of entry dicts sorted newest-first.
    """
    if project:
        import sys as _sys
        _lib = os.path.dirname(os.path.abspath(__file__))
        if _lib not in _sys.path:
            _sys.path.insert(0, _lib)
        from wiki_writer import _safe_filename
        journal_dir = os.path.join(kb_root, 'wiki', 'journal', _safe_filename(project))
    else:
        journal_dir = os.path.join(kb_root, 'wiki', 'journal')
    if not os.path.isdir(journal_dir):
        return []

    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    all_entries = []

    for filepath in sorted(glob.glob(os.path.join(journal_dir, '*.md')), reverse=True):
        basename = os.path.splitext(os.path.basename(filepath))[0]
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
        if not date_match:
            continue
        try:
            file_date = datetime.date.fromisoformat(date_match.group(1))
        except ValueError:
            continue
        if file_date < cutoff:
            break  # sorted newest-first, so stop early
        all_entries.extend(_parse_journal_entries(filepath))

    # Sort newest-first
    all_entries.sort(key=lambda e: (e['date'], e['time']), reverse=True)
    return all_entries


# ── Session log parsing ──────────────────────────────────────────

def _parse_session_log(filepath):
    """Parse a session log markdown file into structured sections."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract frontmatter
    fm_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
    body = content[fm_match.end():] if fm_match else content

    # Extract sections by ## headings
    sections = {}
    current_heading = None
    current_lines = []
    for line in body.split('\n'):
        heading_match = re.match(r'^## (.+)', line)
        if heading_match:
            if current_heading and current_lines:
                sections[current_heading] = '\n'.join(current_lines).strip()
            current_heading = heading_match.group(1).strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)
    if current_heading and current_lines:
        sections[current_heading] = '\n'.join(current_lines).strip()

    basename = os.path.splitext(os.path.basename(filepath))[0]
    return {
        'filename': basename,
        'sections': sections,
        'source_file': filepath,
    }


def gather_session_logs(kb_root, days=7):
    """Read session logs from the last N days.

    Returns list of session dicts sorted newest-first.
    """
    session_dir = os.path.join(kb_root, 'wiki', 'sessions')
    if not os.path.isdir(session_dir):
        return []

    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    sessions = []

    for filepath in sorted(glob.glob(os.path.join(session_dir, '*.md')), reverse=True):
        basename = os.path.splitext(os.path.basename(filepath))[0]
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
        if not date_match:
            continue
        try:
            file_date = datetime.date.fromisoformat(date_match.group(1))
        except ValueError:
            continue
        if file_date < cutoff:
            break
        sessions.append(_parse_session_log(filepath))

    return sessions


# ── Existing insights ────────────────────────────────────────────

def gather_existing_insights(kb_root):
    """Read all existing insight pages for dedup checking.

    Returns list of dicts with title, summary, rules, evidence, source_file.
    """
    insight_dir = os.path.join(kb_root, 'wiki', 'insights')
    if not os.path.isdir(insight_dir):
        return []

    insights = []
    for filepath in sorted(glob.glob(os.path.join(insight_dir, '*.md'))):
        basename = os.path.basename(filepath)
        if basename in ('.gitkeep', '_TEMPLATE.md'):
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Extract title from frontmatter
        title_match = re.search(r'^title:\s*"?([^"\n]+)"?', content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else os.path.splitext(basename)[0]

        # Extract summary
        summary_match = re.search(r'^summary:\s*"?([^"\n]*)"?', content, re.MULTILINE)
        summary = summary_match.group(1).strip() if summary_match else ''

        # Extract Rules section
        rules_match = re.search(r'## Rules\n\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
        rules = rules_match.group(1).strip() if rules_match else ''
        # Strip HTML comments from rules
        rules = re.sub(r'<!--.*?-->', '', rules, flags=re.DOTALL).strip()

        # Extract Evidence section
        evidence_match = re.search(r'## Evidence\n\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
        evidence = evidence_match.group(1).strip() if evidence_match else ''
        evidence = re.sub(r'<!--.*?-->', '', evidence, flags=re.DOTALL).strip()

        insights.append({
            'title': title,
            'summary': summary,
            'rules': rules,
            'evidence': evidence,
            'source_file': filepath,
        })

    return insights


# ── Reference extraction ─────────────────────────────────────────

_WIKILINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')


def extract_references(entries):
    """Extract wikilinks and page references from journal/session entries.

    Returns set of referenced page names.
    """
    refs = set()
    for entry in entries:
        text = entry.get('text', '')
        # Also check session sections
        for section_text in entry.get('sections', {}).values():
            text += '\n' + section_text

        for match in _WIKILINK_RE.finditer(text):
            refs.add(match.group(1).strip())

    return refs


def read_referenced_pages(kb_root, page_names):
    """Read wiki pages by name. Searches wiki/ subdirectories.

    Returns dict mapping page_name -> {title, content, rel_path}.
    """
    wiki_dir = os.path.join(kb_root, 'wiki')
    pages = {}

    # Build a lookup of all wiki pages by stem name
    all_wiki = {}
    for filepath in glob.glob(os.path.join(wiki_dir, '**', '*.md'), recursive=True):
        stem = os.path.splitext(os.path.basename(filepath))[0]
        rel = os.path.relpath(filepath, kb_root)
        all_wiki[stem] = (filepath, rel)

    for name in page_names:
        if name in all_wiki:
            filepath, rel_path = all_wiki[name]
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            # Extract title from frontmatter
            title_match = re.search(r'^title:\s*"?([^"\n]+)"?', content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else name
            pages[name] = {
                'title': title,
                'content': content,
                'rel_path': rel_path,
            }

    return pages


# ── Keyword/theme extraction from entries ────────────────────────

def extract_entry_themes(entries):
    """Extract recurring terms and themes from journal entries.

    Simple frequency-based approach — finds terms that appear in multiple
    entries, which suggests a recurring interest or pattern.

    Returns list of (term, count) sorted by frequency.
    """
    # Tokenize and count across entries
    from collections import Counter
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'shall', 'can', 'this', 'that', 'these',
        'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they', 'my', 'your',
        'his', 'her', 'its', 'our', 'their', 'what', 'which', 'who', 'whom',
        'when', 'where', 'why', 'how', 'all', 'each', 'every', 'both', 'few',
        'more', 'most', 'other', 'some', 'such', 'no', 'not', 'only', 'own',
        'same', 'so', 'than', 'too', 'very', 'just', 'about', 'above', 'after',
        'again', 'also', 'and', 'any', 'because', 'before', 'between', 'but',
        'by', 'for', 'from', 'if', 'in', 'into', 'of', 'on', 'or', 'out',
        'over', 'then', 'to', 'up', 'with', 'while', 'at', 'as', 'like',
        'make', 'use', 'get', 'one', 'two', 'new', 'see', 'way', 'well',
    }

    # Track which entries each term appears in (for cross-entry frequency)
    term_entries = {}  # term -> set of entry indices
    for idx, entry in enumerate(entries):
        text = entry.get('text', '')
        tokens = re.findall(r'[a-zA-Z][\w-]*[a-zA-Z]|\b[A-Z][a-zA-Z]+\b', text)
        seen_in_entry = set()
        for token in tokens:
            t = token.lower()
            if t not in stop_words and len(t) > 2:
                if t not in seen_in_entry:
                    seen_in_entry.add(t)
                    term_entries.setdefault(t, set()).add(idx)

    # Only keep terms appearing in 2+ entries (cross-entry patterns)
    recurring = [(term, len(idxs)) for term, idxs in term_entries.items() if len(idxs) >= 2]
    recurring.sort(key=lambda x: x[1], reverse=True)
    return recurring[:30]


# ── User pattern detection ───────────────────────────────────────

# Signal phrases that indicate specific learning/thinking patterns
_PATTERN_SIGNALS = {
    'reconciliation': {
        'phrases': [
            r'(?:both|all \w+) (?:do|explain|teach|cover|use)',
            r'same (?:concept|thing|problem|idea|pattern)',
            r'different (?:ways?|approaches?|perspectives?|abstraction)',
            r'(?:while|whereas|but) .{5,40} (?:explains?|teaches?|uses?|calls?)',
            r'(?:unif|reconcil|bridg|connect)',
        ],
        'label': 'Reconciliation learner',
        'description': 'Learns by comparing how different sources explain the same concept',
    },
    'gap_detection': {
        'phrases': [
            r'\bgap\b',
            r'\bmissing\b',
            r'(?:not|no|neither) (?:cover|mention|address|include)',
            r'blind spot',
            r'doesn\'t (?:cover|mention|have)',
        ],
        'label': 'Gap detector',
        'description': 'Actively identifies what the KB is missing, not just what it has',
    },
    'architecture_thinking': {
        'phrases': [
            r'(?:pipeline|architecture|pattern|framework|system)',
            r'(?:stage|layer|step) \d',
            r'(?:→|->|leads to|feeds into)',
            r'(?:identical|same|universal) (?:pattern|pipeline|architecture)',
        ],
        'label': 'Systems thinker',
        'description': 'Sees structural patterns and pipelines across domains',
    },
    'meta_observation': {
        'phrases': [
            r'(?:noticed|interesting|realized|occurred to me)',
            r'(?:suggests?|implies?|means?) that',
            r'this (?:is|maps to|connects)',
            r'(?:pattern|trend|trajectory)',
        ],
        'label': 'Meta-observer',
        'description': 'Steps back to observe patterns in their own learning process',
    },
    'depth_first': {
        'phrases': [
            r'(?:deep|deeper|thoroughly|detail|internals)',
            r'(?:from scratch|ground up|first principles)',
            r'(?:cover-to-cover|every lecture|full course)',
        ],
        'label': 'Depth-first learner',
        'description': 'Prefers mastering one topic fully before moving on',
    },
    'breadth_first': {
        'phrases': [
            r'(?:curated|survey|overview|landscape)',
            r'(?:many|several|multiple|different) (?:tools?|frameworks?|approaches?)',
            r'(?:compare|comparison|versus|vs\.?)',
        ],
        'label': 'Breadth-first explorer',
        'description': 'Surveys the landscape before diving deep',
    },
}


def detect_user_patterns(entries):
    """Detect meta-patterns in how the user thinks and learns.

    Scans journal entries for signal phrases that indicate learning styles,
    thinking patterns, and exploration strategies.

    Returns list of detected patterns with evidence.
    """
    if not entries:
        return []

    pattern_hits = {}  # pattern_key -> list of (entry_idx, matched_phrase)

    for idx, entry in enumerate(entries):
        text = entry.get('text', '')
        for key, signals in _PATTERN_SIGNALS.items():
            for phrase_re in signals['phrases']:
                if re.search(phrase_re, text, re.IGNORECASE):
                    pattern_hits.setdefault(key, []).append({
                        'entry_idx': idx,
                        'date': entry.get('date', ''),
                        'time': entry.get('time', ''),
                        'snippet': text[:120],
                    })
                    break  # one match per pattern per entry is enough

    # Only report patterns with 2+ hits (real pattern, not noise)
    detected = []
    for key, hits in pattern_hits.items():
        if len(hits) >= 2:
            signals = _PATTERN_SIGNALS[key]
            detected.append({
                'key': key,
                'label': signals['label'],
                'description': signals['description'],
                'hit_count': len(hits),
                'evidence': hits[:5],  # cap evidence at 5 entries
            })

    # Sort by hit count
    detected.sort(key=lambda p: p['hit_count'], reverse=True)
    return detected


# ── Cross-insight analysis ───────────────────────────────────────

def analyze_insight_connections(insights):
    """Find connections between existing insights.

    Checks for: shared evidence pages, overlapping tags/rules,
    thematic similarity via shared terms.

    Returns list of connection dicts.
    """
    if len(insights) < 2:
        return []

    connections = []

    for i in range(len(insights)):
        for j in range(i + 1, len(insights)):
            a, b = insights[i], insights[j]
            shared_signals = []

            # Check shared evidence (wikilinks in evidence text)
            a_links = set(_WIKILINK_RE.findall(a.get('evidence', '')))
            b_links = set(_WIKILINK_RE.findall(b.get('evidence', '')))
            shared_links = a_links & b_links
            if shared_links:
                shared_signals.append(f"Shared evidence pages: {', '.join(list(shared_links)[:3])}")

            # Check overlapping rule themes (significant words in rules)
            a_words = set(re.findall(r'[a-zA-Z]{4,}', a.get('rules', '').lower()))
            b_words = set(re.findall(r'[a-zA-Z]{4,}', b.get('rules', '').lower()))
            overlap = a_words & b_words - {'when', 'this', 'that', 'with', 'from',
                                            'into', 'about', 'update', 'check', 'should',
                                            'insight', 'table', 'section', 'added', 'link'}
            if len(overlap) >= 3:
                shared_signals.append(f"Overlapping rule themes: {', '.join(list(overlap)[:5])}")

            # Check title word overlap (significant terms)
            a_title = set(re.findall(r'[a-zA-Z]{4,}', a.get('title', '').lower()))
            b_title = set(re.findall(r'[a-zA-Z]{4,}', b.get('title', '').lower()))
            title_overlap = a_title & b_title - {'the', 'and', 'for', 'from', 'with'}
            if title_overlap:
                shared_signals.append(f"Shared title terms: {', '.join(title_overlap)}")

            if shared_signals:
                connections.append({
                    'insight_a': a['title'],
                    'insight_b': b['title'],
                    'signals': shared_signals,
                    'strength': len(shared_signals),
                })

    connections.sort(key=lambda c: c['strength'], reverse=True)
    return connections


# ── Search-based discovery ───────────────────────────────────────

def find_related_via_search(kb_root, entries, max_queries=5):
    """Use the search engine to find wiki pages related to journal themes.

    Extracts key phrases from entries and runs them as search queries.
    Returns list of search result dicts.
    """
    try:
        from search import search as kb_search
    except ImportError:
        try:
            import sys
            lib_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from search import search as kb_search
        except ImportError:
            return []

    # Build queries from entry content — pick distinctive phrases
    queries = set()
    for entry in entries[:10]:  # cap at 10 entries to avoid excessive searches
        text = entry.get('text', '')
        # Look for proper nouns, course codes, tool names
        # Course codes: CS229, CS336, etc.
        codes = re.findall(r'\b[A-Z]{2,5}\d{3,4}\b', text)
        queries.update(codes)
        # Capitalized proper nouns (2+ words)
        proper = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', text)
        queries.update(proper)
        # Quoted terms
        quoted = re.findall(r'"([^"]+)"', text)
        queries.update(quoted)

    # Also add recurring theme terms
    themes = extract_entry_themes(entries)
    for term, count in themes[:5]:
        queries.add(term)

    # Run searches
    all_results = {}
    queries_run = 0
    for q in list(queries)[:max_queries]:
        if not q.strip():
            continue
        ok, results = kb_search(kb_root, q, top_k=5)
        if ok and isinstance(results, list):
            for r in results:
                path = r.get('rel_path', '')
                if path and path not in all_results:
                    all_results[path] = r
        queries_run += 1

    return list(all_results.values()), list(queries)[:max_queries]


def _focus_search(kb_root, focus, entries, max_queries=5):
    """Search using the focus string as the primary query, plus entry-derived queries.

    The focus gets priority — it's always the first query. Remaining slots
    go to terms extracted from focus-relevant journal entries.
    """
    try:
        from search import search as kb_search
    except ImportError:
        try:
            import sys
            lib_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
            if lib_dir not in sys.path:
                sys.path.insert(0, lib_dir)
            from search import search as kb_search
        except ImportError:
            return [], [focus]

    all_results = {}
    queries_used = []

    # Primary query: the focus itself
    ok, results = kb_search(kb_root, focus, top_k=10)
    queries_used.append(focus)
    if ok and isinstance(results, list):
        for r in results:
            path = r.get('rel_path', '')
            if path and path not in all_results:
                all_results[path] = r

    # Secondary queries: extract terms from focus-relevant entries
    relevant_entries = [e for e in entries if e.get('relevance', 0) > 0.3]
    secondary_queries = set()
    for entry in relevant_entries[:5]:
        text = entry.get('text', '')
        codes = re.findall(r'\b[A-Z]{2,5}\d{3,4}\b', text)
        secondary_queries.update(codes)
        proper = re.findall(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+', text)
        secondary_queries.update(proper)

    for q in list(secondary_queries)[:max_queries - 1]:
        if not q.strip() or q == focus:
            continue
        ok, results = kb_search(kb_root, q, top_k=5)
        queries_used.append(q)
        if ok and isinstance(results, list):
            for r in results:
                path = r.get('rel_path', '')
                if path and path not in all_results:
                    all_results[path] = r

    return list(all_results.values()), queries_used


# ── Recent wiki activity ─────────────────────────────────────────

def gather_recent_wiki_changes(kb_root, days=7):
    """Find wiki pages modified in the last N days.

    Returns list of {title, rel_path, modified_date}.
    """
    cutoff_ts = (datetime.datetime.now() - datetime.timedelta(days=days)).timestamp()
    wiki_dir = os.path.join(kb_root, 'wiki')
    recent = []

    for filepath in glob.glob(os.path.join(wiki_dir, '**', '*.md'), recursive=True):
        basename = os.path.basename(filepath)
        if basename in ('.gitkeep', '_TEMPLATE.md'):
            continue
        mtime = os.path.getmtime(filepath)
        if mtime >= cutoff_ts:
            rel = os.path.relpath(filepath, kb_root)
            with open(filepath, 'r', encoding='utf-8') as f:
                first_lines = f.read(500)
            title_match = re.search(r'^title:\s*"?([^"\n]+)"?', first_lines, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else os.path.splitext(basename)[0]
            modified = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
            recent.append({
                'title': title,
                'rel_path': rel,
                'modified_date': modified,
            })

    recent.sort(key=lambda x: x['modified_date'], reverse=True)
    return recent


# ── Main report builder ──────────────────────────────────────────

def _filter_entries_by_focus(entries, focus):
    """Score and filter journal entries by relevance to the focus topic.

    Returns all entries, but sorted with focus-relevant entries first.
    Each entry gets a 'relevance' field (0.0 = unrelated, 1.0 = highly relevant).
    """
    if not focus:
        for e in entries:
            e['relevance'] = 1.0
        return entries

    focus_lower = focus.lower()
    focus_terms = set(re.findall(r'[a-zA-Z][\w-]*[a-zA-Z]', focus_lower))

    for entry in entries:
        text_lower = entry.get('text', '').lower()
        # Score: fraction of focus terms found in entry
        if focus_terms:
            matches = sum(1 for t in focus_terms if t in text_lower)
            entry['relevance'] = matches / len(focus_terms)
        else:
            entry['relevance'] = 1.0 if focus_lower in text_lower else 0.0

    # Sort: relevant entries first, then by date
    entries.sort(key=lambda e: (-e['relevance'], e['date'], e['time']), reverse=False)
    entries.sort(key=lambda e: e['relevance'], reverse=True)
    return entries


def build_reflect_report(kb_root, days=7, deep=False, focus=None, project=None):
    """Build the complete reflect report for LLM analysis.

    Args:
        kb_root: Path to the knowledge base root.
        days: How many days back to look (default 7).
        deep: If True, also run search-based discovery for related pages.
        focus: Optional user-provided focus/question to guide analysis.

    Returns a dict with all gathered data:
        - journal_entries: list of entry dicts
        - session_logs: list of session dicts
        - existing_insights: list of insight dicts
        - referenced_pages: dict of page_name -> page data
        - recurring_themes: list of (term, count) tuples
        - search_discoveries: list of search result dicts (if deep or focus)
        - recent_changes: list of recently modified wiki pages
        - focus: the user's focus string (or None)
        - stats: summary counts
    """
    journal_entries = gather_journal_entries(kb_root, days=days, project=project)
    session_logs = gather_session_logs(kb_root, days=days)
    existing_insights = gather_existing_insights(kb_root)

    # Score entries by focus relevance
    if focus:
        journal_entries = _filter_entries_by_focus(journal_entries, focus)

    # Extract references from both journals and sessions
    all_entries = []
    for e in journal_entries:
        all_entries.append(e)
    for s in session_logs:
        all_entries.append(s)

    refs = extract_references(all_entries)
    referenced_pages = read_referenced_pages(kb_root, refs)

    # Theme extraction from journal entries
    recurring_themes = extract_entry_themes(journal_entries)

    # Search-based discovery — auto-enable when focus is provided
    search_discoveries = []
    search_queries = []
    should_search = deep or bool(focus)
    if should_search and (journal_entries or focus):
        # When focus is set, use it as the primary search query
        if focus:
            search_discoveries, search_queries = _focus_search(
                kb_root, focus, journal_entries, max_queries=5
            )
        else:
            search_discoveries, search_queries = find_related_via_search(
                kb_root, journal_entries, max_queries=5
            )

    # User pattern detection — how the user thinks/learns
    user_patterns = detect_user_patterns(journal_entries)

    # Cross-insight analysis — connections between existing insights
    insight_connections = []
    if deep or focus:
        insight_connections = analyze_insight_connections(existing_insights)

    # Recent wiki activity
    recent_changes = gather_recent_wiki_changes(kb_root, days=days)

    return {
        'journal_entries': journal_entries,
        'session_logs': session_logs,
        'existing_insights': existing_insights,
        'referenced_pages': referenced_pages,
        'recurring_themes': recurring_themes,
        'search_discoveries': search_discoveries,
        'search_queries': search_queries,
        'user_patterns': user_patterns,
        'insight_connections': insight_connections,
        'recent_changes': recent_changes,
        'days': days,
        'focus': focus,
        'stats': {
            'journal_entry_count': len(journal_entries),
            'session_count': len(session_logs),
            'existing_insight_count': len(existing_insights),
            'referenced_page_count': len(referenced_pages),
            'recurring_theme_count': len(recurring_themes),
            'search_discovery_count': len(search_discoveries),
            'user_pattern_count': len(user_patterns),
            'insight_connection_count': len(insight_connections),
            'recent_change_count': len(recent_changes),
        },
    }


def format_report_text(report):
    """Format the reflect report as readable text for CLI or MCP output.

    This is the structured output the LLM reads to find patterns and
    propose insights.
    """
    lines = []
    stats = report['stats']
    focus = report.get('focus')

    if focus:
        lines.append(f"# Reflect Report — last {report['days']} days")
        lines.append(f'## Focus: "{focus}"')
    else:
        lines.append(f"# Reflect Report — last {report['days']} days")
    lines.append('')
    lines.append(f"Journal entries: {stats['journal_entry_count']} | "
                 f"Sessions: {stats['session_count']} | "
                 f"Existing insights: {stats['existing_insight_count']} | "
                 f"Referenced pages: {stats['referenced_page_count']} | "
                 f"Recent wiki changes: {stats['recent_change_count']}")
    lines.append('')

    # ── Journal Entries ──────────────────────────────────
    if report['journal_entries']:
        lines.append('## Journal Entries')
        lines.append('')
        for entry in report['journal_entries']:
            lines.append(f"### {entry['date']} {entry['time']}")
            lines.append(entry['text'])
            lines.append('')
    else:
        lines.append('## Journal Entries')
        lines.append('')
        lines.append('_No journal entries in this period. The user can write entries with `kb journal "text"`._')
        lines.append('')

    # ── Session Logs ─────────────────────────────────────
    if report['session_logs']:
        lines.append('## Session Logs')
        lines.append('')
        for session in report['session_logs']:
            lines.append(f"### Session: {session['filename']}")
            for heading, content in session['sections'].items():
                lines.append(f"**{heading}:**")
                lines.append(content)
                lines.append('')
            lines.append('')

    # ── Recurring Themes ─────────────────────────────────
    if report['recurring_themes']:
        lines.append('## Recurring Themes (terms appearing in 2+ entries)')
        lines.append('')
        for term, count in report['recurring_themes'][:15]:
            lines.append(f"  - **{term}** ({count} entries)")
        lines.append('')

    # ── Existing Insights (for dedup) ────────────────────
    if report['existing_insights']:
        lines.append('## Existing Insights (do NOT duplicate)')
        lines.append('')
        for insight in report['existing_insights']:
            lines.append(f"- **{insight['title']}**")
            if insight['summary']:
                lines.append(f"  {insight['summary']}")
            if insight['rules']:
                lines.append(f"  Rules: {insight['rules'][:200]}")
            lines.append('')

    # ── Referenced Pages ─────────────────────────────────
    if report['referenced_pages']:
        lines.append('## Referenced Wiki Pages')
        lines.append('')
        for name, page in report['referenced_pages'].items():
            lines.append(f"### [[{name}]] ({page['rel_path']})")
            # Show first ~300 chars of content after frontmatter
            body = re.sub(r'^---\n.*?\n---\n', '', page['content'], count=1, flags=re.DOTALL)
            snippet = body.strip()[:400]
            if snippet:
                lines.append(snippet)
            lines.append('')

    # ── Search Discoveries ───────────────────────────────
    if report['search_discoveries']:
        lines.append('## Related Pages Found via Search')
        lines.append(f"_(Searched for: {", ".join(report["search_queries"])})_")
        lines.append('')
        for r in report['search_discoveries'][:10]:
            title = r.get('title', 'Untitled')
            path = r.get('rel_path', '')
            summary = r.get('summary', '')
            lines.append(f"- **{title}** (`{path}`)")
            if summary:
                lines.append(f"  {summary[:200]}")
        lines.append('')

    # ── User Patterns ────────────────────────────────────
    if report.get('user_patterns'):
        lines.append('## User Learning Patterns Detected')
        lines.append('')
        for pattern in report['user_patterns']:
            lines.append(f"**{pattern['label']}** — {pattern['description']} ({pattern['hit_count']} entries)")
            for ev in pattern['evidence'][:3]:
                lines.append(f"  - {ev['date']} {ev['time']}: _{ev['snippet']}..._")
            lines.append('')

    # ── Cross-Insight Connections ────────────────────────
    if report.get('insight_connections'):
        lines.append('## Insight Connections (consider grouping)')
        lines.append('')
        for conn in report['insight_connections']:
            lines.append(f"**{conn['insight_a']}** ↔ **{conn['insight_b']}**")
            for signal in conn['signals']:
                lines.append(f"  - {signal}")
            lines.append('')

    # ── Recent Wiki Changes ──────────────────────────────
    if report['recent_changes']:
        lines.append('## Recently Modified Wiki Pages')
        lines.append('')
        for page in report['recent_changes'][:15]:
            lines.append(f"- {page['title']} (`{page['rel_path']}`, modified {page['modified_date']})")
        lines.append('')

    # ── Analysis Instructions ────────────────────────────
    lines.append('---')
    lines.append('')
    if focus:
        lines.append(f'## Your Task: Analyze Through the Lens of "{focus}"')
        lines.append('')
        lines.append(f'The user wants to reflect specifically on: **{focus}**')
        lines.append('')
        lines.append('Using the journal entries, session logs, referenced pages, and search results above:')
        lines.append('')
        lines.append(f'1. **Answer the focus question** — What patterns in the data address "{focus}"?')
        lines.append('2. **Cross-entry synthesis** — How do multiple entries connect around this topic?')
        lines.append('3. **Knowledge gaps** — What does the KB NOT cover that would help answer this?')
        lines.append('4. **Contradictions** — Do any sources disagree on this topic?')
        lines.append('5. **Next steps** — What should the user explore, read, or capture next?')
    else:
        lines.append('## Your Task: Find Patterns and Propose Insights')
        lines.append('')
        lines.append('Analyze the journal entries, session logs, and referenced pages above. Look for:')
        lines.append('')
        lines.append('1. **Cross-entry patterns** — Multiple entries about the same concept, theme, or domain')
        lines.append('2. **Contradictions** — Entries or sources that make conflicting claims')
        lines.append('3. **Hidden connections** — Entries that connect previously unrelated wiki pages')
        lines.append('4. **Recurring questions** — Questions the user keeps asking (unresolved gaps)')
        lines.append('5. **Learning trajectories** — Topics the user is building depth in over time')
    lines.append('')
    lines.append('For each pattern found, propose an insight with:')
    lines.append('- **Title**: concise name for the insight')
    lines.append('- **Finding**: 2-3 sentence description of the pattern/connection')
    lines.append('- **Evidence**: which journal entries and wiki pages support it')
    lines.append('- **Suggested rules**: what the AI should do when future content relates to this insight')
    lines.append('')
    lines.append('IMPORTANT:')
    lines.append('- Do NOT duplicate existing insights listed above')
    lines.append('- Do NOT propose insights that just summarize individual pages (those are derivable)')
    lines.append('- DO propose insights that synthesize ACROSS sources or entries')
    lines.append('- Propose 1-3 insights. Quality over quantity.')
    lines.append('- Ask the user which insights to save before creating them')
    lines.append('')
    lines.append('To save approved insights, use: `kb_insight` with the title and body.')

    return '\n'.join(lines)
