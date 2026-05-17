"""Tier 2 export renderers — matplotlib, plotly, Marp.

All imports are lazy. If a dependency is missing, returns a helpful
install message instead of crashing.

Usage:
    from export_tier2 import render_bar_png, render_pie_png, render_chart_html, render_slides_pptx
"""

import os
import subprocess
import time


# ── Availability checks ────────────────────────────────────────────

def _check_matplotlib():
    try:
        import matplotlib
        return True, None
    except ImportError:
        return False, 'pip install athena-brain[export]'


def _check_plotly():
    try:
        import plotly
        return True, None
    except ImportError:
        return False, 'pip install athena-brain[export]'


def _check_marp():
    try:
        result = subprocess.run(['marp', '--version'], capture_output=True, text=True, timeout=5)
        return result.returncode == 0, None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, 'npm install -g @marp-team/marp-cli'


# ── matplotlib renderers ───────────────────────────────────────────

def render_bar_png(data, title='', output_path=None, options=None):
    """Render a bar chart as PNG using matplotlib.

    Args:
        data: list of {'label': str, 'value': number}
        title: chart title
        output_path: where to save the PNG
        options: dict with 'sort_by', 'max_items', 'color'
    Returns (success, path_or_error)
    """
    ok, install_cmd = _check_matplotlib()
    if not ok:
        return False, f'matplotlib not installed. Run: {install_cmd}'

    import matplotlib
    matplotlib.use('Agg')  # non-interactive backend
    import matplotlib.pyplot as plt

    options = options or {}
    max_items = options.get('max_items', 15)
    sort_by = options.get('sort_by', 'value')
    color = options.get('color', '#4A90D9')

    if sort_by == 'value':
        data = sorted(data, key=lambda d: d.get('value', 0), reverse=True)
    data = data[:max_items]

    if not data:
        return False, 'No data for chart'

    labels = [d['label'] for d in data]
    values = [d.get('value', 0) for d in data]

    fig, ax = plt.subplots(figsize=(10, max(4, len(data) * 0.4)))
    bars = ax.barh(range(len(labels)), values, color=color)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    if title:
        ax.set_title(title, fontsize=14, fontweight='bold')
    ax.set_xlabel('Count')

    # Add value labels on bars
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                str(val), va='center', fontsize=9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return True, output_path


def render_pie_png(data, title='', output_path=None):
    """Render a pie chart as PNG using matplotlib.

    Args:
        data: list of (label, count) tuples
    Returns (success, path_or_error)
    """
    ok, install_cmd = _check_matplotlib()
    if not ok:
        return False, f'matplotlib not installed. Run: {install_cmd}'

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    if not data:
        return False, 'No data for chart'

    labels = [d[0] for d in data]
    sizes = [d[1] for d in data]
    colors = plt.cm.Set3([i / len(labels) for i in range(len(labels))])

    fig, ax = plt.subplots(figsize=(8, 8))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, autopct='%1.0f%%',
        colors=colors, startangle=90
    )
    for text in autotexts:
        text.set_fontsize(9)
    if title:
        ax.set_title(title, fontsize=14, fontweight='bold')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return True, output_path


def render_heatmap_png(data, title='', output_path=None):
    """Render a feature matrix heatmap as PNG.

    Args:
        data: list of dicts with 'feature' key and competitor columns
              Values: 'Strong'=3, 'Adequate'=2, 'Weak'=1, 'Absent'=0
    Returns (success, path_or_error)
    """
    ok, install_cmd = _check_matplotlib()
    if not ok:
        return False, f'matplotlib not installed. Run: {install_cmd}'

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    if not data:
        return False, 'No data for heatmap'

    score_map = {
        'strong': 3, 'adequate': 2, 'weak': 1, 'absent': 0,
        'yes': 3, 'partial': 2, 'no': 0,
    }

    features = [d.get('feature', d.get('Feature', '')) for d in data]
    columns = [k for k in data[0] if k.lower() not in ('feature', 'capability')]

    matrix = []
    for row in data:
        row_vals = []
        for col in columns:
            val = str(row.get(col, '')).lower().strip()
            row_vals.append(score_map.get(val, 1))
        matrix.append(row_vals)

    fig, ax = plt.subplots(figsize=(max(8, len(columns) * 1.5), max(6, len(features) * 0.4)))
    cmap = mcolors.ListedColormap(['#FF6B6B', '#FFA07A', '#FFD700', '#90EE90'])
    im = ax.imshow(matrix, cmap=cmap, aspect='auto', vmin=0, vmax=3)

    ax.set_xticks(range(len(columns)))
    ax.set_xticklabels(columns, rotation=45, ha='right')
    ax.set_yticks(range(len(features)))
    ax.set_yticklabels(features)

    # Add text annotations
    labels = {0: 'Absent', 1: 'Weak', 2: 'OK', 3: 'Strong'}
    for i in range(len(features)):
        for j in range(len(columns)):
            ax.text(j, i, labels.get(matrix[i][j], ''), ha='center', va='center', fontsize=8)

    if title:
        ax.set_title(title, fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    return True, output_path


# ── plotly renderers ───────────────────────────────────────────────

def render_chart_html(chart_type, data, title='', output_path=None):
    """Render an interactive chart as self-contained HTML using plotly.

    Args:
        chart_type: 'bar', 'pie', 'timeline', 'network'
        data: varies by chart type
        output_path: where to save the HTML
    Returns (success, path_or_error)
    """
    ok, install_cmd = _check_plotly()
    if not ok:
        return False, f'plotly not installed. Run: {install_cmd}'

    import plotly.graph_objects as go

    if chart_type == 'bar':
        # data: list of {'label': str, 'value': number}
        labels = [d['label'] for d in data]
        values = [d.get('value', 0) for d in data]
        fig = go.Figure(go.Bar(x=values, y=labels, orientation='h'))
        fig.update_layout(title=title, yaxis=dict(autorange='reversed'))

    elif chart_type == 'pie':
        # data: list of (label, count)
        labels = [d[0] for d in data]
        values = [d[1] for d in data]
        fig = go.Figure(go.Pie(labels=labels, values=values))
        fig.update_layout(title=title)

    elif chart_type == 'timeline':
        # data: list of {'date': str, 'title': str}
        dates = [d['date'] for d in data]
        titles = [d['title'] for d in data]
        fig = go.Figure(go.Scatter(x=dates, y=list(range(len(dates))),
                                   mode='markers+text', text=titles,
                                   textposition='top center'))
        fig.update_layout(title=title, showlegend=False,
                         yaxis=dict(visible=False))

    elif chart_type == 'network':
        # data: {'nodes': [...], 'edges': [...]}
        nodes = data.get('nodes', [])
        edges = data.get('edges', [])
        # Simple force-directed layout approximation
        import math
        pos = {}
        for i, n in enumerate(nodes):
            angle = 2 * math.pi * i / max(len(nodes), 1)
            pos[n['id']] = (math.cos(angle), math.sin(angle))

        edge_x, edge_y = [], []
        for e in edges:
            if e['source'] in pos and e['target'] in pos:
                x0, y0 = pos[e['source']]
                x1, y1 = pos[e['target']]
                edge_x.extend([x0, x1, None])
                edge_y.extend([y0, y1, None])

        node_x = [pos[n['id']][0] for n in nodes if n['id'] in pos]
        node_y = [pos[n['id']][1] for n in nodes if n['id'] in pos]
        node_text = [n.get('label', n['id']) for n in nodes if n['id'] in pos]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode='lines',
                                 line=dict(width=0.5, color='#888'), hoverinfo='none'))
        fig.add_trace(go.Scatter(x=node_x, y=node_y, mode='markers+text',
                                 text=node_text, textposition='top center',
                                 marker=dict(size=10, color='#4A90D9')))
        fig.update_layout(title=title, showlegend=False,
                         xaxis=dict(visible=False), yaxis=dict(visible=False))
    else:
        return False, f'Unknown chart type: {chart_type}'

    fig.write_html(output_path, include_plotlyjs=True)
    return True, output_path


# ── Marp slide renderer ────────────────────────────────────────────

def render_slides_marp(input_md, output_path, fmt='pptx'):
    """Convert markdown slides to PPTX/PDF/HTML using Marp CLI.

    Args:
        input_md: path to the markdown slides file (Obsidian Slides format)
        output_path: where to save the output
        fmt: 'pptx', 'pdf', or 'html'
    Returns (success, path_or_error)
    """
    ok, install_cmd = _check_marp()
    if not ok:
        return False, f'Marp CLI not installed. Run: {install_cmd}'

    fmt_flag = f'--{fmt}'
    try:
        result = subprocess.run(
            ['marp', input_md, fmt_flag, '-o', output_path],
            capture_output=True, text=True, timeout=30,
            env={**os.environ,
                 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:' + os.environ.get('PATH', '')}
        )
        if result.returncode == 0:
            return True, output_path
        else:
            return False, f'Marp error: {result.stderr[:200]}'
    except subprocess.TimeoutExpired:
        return False, 'Marp timed out'
    except (OSError, subprocess.SubprocessError) as e:
        return False, f'Marp failed: {e}'
