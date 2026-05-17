"""Tier 1 export templates — Mermaid charts, Obsidian Slides, CSV/JSON.

All templates produce markdown that renders natively in Obsidian.
No external dependencies required.

Usage:
    from export_templates import render_comparison_bar, render_distribution_pie
"""

import os
import re
import csv
import json
import time
import io


def render_comparison_bar(data, title='', options=None):
    """Render a Mermaid xychart-beta bar chart.

    Args:
        data: list of {'label': str, 'value': number}
        title: chart title
        options: dict with 'max_items' (default 15), 'sort_by' ('value'|'name')
    """
    options = options or {}
    max_items = options.get('max_items', 15)
    sort_by = options.get('sort_by', 'value')

    if sort_by == 'value':
        data = sorted(data, key=lambda d: d.get('value', 0), reverse=True)
    elif sort_by == 'name':
        data = sorted(data, key=lambda d: d.get('label', ''))

    data = data[:max_items]
    if not data:
        return '*(No data for bar chart)*'

    # Truncate long labels for readability
    labels = [_truncate(d['label'], 30) for d in data]
    values = [d.get('value', 0) for d in data]
    max_val = max(values) if values else 1

    lines = ['```mermaid', 'xychart-beta']
    if title:
        lines.append(f'    title "{title}"')
    lines.append(f'    x-axis [{", ".join(json.dumps(l) for l in labels)}]')
    lines.append(f'    y-axis "Count" 0 --> {int(max_val * 1.1) + 1}')
    lines.append(f'    bar [{", ".join(str(v) for v in values)}]')
    lines.append('```')

    return '\n'.join(lines)


def render_distribution_pie(data, title=''):
    """Render a Mermaid pie chart.

    Args:
        data: list of (label, count) tuples
    """
    if not data:
        return '*(No data for pie chart)*'

    lines = ['```mermaid']
    if title:
        lines.append(f'pie title "{title}"')
    else:
        lines.append('pie')
    for label, count in data:
        lines.append(f'    "{label}" : {count}')
    lines.append('```')

    return '\n'.join(lines)


def render_feature_matrix(data, title=''):
    """Render a feature comparison matrix with emoji indicators.

    Args:
        data: list of dicts with 'feature' key and competitor columns
              Values should be 'Strong', 'Adequate', 'Weak', 'Absent'
    """
    if not data:
        return '*(No data for feature matrix)*'

    # Map qualitative labels to emoji
    emoji_map = {
        'strong': '🟢',
        'adequate': '🟡',
        'weak': '🟠',
        'absent': '🔴',
        'yes': '🟢',
        'no': '🔴',
        'partial': '🟡',
    }

    # Get all column names (excluding 'feature')
    columns = []
    for row in data:
        for k in row:
            if k.lower() not in ('feature', 'capability') and k not in columns:
                columns.append(k)

    lines = []
    if title:
        lines.append(f'**{title}**\n')

    # Header
    feature_col = 'Feature' if 'feature' in data[0] else 'Capability'
    lines.append(f'| {feature_col} | {" | ".join(columns)} |')
    lines.append(f'|---|{"---|" * len(columns)}')

    # Rows
    for row in data:
        feature = row.get('feature', row.get('Capability', row.get('Feature', '')))
        cells = []
        for col in columns:
            val = row.get(col, '')
            # Convert to emoji
            val_lower = str(val).lower().strip()
            emoji = emoji_map.get(val_lower, str(val))
            cells.append(emoji)
        lines.append(f'| {feature} | {" | ".join(cells)} |')

    return '\n'.join(lines)


def render_topic_slides(title, summary, sections, related=None):
    """Render Obsidian Slides format (reveal.js markdown).

    Args:
        title: slide deck title
        summary: overview text
        sections: list of {'heading': str, 'content': str}
        related: list of related page names
    """
    lines = []

    # Title slide
    lines.append(f'# {title}')
    if summary:
        lines.append('')
        lines.append(summary)
    lines.append('')
    lines.append('---')

    # Content slides — one per section
    for section in sections:
        lines.append('')
        heading = section.get('heading') or 'Overview'
        content = section.get('content', '')

        lines.append(f'## {heading}')
        lines.append('')

        # Convert content to bullet points for slide readability
        content_lines = content.strip().split('\n')
        for cl in content_lines[:12]:  # max 12 lines per slide
            cl = cl.strip()
            if cl and not cl.startswith('|'):  # skip table rows
                if not cl.startswith('- ') and not cl.startswith('* '):
                    cl = f'- {cl}'
                lines.append(cl)

        lines.append('')
        lines.append('---')

    # Related pages slide
    if related:
        lines.append('')
        lines.append('## Related')
        lines.append('')
        for r in related[:10]:
            lines.append(f'- [[{r}]]')
        lines.append('')
        lines.append('---')

    # Closing
    lines.append('')
    lines.append(f'## {title}')
    lines.append('')
    lines.append(f'*Generated {time.strftime("%Y-%m-%d")}*')

    return '\n'.join(lines)


def render_relationship_graph(nodes, edges, title='', max_nodes=20):
    """Render a Mermaid graph showing page relationships.

    Args:
        nodes: list of {'id': str, 'label': str, 'type': str}
        edges: list of {'source': str, 'target': str}
    """
    if not nodes:
        return '*(No data for relationship graph)*'

    # Limit nodes
    node_set = set()
    for n in nodes[:max_nodes]:
        node_set.add(n['id'])

    lines = ['```mermaid', 'graph LR']

    # Define nodes with type-based styling
    type_styles = {
        'repo': '([{label}])',
        'webpage': '[{label}]',
        'paper': '>{label}]',
        'video': '{{{{label}}}}',
        'topic': '(({label}))',
        'entity': '[[{label}]]',
        'insight': '[/{label}/]',
    }

    node_ids = {}
    for i, n in enumerate(nodes[:max_nodes]):
        nid = f'N{i}'
        node_ids[n['id']] = nid
        label = _truncate(n.get('label', n['id']), 25)
        style = type_styles.get(n.get('type', ''), '[{label}]')
        node_def = style.replace('{label}', label)
        lines.append(f'    {nid}{node_def}')

    # Define edges
    for e in edges:
        src = node_ids.get(e['source'])
        tgt = node_ids.get(e['target'])
        if src and tgt:
            lines.append(f'    {src} --> {tgt}')

    lines.append('```')

    return '\n'.join(lines)


def render_roadmap_gantt(milestones, title=''):
    """Render a Mermaid Gantt chart.

    Args:
        milestones: list of {'task': str, 'start': str, 'duration': str, 'status': str}
                    duration like '2w', '4w', '1m'
    """
    if not milestones:
        return '*(No milestone data)*'

    lines = ['```mermaid', 'gantt']
    if title:
        lines.append(f'    title {title}')
    lines.append('    dateFormat YYYY-MM-DD')

    for m in milestones:
        task = m.get('task', '')
        start = m.get('start', '')
        duration = m.get('duration', '2w')
        status = m.get('status', 'active')
        lines.append(f'    {task} :{status}, {start}, {duration}')

    lines.append('```')

    return '\n'.join(lines)


def render_timeline(events, title=''):
    """Render a Mermaid timeline.

    Args:
        events: list of {'date': str, 'title': str}
    """
    if not events:
        return '*(No timeline data)*'

    lines = ['```mermaid', 'timeline']
    if title:
        lines.append(f'    title {title}')

    # Group by date
    by_date = {}
    for e in events:
        d = e.get('date', 'Unknown')
        by_date.setdefault(d, []).append(e.get('title', ''))

    for date in sorted(by_date.keys()):
        lines.append(f'    {date}')
        for t in by_date[date]:
            lines.append(f'        : {_truncate(t, 40)}')

    lines.append('```')

    return '\n'.join(lines)


def render_stats_summary(stats, charts):
    """Render a multi-chart dashboard page.

    Args:
        stats: dict with summary numbers
        charts: list of {'title': str, 'content': str} — pre-rendered chart blocks
    """
    lines = [
        '---',
        'title: "Knowledge Base Dashboard"',
        'source_type: "topic"',
        'tags: [dashboard]',
        f'last_updated: {time.strftime("%Y-%m-%d")}',
        '---',
        '',
        '# Knowledge Base Dashboard',
        '',
        f'> Generated {time.strftime("%Y-%m-%d %H:%M")}',
        '',
    ]

    # Summary stats
    if stats:
        lines.append('## Overview')
        lines.append('')
        for key, value in stats.items():
            lines.append(f'- **{key}:** {value}')
        lines.append('')

    # Charts
    for chart in charts:
        lines.append(f'## {chart["title"]}')
        lines.append('')
        lines.append(chart['content'])
        lines.append('')

    return '\n'.join(lines)


def render_table_export(data, fmt='csv'):
    """Export data as CSV or JSON string.

    Args:
        data: list of dicts
        fmt: 'csv' or 'json'
    """
    if not data:
        return ''

    if fmt == 'json':
        return json.dumps(data, indent=2, default=str)

    # CSV
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    return output.getvalue()


def _truncate(text, max_len):
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + '...'
