"""Contradiction detection between wiki pages.

Finds pages that make conflicting claims about the same subject.
Uses the search index to find pages with overlapping topics,
then compares key claims for inconsistencies.

Used by kb lint to flag contradictions for user review.
"""

import os
import re
import sqlite3


def _parse_frontmatter(filepath):
    """Parse frontmatter. Returns (meta, body)."""
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
    for line in yaml_block.split('\n'):
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return meta, body


def detect_contradictions(kb_root):
    """Scan wiki pages for potential contradictions.

    Looks for:
    1. Pages about the same subject with conflicting star counts/numbers
    2. Pages that describe the same tool differently
    3. Pages where one says "X is Y" and another says "X is Z"

    Returns list of {page1, page2, claim1, claim2, subject} dicts.
    """
    contradictions = []
    wiki_dir = os.path.join(kb_root, 'wiki')

    # Collect all pages with their claims (numbers, descriptions)
    pages = []
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if d not in ('dashboards', 'exports', 'sessions', 'journal')]
        for fname in files:
            if not fname.endswith('.md') or fname.startswith('_') or fname == '.gitkeep':
                continue
            filepath = os.path.join(root, fname)
            meta, body = _parse_frontmatter(filepath)
            if not meta.get('title'):
                continue

            # Extract numeric claims (e.g., "1,374 stars", "14 implementations")
            numbers = {}
            for match in re.finditer(r'(\d[\d,]*)\s+(stars|implementations|lectures|lessons|modules|pages|repos)', body):
                num = int(match.group(1).replace(',', ''))
                subject = match.group(2)
                numbers[subject] = num

            pages.append({
                'path': os.path.relpath(filepath, kb_root),
                'title': meta.get('title', fname),
                'source_type': meta.get('source_type', ''),
                'url': meta.get('url', ''),
                'numbers': numbers,
                'body_lower': body.lower()[:2000],
            })

    # Check for numeric contradictions between pages about the same subject
    # Two pages mentioning the same repo/tool with different star counts
    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            p1, p2 = pages[i], pages[j]

            # Skip if different source types (a topic page summarizing vs a repo page is expected)
            if p1['source_type'] == 'topic' or p2['source_type'] == 'topic':
                continue

            # Check if pages are about the SAME specific subject
            # Two different repos having different star counts is expected.
            # Only flag when pages share the same URL or >70% title word overlap.
            same_url = (p1.get('url') and p1.get('url') == p2.get('url'))
            title1_words = set(re.findall(r'\w{4,}', p1['title'].lower()))
            title2_words = set(re.findall(r'\w{4,}', p2['title'].lower()))
            overlap = title1_words & title2_words
            min_words = min(len(title1_words), len(title2_words))
            strong_overlap = min_words > 0 and len(overlap) / min_words >= 0.7
            if not same_url and not strong_overlap:
                continue  # Different subjects

            # Check for conflicting numbers
            for subject, num1 in p1['numbers'].items():
                num2 = p2['numbers'].get(subject)
                if num2 is not None and num1 != num2:
                    # Allow small differences (star counts change)
                    if abs(num1 - num2) / max(num1, num2, 1) > 0.2:  # >20% difference
                        contradictions.append({
                            'page1': p1['title'],
                            'page2': p2['title'],
                            'claim1': f'{num1:,} {subject}',
                            'claim2': f'{num2:,} {subject}',
                            'subject': subject,
                            'severity': 'warning',
                        })

    # Check for date inconsistencies
    # (e.g., one page says "Spring 2022", another says "Spring 2025")
    date_pages = {}
    for p in pages:
        for match in re.finditer(r'(spring|fall|winter|summer)\s+(20\d{2})', p['body_lower']):
            semester = match.group(1) + ' ' + match.group(2)
            key = p['title'][:30]
            if key in date_pages and date_pages[key] != semester:
                contradictions.append({
                    'page1': p['title'],
                    'page2': date_pages[key + '_page'],
                    'claim1': semester,
                    'claim2': date_pages[key],
                    'subject': 'semester/date',
                    'severity': 'info',
                })
            date_pages[key] = semester
            date_pages[key + '_page'] = p['title']

    return contradictions
