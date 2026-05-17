"""Shared KB command implementations for Athena.

Single source of truth for: list, stats, rules, create.
Called by bin/kb (shell), MCP server, and Obsidian plugin.

Usage:
    python3 bin/lib/kb_commands.py list --vault /path [--filter repos] [--recent]
    python3 bin/lib/kb_commands.py stats --vault /path
    python3 bin/lib/kb_commands.py rules --vault /path [--section ingest] [--add "rule"]
    python3 bin/lib/kb_commands.py create --vault /path --name "Name" [--topic|--insight|--project]
"""

import sys
import os
import re
import json
import time
import glob

# Import shared functions from wiki_page
try:
    from wiki_page import update_index_counts
except ImportError:
    _lib_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _lib_dir)
    from wiki_page import update_index_counts


# ── LIST ─────────────────────────────────────────────────────────

def kb_list(vault_path, filter_type=None, recent=False):
    """List wiki pages. Returns structured data.

    Args:
        filter_type: None, "insight", "topic", "entity", "repo", "paper",
                     "video", "webpage", "image", "comparison", "journal",
                     "urls", "project"
        recent: if True, return last 20 by date

    Returns { pages: [{ name, source_type, date_added, summary }] }
    or specialized results for urls/journal/project filters.
    """
    wiki_dir = os.path.join(vault_path, 'wiki')

    # ── URLs filter ──
    if filter_type == 'urls':
        return _list_urls(vault_path)

    # ── Journal filter ──
    if filter_type == 'journal':
        return _list_journal(vault_path)

    # ── Project filter ──
    if filter_type == 'project':
        return _list_projects(vault_path)

    # ── Standard wiki page listing ──
    skip_dirs = {'dashboards', 'exports', 'sessions', 'feedback', 'keywords', 'journal', 'profile'}
    pages = []

    for root, dirs, files in os.walk(wiki_dir):
        rel = os.path.relpath(root, wiki_dir)
        parts = rel.split(os.sep)
        if any(p in skip_dirs for p in parts):
            continue
        for f in files:
            if not f.endswith('.md'):
                continue
            fpath = os.path.join(root, f)
            name = os.path.splitext(f)[0]

            # Parse frontmatter
            fm = {}
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as fh:
                    content = fh.read(4000)
                fm_match = re.match(r'^---\r?\n(.*?)\r?\n---', content, re.DOTALL)
                if fm_match:
                    # Simple YAML parsing (avoid yaml dependency)
                    for line in fm_match.group(1).split('\n'):
                        m = re.match(r'^(\w+):\s*"?(.+?)"?\s*$', line)
                        if m:
                            fm[m.group(1)] = m.group(2)
            except (UnicodeDecodeError, OSError):
                pass

            source_type = fm.get('source_type', '')
            date_added = fm.get('date_added', '')
            summary = fm.get('summary', '')[:80] if isinstance(fm.get('summary'), str) else ''

            if filter_type and source_type != filter_type:
                continue

            pages.append({
                'name': name,
                'source_type': source_type,
                'date_added': date_added,
                'summary': summary,
            })

    if recent:
        pages.sort(key=lambda p: p['date_added'] or '0000-00-00', reverse=True)
        pages = pages[:20]
    else:
        pages.sort(key=lambda p: p['name'].lower())

    return {'pages': pages, 'count': len(pages), 'filter': filter_type or 'all'}


def _list_urls(vault_path):
    """List tracked URLs from url-resolved.tsv."""
    resolved = os.path.join(vault_path, 'inbox', 'url-resolved.tsv')
    pending_path = os.path.join(vault_path, 'inbox', 'url-new.txt')

    entries = []
    if os.path.exists(resolved):
        with open(resolved, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 3:
                    entries.append({
                        'status': parts[0],
                        'title': parts[1],
                        'url': parts[2],
                        'date': parts[3] if len(parts) > 3 else '',
                    })

    if os.path.exists(pending_path):
        with open(pending_path, 'r', encoding='utf-8') as f:
            for line in f:
                url = line.strip()
                if url and url.startswith('http'):
                    entries.append({'status': 'pending', 'title': '', 'url': url, 'date': ''})

    from collections import Counter
    status_counts = dict(Counter(e['status'] for e in entries))

    return {
        'entries': entries,
        'count': len(entries),
        'status_counts': status_counts,
        'filter': 'urls',
    }


def _list_journal(vault_path):
    """List journal entry files."""
    journal_dir = os.path.join(vault_path, 'wiki', 'journal')
    if not os.path.isdir(journal_dir):
        return {'entries': [], 'count': 0, 'filter': 'journal'}
    files = sorted([f for f in os.listdir(journal_dir) if f.endswith('.md')], reverse=True)
    return {
        'entries': [os.path.splitext(f)[0] for f in files],
        'count': len(files),
        'filter': 'journal',
    }


def _list_projects(vault_path):
    """List project directories."""
    proj_dir = os.path.join(vault_path, 'projects')
    if not os.path.isdir(proj_dir):
        return {'projects': [], 'count': 0, 'filter': 'project'}

    projects = []
    for d in sorted(os.listdir(proj_dir)):
        full = os.path.join(proj_dir, d)
        if not os.path.isdir(full) or d.startswith('.'):
            continue
        index_path = os.path.join(full, 'index.md')
        proj = {'name': d, 'title': d, 'status': '', 'summary': ''}
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as fh:
                    content = fh.read(2000)
                sm = re.search(r'status:\s*"?(\w+)"?', content)
                if sm:
                    proj['status'] = sm.group(1)
                tm = re.search(r'title:\s*"([^"]*)"', content)
                if tm:
                    proj['title'] = tm.group(1)
                summ = re.search(r'summary:\s*"([^"]*)"', content)
                if summ:
                    proj['summary'] = summ.group(1)[:80]
            except Exception:
                pass
        projects.append(proj)

    return {'projects': projects, 'count': len(projects), 'filter': 'project'}


# ── STATS ────────────────────────────────────────────────────────

def _reconstruct_url_from_slug(rel_path):
    """Best-effort: derive the originating URL from a raw file's slug.
    Returns None when the slug gives no reliable clue (truncation, missing
    separators). Conservative: better to return None than an incorrect URL.
    """
    import re as _re
    fname = os.path.basename(rel_path)
    stem = os.path.splitext(fname)[0]
    # X / Twitter: x-com-<user>-status-<id>[-<query-junk>]
    m = _re.match(r'^(?:x|twitter)-com-([^-]+)-status-(\d+)', stem)
    if m:
        return f"https://x.com/{m.group(1)}/status/{m.group(2)}"
    # YouTube video: www-youtube-com-watch-v-<id>  OR  video-<id>
    m = _re.match(r'^www-youtube-com-watch-v-([^-]+)', stem)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    m = _re.match(r'^video-(.+)$', stem)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    # YouTube playlist (NEW format, case-preserved): playlist-<id>
    m = _re.match(r'^playlist-(.+)$', stem)
    if m:
        return f"https://www.youtube.com/playlist?list={m.group(1)}"
    # YouTube playlist (LEGACY, pre-2026-04-18): www-youtube-com-playlist-list-<id>
    # These were captured through the webpage slug fallback which lowercases.
    # IDs are case-sensitive, so the reconstructed URL usually 404s — flag it.
    m = _re.match(r'^www-youtube-com-playlist-list-(.+)$', stem)
    if m:
        pid = m.group(1)
        return (f"https://www.youtube.com/playlist?list={pid}  "
                f"# WARNING: case lost by slug — verify ID on YouTube")
    # GitHub: <owner>--<repo>
    if '--' in stem and not stem.startswith('video-') and not stem.startswith('arxiv-'):
        parts = stem.split('--', 1)
        if len(parts) == 2 and '-' not in parts[0][:1]:
            return f"https://github.com/{parts[0]}/{parts[1]}"
    # arXiv: arxiv-<id>
    m = _re.match(r'^arxiv-(.+)$', stem)
    if m:
        return f"https://arxiv.org/abs/{m.group(1)}"
    return None


def _extract_url_from_raw_body(full_path):
    """Walk the first ~40 lines of a raw file looking for an explicit URL.

    Preferred because it preserves case (important for YouTube playlist IDs,
    tweet IDs, etc.) — slugs in filenames are lowercased, which makes
    reconstructed URLs 404 for case-sensitive hosts.

    Priority order for detection:
      1. `source: "https://..."` or `url: "https://..."` (YAML-style, from embedded frontmatter).
         `source:` is what wiki_schema.write_raw_page emits; `url:` covers legacy raws.
      2. `- **URL:** https://...` (old-format raw bullet)
      3. First bare `https://...` line that matches a plausible host
    Returns None if nothing found.
    """
    import re as _re
    try:
        with open(full_path, encoding='utf-8', errors='replace') as fh:
            head = fh.read(8000)
    except OSError:
        return None
    # YAML-style frontmatter URL — `source:` (canonical writer) or `url:` (legacy).
    m = _re.search(r'^\s*(?:source|url):\s*"?(https?://[^\s"]+)"?\s*$', head, _re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Legacy URL bullet
    m = _re.search(r'^\s*-\s+\*\*URL:\*\*\s+(https?://\S+)', head, _re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Bare URL (first one that's not an image/favicon)
    for m in _re.finditer(r'(https?://[^\s)\]"<>]+)', head):
        u = m.group(1)
        if any(skip in u for skip in ('pbs.twimg', '/favicons/', '/assets/')):
            continue
        return u
    return None


def _find_orphan_raws(vault_path):
    """Return a list of dicts: {rel_path, inferred_url, source} for raw files
    not referenced by any wiki page.

    `source` indicates where the URL came from:
      - "body" — pulled from the raw file's content (case-preserved, reliable)
      - "slug" — reconstructed from the filename (case lost for some hosts)
      - None — no URL could be derived
    """
    import glob as _glob
    from config import raw_categories, raw_dir

    # Build a set of everything wiki pages reference
    wiki_references = ""
    for wf in _glob.glob(os.path.join(vault_path, 'wiki', '**', '*.md'), recursive=True):
        try:
            with open(wf, encoding='utf-8', errors='replace') as fh:
                wiki_references += fh.read(4000) + "\n"
        except OSError:
            continue

    orphans = []
    for cat_name in raw_categories():
        dir_path = os.path.join(vault_path, raw_dir(cat_name))
        if not os.path.isdir(dir_path):
            continue
        for fname in sorted(os.listdir(dir_path)):
            if fname.startswith('_') or fname.startswith('.') or fname == '.gitkeep':
                continue
            if not fname.endswith('.md'):
                continue
            stem = os.path.splitext(fname)[0]
            # Skip common Twitter-image junk that's never meant to be wiki'd
            if 'pbs-twimg' in stem or '-format-jpg' in stem:
                continue
            if stem in wiki_references:
                continue
            full_path = os.path.join(dir_path, fname)
            rel = os.path.relpath(full_path, vault_path)
            # X/Twitter posts: prefer slug-derived URL. The body scan would
            # return the FIRST https:// URL, which is often a link cited
            # inside the tweet (not the tweet itself). The slug encodes the
            # actual tweet URL (user + tweet ID) unambiguously.
            is_tweet = stem.startswith('x-com-') or stem.startswith('twitter-com-')
            slug_url = _reconstruct_url_from_slug(rel)
            if is_tweet and slug_url:
                orphans.append({'rel_path': rel, 'inferred_url': slug_url, 'source': 'slug'})
                continue
            body_url = _extract_url_from_raw_body(full_path)
            if body_url:
                orphans.append({'rel_path': rel, 'inferred_url': body_url, 'source': 'body'})
            else:
                orphans.append({
                    'rel_path': rel,
                    'inferred_url': slug_url,
                    'source': 'slug' if slug_url else None,
                })
    return orphans


def kb_stats(vault_path):
    """Gather knowledge base statistics. Returns structured data."""
    from config import raw_dir_for_source_type
    def count_md(d):
        if not os.path.isdir(d):
            return 0
        return len([f for f in os.listdir(d)
                     if f.endswith('.md') and not f.startswith('_') and f != '.gitkeep'])

    def count_pdf(d):
        if not os.path.isdir(d):
            return 0
        return len([f for f in os.listdir(d) if f.endswith('.pdf')])

    # Raw sources — resolve paths through config so the artifacts subdir
    # is picked up automatically.
    raw = {
        'webpages': count_md(os.path.join(vault_path, raw_dir_for_source_type('webpage'))),
        'repos':    count_md(os.path.join(vault_path, raw_dir_for_source_type('repo'))),
        'papers':   count_md(os.path.join(vault_path, raw_dir_for_source_type('paper'))),
        'videos':   count_md(os.path.join(vault_path, raw_dir_for_source_type('video'))),
        'images':   count_md(os.path.join(vault_path, raw_dir_for_source_type('image'))),
        'pdfs':     count_pdf(os.path.join(vault_path, raw_dir_for_source_type('paper'))),
    }
    raw['total'] = raw['webpages'] + raw['repos'] + raw['papers'] + raw['videos'] + raw['images']

    # Orphan raws — raw files not referenced by any wiki page. Reconstruct
    # clickable URLs from the slug so the user can re-clip via Web Clipper
    # (which has the authenticated browser session X and some other sites
    # require) or `kb add <url>`.
    orphans = _find_orphan_raws(vault_path)

    # Wiki pages
    wiki = {
        'webpages': count_md(os.path.join(vault_path, 'wiki', 'format', 'webpages')),
        'repos': count_md(os.path.join(vault_path, 'wiki', 'format', 'repos')),
        'papers': count_md(os.path.join(vault_path, 'wiki', 'format', 'papers')),
        'videos': count_md(os.path.join(vault_path, 'wiki', 'format', 'videos')),
        'images': count_md(os.path.join(vault_path, 'wiki', 'format', 'images')),
        'topics': count_md(os.path.join(vault_path, 'wiki', 'topics')),
        'entities': count_md(os.path.join(vault_path, 'wiki', 'entities')),
        'insights': count_md(os.path.join(vault_path, 'wiki', 'insights')),
        'comparisons': count_md(os.path.join(vault_path, 'wiki', 'comparisons')),
    }
    wiki['total'] = sum(wiki.values())

    # Ingestion stats
    ingestion = {'ingested': 0, 'captured': 0, 'skipped': 0, 'removed': 0, 'derived': 0, 'pending': 0}
    tsv = os.path.join(vault_path, 'inbox', 'url-resolved.tsv')
    if os.path.exists(tsv):
        with open(tsv, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) < 2:
                    continue
                ingestion['ingested'] += 1
                s = parts[0]
                if s in ('captured', 'has-content'):
                    ingestion['captured'] += 1
                elif s == 'skipped':
                    ingestion['skipped'] += 1
                elif s == 'removed':
                    ingestion['removed'] += 1

    # Auto-discovered repos
    repos_dir = os.path.join(vault_path, 'raw', 'repos')
    if os.path.isdir(repos_dir):
        for f in os.listdir(repos_dir):
            if not f.endswith('.md'):
                continue
            try:
                with open(os.path.join(repos_dir, f), 'r', encoding='utf-8') as fh:
                    head = fh.read(300)
                if 'Discovered via' in head or 'discovered' in head.lower():
                    ingestion['derived'] += 1
            except Exception:
                pass

    # Pending
    url_new = os.path.join(vault_path, 'inbox', 'url-new.txt')
    if os.path.exists(url_new):
        with open(url_new, 'r', encoding='utf-8') as f:
            ingestion['pending'] = len([l for l in f if l.strip().startswith('http')])

    return {'raw': raw, 'wiki': wiki, 'ingestion': ingestion, 'orphans': orphans}


# ── RULES ────────────────────────────────────────────────────────

def kb_rules(vault_path, section=None):
    """Read processing rules from RULES.md. Returns structured data."""
    rules_path = os.path.join(vault_path, 'RULES.md')
    if not os.path.exists(rules_path):
        return {'sections': [], 'error': 'No RULES.md found.'}

    with open(rules_path, 'r', encoding='utf-8') as f:
        content = f.read()

    sections = re.split(r'^## ', content, flags=re.MULTILINE)
    result = []

    for s in sections[1:]:  # skip header
        title = s.split('\n')[0].strip()
        body = '\n'.join(s.split('\n')[1:]).strip()
        body = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL).strip()
        rules = [line.strip() for line in body.split('\n') if line.strip().startswith('- ')]

        if section and section.lower() not in title.lower():
            continue

        result.append({'title': title, 'rules': rules})

    return {'sections': result}


def kb_rules_add(vault_path, rule_text):
    """Add a rule to RULES.md. Auto-detects the best section."""
    rules_path = os.path.join(vault_path, 'RULES.md')
    if not os.path.exists(rules_path):
        return {'status': 'error', 'message': 'No RULES.md found.'}

    with open(rules_path, 'r', encoding='utf-8') as f:
        content = f.read()

    section_map = {
        'Ingest Rules': ['ingest', 'extract', 'skip', 'process', 'capture', 'download'],
        'Tagging Rules': ['tag', 'label', 'categorize', 'classify'],
        'Priority Rules': ['priority', 'rank', 'important', 'weight', 'star'],
        'Quality Rules': ['quality', 'format', 'length', 'detail', 'summary'],
        'Connection Rules': ['link', 'connect', 'reference', 'related', 'cross'],
        'Domain Rules': ['term', 'define', 'vocabulary', 'acronym', 'agent', 'context'],
    }

    best_section = 'Ingest Rules'
    best_score = 0
    rule_lower = rule_text.lower()
    for sec, keywords in section_map.items():
        score = sum(1 for kw in keywords if kw in rule_lower)
        if score > best_score:
            best_score = score
            best_section = sec

    lines = content.split('\n')
    section_found = False
    insert_idx = None
    for i, line in enumerate(lines):
        if line.startswith(f'## {best_section}'):
            section_found = True
            continue
        if section_found:
            if line.startswith('## '):
                insert_idx = i
                break
            if line.strip().startswith('- '):
                insert_idx = i + 1

    if insert_idx is None and section_found:
        insert_idx = len(lines)

    if insert_idx:
        lines.insert(insert_idx, f'- {rule_text}')
        with open(rules_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return {'status': 'added', 'section': best_section, 'rule': rule_text}
    else:
        return {'status': 'error', 'message': f"Could not find section '{best_section}'"}


# ── CREATE ───────────────────────────────────────────────────────

def kb_create(vault_path, name, page_type='topic', description='', goal=''):
    """Create a hub/topic/insight/project page.

    Args:
        page_type: 'topic', 'insight', or 'project'

    Returns { status, file_path, name }.
    """
    if not name:
        return {'status': 'error', 'message': 'Name required'}
    if '..' in name or '/' in name or '\\' in name:
        return {'status': 'error', 'message': f'Name contains unsafe characters: {name}'}

    # Check for duplicates
    for search_dir in [os.path.join(vault_path, 'wiki'), os.path.join(vault_path, 'projects')]:
        if not os.path.isdir(search_dir):
            continue
        for f in glob.glob(os.path.join(search_dir, '**', '*.md'), recursive=True):
            if os.path.splitext(os.path.basename(f))[0] == name:
                return {'status': 'exists', 'message': f"Page '{name}' already exists",
                        'file_path': os.path.relpath(f, vault_path)}

    today = time.strftime('%Y-%m-%d')
    summary = description or f'Hub page for {name}.'

    if page_type == 'project':
        proj_slug = re.sub(r'[^\w\s-]', '', name).strip().lower()
        proj_slug = re.sub(r'[-\s]+', '-', proj_slug)[:60]
        out_dir = os.path.join(vault_path, 'projects', proj_slug)
        out_path = os.path.join(out_dir, 'index.md')
        os.makedirs(out_dir, exist_ok=True)
        project_goal = goal or description or f'Goal for {name}.'
        content = f'''---
title: "{name}"
source_type: "project"
status: "active"
started: {today}
date_added: {today}
last_updated: {today}
tags: [project]
related:
summary: "{summary}"
---

## Goal

{project_goal}

## Phases

| Phase | Status | Description |
|---|---|---|
| Phase 1 | active | |

## Tasks

- [ ] Define project scope
- [ ] Gather resources

## Resources

Wiki pages that inform this project:

## Notes

## Connections
'''
    elif page_type == 'insight':
        out_dir = os.path.join(vault_path, 'wiki', 'insights')
        out_path = os.path.join(out_dir, f'{name}.md')
        os.makedirs(out_dir, exist_ok=True)
        content = f'''---
title: "{name}"
source_type: "insight"
date_added: {today}
last_updated: {today}
tags: [insight]
related:
summary: "{summary}"
---

{description}

## Insights

| Insight | Summary |
|---|---|

## Themes

<!-- What patterns connect these insights? -->

## Keywords
[[insight]]
'''
    else:
        # topic (default)
        out_dir = os.path.join(vault_path, 'wiki', 'topics')
        out_path = os.path.join(out_dir, f'{name}.md')
        os.makedirs(out_dir, exist_ok=True)
        tags = 'topic' if page_type == 'topic' else ''
        content = f'''---
title: "{name}"
source_type: "topic"
date_added: {today}
last_updated: {today}
tags: [{tags}]
related:
summary: "{summary}"
---

{description}

## Resources

| Resource | Type | Tags |
|---|---|---|

## Keywords
[[{name.lower()}]]
'''

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)

    update_index_counts(vault_path)
    return {'status': 'created', 'file_path': os.path.relpath(out_path, vault_path), 'name': name}


# ── CLI entry point ──────────────────────────────────────────────

def _format_list(data):
    """Format list result for terminal output."""
    filt = data.get('filter', 'all')
    if filt == 'urls':
        entries = data.get('entries', [])
        if not entries:
            print('No URLs tracked yet.')
            return
        from collections import Counter
        sc = Counter(e['status'] for e in entries)
        print(f"{len(entries)} URLs tracked:\n")
        icons = {'captured': '\u2713', 'thin': '\u26A0', 'uncapturable': '\u2717', 'pending': '\u23F3'}
        for s in ['captured', 'thin', 'uncapturable', 'pending']:
            c = sc.get(s, 0)
            if c:
                print(f"  {icons.get(s, '?')} {s}: {c}")
        for label, status in [('Thin captures', 'thin'), ('Uncapturable', 'uncapturable'), ('Pending', 'pending')]:
            items = [e for e in entries if e['status'] == status]
            if items:
                print(f"\n  {label}:")
                for e in items:
                    print(f"    {e['title'] or e['url'][:60]}")
    elif filt == 'journal':
        entries = data.get('entries', [])
        if not entries:
            print('No journal entries yet.')
            return
        print(f"{len(entries)} journal entries:\n")
        for e in entries:
            print(f"  {e}")
    elif filt == 'project':
        projects = data.get('projects', [])
        if not projects:
            print('No projects yet.')
            return
        print(f"{len(projects)} projects:\n")
        for p in projects:
            meta = f"  ({p['status']})" if p.get('status') else ''
            print(f"  {p['title']}{meta}")
            if p.get('summary'):
                print(f"    {p['summary']}")
    else:
        pages = data.get('pages', [])
        if not pages:
            label = filt if filt != 'all' else 'wiki'
            print(f'No {label} pages found.')
            return
        label = f"{filt} pages" if filt != 'all' else 'wiki pages'
        print(f"{len(pages)} {label}:\n")
        for p in pages:
            line = f"  {p['name']}"
            meta = []
            if p.get('source_type') and filt == 'all':
                meta.append(p['source_type'])
            if p.get('date_added'):
                meta.append(p['date_added'])
            if meta:
                line += f"  ({', '.join(meta)})"
            print(line)
            if p.get('summary'):
                print(f"    {p['summary']}")


def _format_stats(data):
    """Format stats result for terminal output."""
    raw = data['raw']
    wiki = data['wiki']
    ing = data['ingestion']
    print('Knowledge Base Stats')
    print('=' * 40)
    print('\n  Raw Sources')
    print(f"    Webpages:     {raw['webpages']}")
    print(f"    Repos:        {raw['repos']}")
    print(f"    Papers:       {raw['papers']} (+ {raw['pdfs']} PDFs)")
    print(f"    Videos:       {raw['videos']}")
    if raw['images']:
        print(f"    Images:       {raw['images']}")
    print(f"    Total:        {raw['total']}")
    print('\n  Wiki Pages')
    print(f"    Webpages:     {wiki['webpages']}")
    print(f"    Repos:        {wiki['repos']}")
    print(f"    Papers:       {wiki['papers']}")
    print(f"    Videos:       {wiki['videos']}")
    print(f"    Topics:       {wiki['topics']}")
    if wiki['entities']:
        print(f"    Entities:     {wiki['entities']}")
    if wiki['insights']:
        print(f"    Insights:     {wiki['insights']}")
    if wiki['comparisons']:
        print(f"    Comparisons:  {wiki['comparisons']}")
    print(f"    Total:        {wiki['total']}")
    print('\n  Ingestion')
    print(f"    URLs ingested:  {ing['ingested']}")
    print(f"    Captured:       {ing['captured']}")
    if ing['skipped']:
        print(f"    Skipped (dup):  {ing['skipped']}")
    if ing['removed']:
        print(f"    Removed:        {ing['removed']}")
    if ing['derived']:
        print(f"    Auto-discovered repos: {ing['derived']}")
    if ing['pending']:
        print(f"    Pending:        {ing['pending']}")

    orphans = data.get('orphans') or []
    if orphans:
        print(f"\n  Orphan Raw Files — {len(orphans)} (no wiki page)")
        print(f"    `kb lint` will synthesize wiki pages from these on next run.")
        print(f"    Or recapture via Web Clipper / `kb add <url>`:\n")
        for o in orphans:
            url = o.get('inferred_url')
            source = o.get('source')
            if url and source == 'body':
                print(f"    {url}  # from raw body (case-preserved)")
            elif url and source == 'slug':
                hint = "  # case may be lost in slug" if 'playlist' in o['rel_path'] else ""
                print(f"    {url}{hint}")
            else:
                print(f"    # (cannot derive URL)  {o['rel_path']}")


def _format_rules(data):
    """Format rules result for terminal output."""
    if data.get('error'):
        print(data['error'])
        return
    sections = data.get('sections', [])
    if not sections:
        print('No rules found.')
        return
    print('Current Processing Rules')
    print('=' * 40)
    for s in sections:
        print(f"\n## {s['title']}")
        if s['rules']:
            for r in s['rules']:
                print(f"  {r}")
        else:
            print('  (no rules yet)')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Athena KB commands')
    subparsers = parser.add_subparsers(dest='command', help='Command')

    # list
    lp = subparsers.add_parser('list')
    lp.add_argument('--vault', required=True)
    lp.add_argument('--filter', default=None)
    lp.add_argument('--recent', action='store_true')
    lp.add_argument('--json', action='store_true', help='Output raw JSON')

    # stats
    sp = subparsers.add_parser('stats')
    sp.add_argument('--vault', required=True)
    sp.add_argument('--json', action='store_true')

    # rules
    rp = subparsers.add_parser('rules')
    rp.add_argument('--vault', required=True)
    rp.add_argument('--section', default=None)
    rp.add_argument('--add', default=None)
    rp.add_argument('--json', action='store_true')

    # create
    cp = subparsers.add_parser('create')
    cp.add_argument('--vault', required=True)
    cp.add_argument('--name', required=True)
    cp.add_argument('--type', default='topic', choices=['topic', 'insight', 'project'])
    cp.add_argument('--desc', default='')
    cp.add_argument('--goal', default='')
    cp.add_argument('--json', action='store_true')

    args = parser.parse_args()

    if args.command == 'list':
        result = kb_list(args.vault, filter_type=args.filter, recent=args.recent)
        if args.json:
            print(json.dumps(result))
        else:
            _format_list(result)

    elif args.command == 'stats':
        result = kb_stats(args.vault)
        if args.json:
            print(json.dumps(result))
        else:
            _format_stats(result)

    elif args.command == 'rules':
        if args.add:
            result = kb_rules_add(args.vault, args.add)
            if args.json:
                print(json.dumps(result))
            else:
                if result['status'] == 'added':
                    print(f"Added to {result['section']}:")
                    print(f"  - {result['rule']}")
                else:
                    print(f"Error: {result.get('message', 'Unknown error')}")
        else:
            result = kb_rules(args.vault, section=args.section)
            if args.json:
                print(json.dumps(result))
            else:
                _format_rules(result)

    elif args.command == 'create':
        result = kb_create(args.vault, args.name, page_type=args.type,
                           description=args.desc, goal=args.goal)
        if args.json:
            print(json.dumps(result))
        else:
            if result['status'] == 'created':
                print(f"Created: {result['file_path']}")
            elif result['status'] == 'exists':
                print(f"Error: {result['message']}")
            else:
                print(f"Error: {result.get('message', 'Unknown error')}")

    else:
        parser.print_help()
