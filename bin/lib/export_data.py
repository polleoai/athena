"""Data extraction from wiki pages for export/visualization.

Parses markdown tables, aggregates frontmatter metadata, extracts
page sections, and builds subgraphs from wikilinks.

Usage:
    from export_data import parse_markdown_table, aggregate_frontmatter, count_by_field
"""

import os
import re
import json


def _parse_frontmatter(filepath):
    """Parse YAML frontmatter. Returns (meta_dict, body_text)."""
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
            if value == '' and i + 1 < len(lines) and lines[i + 1].strip().startswith('-'):
                items = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith('-'):
                    item = lines[i].strip().lstrip('- ').strip().strip('"').strip("'")
                    items.append(item)
                    i += 1
                meta[key] = items
                continue
            elif value.startswith('[') and value.endswith(']'):
                items = [v.strip().strip('"').strip("'")
                         for v in value[1:-1].split(',') if v.strip()]
                meta[key] = items
            else:
                meta[key] = value.strip('"').strip("'")
        i += 1

    return meta, body


def parse_markdown_table(filepath, table_index=0, numeric_columns=None):
    """Parse a markdown table from a file.

    Args:
        filepath: path to the markdown file
        table_index: which table to parse (0-indexed)
        numeric_columns: list of column names to parse as numbers

    Returns list of dicts, one per row. Numeric columns have int/float values.
    """
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except (IOError, OSError):
        return []

    _, body = _parse_frontmatter(filepath)

    # Find all tables (blocks of lines starting with |)
    tables = []
    current_table = []
    for line in body.split('\n'):
        stripped = line.strip()
        if stripped.startswith('|') and '|' in stripped[1:]:
            current_table.append(stripped)
        else:
            if current_table:
                tables.append(current_table)
                current_table = []
    if current_table:
        tables.append(current_table)

    if table_index >= len(tables):
        return []

    table = tables[table_index]
    if len(table) < 3:  # need header + separator + at least one row
        return []

    # Parse header
    header = [cell.strip() for cell in table[0].split('|') if cell.strip()]
    # Skip separator row (table[1])

    # Parse data rows
    rows = []
    for row_line in table[2:]:
        cells = [cell.strip() for cell in row_line.split('|') if cell.strip()]
        if not cells:
            continue
        # Skip section header rows (e.g., "| **Standalone Agents** |")
        if len(cells) == 1 and cells[0].startswith('**'):
            continue

        row = {}
        for j, cell in enumerate(cells):
            if j < len(header):
                col_name = header[j]
                # Strip markdown formatting
                value = re.sub(r'\*\*([^*]+)\*\*', r'\1', cell)
                value = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', value)
                value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value)
                value = value.strip()

                # Parse as number if requested
                if numeric_columns and col_name in numeric_columns:
                    value = _parse_number(value)

                row[col_name] = value
        if row:
            rows.append(row)

    return rows


def _parse_number(text):
    """Parse a numeric value from text. Handles commas, K/M suffixes, ranges."""
    text = text.strip().replace(',', '')
    # Handle ranges like "125-361" — take the max
    range_match = re.match(r'(\d+)\s*[-–]\s*(\d+)', text)
    if range_match:
        return int(range_match.group(2))
    # Handle K/M suffixes
    km_match = re.match(r'([\d.]+)\s*([KkMm])', text)
    if km_match:
        num = float(km_match.group(1))
        suffix = km_match.group(2).upper()
        return int(num * 1000) if suffix == 'K' else int(num * 1000000)
    # Handle stars with star symbol
    text = text.replace('★', '').replace('⭐', '').strip()
    # Try int
    try:
        return int(text)
    except ValueError:
        pass
    # Try float
    try:
        return float(text)
    except ValueError:
        return 0


def aggregate_frontmatter(kb_root, base_path='wiki', filter_fn=None):
    """Walk wiki pages and collect frontmatter metadata.

    Args:
        kb_root: vault root path
        base_path: subdirectory to scan (default 'wiki')
        filter_fn: optional callable(meta_dict) -> bool to filter pages

    Returns list of metadata dicts with 'rel_path' added.
    """
    wiki_dir = os.path.join(kb_root, base_path)
    results = []

    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if d not in ('dashboards',)]
        for fname in files:
            if not fname.endswith('.md') or fname.startswith('_') or fname == '.gitkeep':
                continue
            filepath = os.path.join(root, fname)
            meta, _ = _parse_frontmatter(filepath)
            if not meta:
                continue
            meta['rel_path'] = os.path.relpath(filepath, kb_root)
            meta['filename'] = fname.replace('.md', '')
            if filter_fn is None or filter_fn(meta):
                results.append(meta)

    return results


def count_by_field(kb_root, field, base_path='wiki'):
    """Count pages grouped by a frontmatter field value.

    For array fields like tags, each element is counted independently.
    Returns sorted list of (value, count) tuples, descending by count.
    """
    pages = aggregate_frontmatter(kb_root, base_path)
    counts = {}
    for meta in pages:
        value = meta.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            for v in value:
                counts[v] = counts.get(v, 0) + 1
        else:
            counts[value] = counts.get(value, 0) + 1

    return sorted(counts.items(), key=lambda x: x[1], reverse=True)


def extract_page_sections(filepath):
    """Split a page at ## headings into sections.

    Returns list of {'heading': str|None, 'content': str, 'level': int}.
    """
    meta, body = _parse_frontmatter(filepath)
    if not body:
        return []

    sections = []
    parts = re.split(r'^(#{2,3}\s+.+)$', body, flags=re.MULTILINE)

    current_heading = None
    current_level = 0
    current_text = []

    for part in parts:
        heading_match = re.match(r'^(#{2,3})\s+(.+)$', part.strip())
        if heading_match:
            # Save previous section
            text = '\n'.join(current_text).strip()
            if text or current_heading:
                sections.append({
                    'heading': current_heading,
                    'content': text,
                    'level': current_level,
                })
            current_heading = heading_match.group(2).strip()
            current_level = len(heading_match.group(1))
            current_text = []
        else:
            current_text.append(part)

    # Save last section
    text = '\n'.join(current_text).strip()
    if text or current_heading:
        sections.append({
            'heading': current_heading,
            'content': text,
            'level': current_level,
        })

    return sections


def extract_wikilinks(filepath):
    """Extract all wikilink targets from a page. Returns list of page names."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except (IOError, OSError):
        return []

    links = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)
    seen = set()
    result = []
    for link in links:
        name = link.strip()
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def build_subgraph(kb_root, seed_pages, depth=1):
    """Build a subgraph from seed pages by traversing wikilinks.

    Returns {'nodes': [{'id': str, 'label': str, 'type': str}],
             'edges': [{'source': str, 'target': str}]}
    """
    # Build page name -> filepath map
    page_map = {}
    wiki_dir = os.path.join(kb_root, 'wiki')
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if d not in ('dashboards',)]
        for fname in files:
            if fname.endswith('.md') and not fname.startswith('_'):
                name = fname.replace('.md', '')
                page_map[name] = os.path.join(root, fname)

    nodes = {}
    edges = set()
    frontier = set(seed_pages)
    visited = set()

    for _ in range(depth + 1):
        next_frontier = set()
        for page_name in frontier:
            if page_name in visited:
                continue
            visited.add(page_name)

            filepath = page_map.get(page_name)
            if not filepath:
                continue

            meta, _ = _parse_frontmatter(filepath)
            nodes[page_name] = {
                'id': page_name,
                'label': meta.get('title', page_name),
                'type': meta.get('source_type', 'unknown'),
            }

            links = extract_wikilinks(filepath)
            for link in links:
                if link in page_map:
                    edges.add((page_name, link))
                    next_frontier.add(link)

        frontier = next_frontier - visited

    return {
        'nodes': list(nodes.values()),
        'edges': [{'source': s, 'target': t} for s, t in edges],
    }
