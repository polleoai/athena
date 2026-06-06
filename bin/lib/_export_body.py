import sys, os, time, json
sys.path.insert(0, sys.argv[1] + '/bin/lib')
from export_data import (parse_markdown_table, aggregate_frontmatter,
                         count_by_field, extract_page_sections, extract_wikilinks,
                         build_subgraph, _parse_frontmatter)
from export_templates import (render_comparison_bar, render_distribution_pie,
                              render_feature_matrix, render_topic_slides,
                              render_relationship_graph, render_roadmap_gantt,
                              render_timeline, render_stats_summary,
                              render_table_export)

kb_root = sys.argv[1]
args = sys.argv[2:]

if not args or args[0] in ('-h', '--help'):
    print("Usage: kb export <template> <source> [--format fmt] [--title title]")
    print()
    print("Templates:")
    print("  comparison_bar     Bar chart from a table with numbers")
    print("  distribution_pie   Pie chart from page type/tag counts")
    print("  feature_matrix     Feature comparison with emoji indicators")
    print("  topic_slides       Slide deck from a topic/insight page")
    print("  relationship_graph Network graph from wikilinks")
    print("  timeline           Timeline of when sources were added")
    print("  stats_summary      Multi-chart KB dashboard")
    print("  table_export       CSV or JSON export of page metadata")
    print()
    print("Sources:")
    print("  kb_stats           Aggregate from all pages")
    print("  tag:<tagname>      Filter by tag")
    print("  <page path>        Specific wiki page")
    print()
    print("Formats: mermaid (default), slides_md, csv, json")
    sys.exit(0)

template = args[0] if args else ''
if len(args) < 2:
    print("Error: source is required.")
    print("  kb export comparison_bar \"wiki/insights/LLM Wiki Market Analysis.md\"")
    print("  kb export distribution_pie tag:ml")
    print("  kb export topic_slides \"wiki/topics/AI Security.md\"")
    print("  kb export table_export tag:security --format csv")
    print("  kb export stats_summary kb_stats")
    print()
    print("Run 'kb export --help' for all templates.")
    sys.exit(1)
source = args[1]

# Parse flags
fmt = 'mermaid'
title = ''
i = 2
while i < len(args):
    if args[i] == '--format' and i + 1 < len(args):
        fmt = args[i + 1]; i += 2
    elif args[i] == '--title' and i + 1 < len(args):
        title = args[i + 1]; i += 2
    else:
        i += 1

# Output directory
export_dir = os.path.join(kb_root, 'wiki', 'exports')
os.makedirs(export_dir, exist_ok=True)


def write_export(content, slug, ext='md'):
    """Write export file and print result."""
    filepath = os.path.join(export_dir, f'{slug}.{ext}')
    with open(filepath, 'w') as f:
        f.write(content)
    rel = os.path.relpath(filepath, kb_root)
    print(f"  Exported to: {rel}")
    print(f"  Open in Obsidian to view.")
    return filepath

# Tier 2 output directory for binary files
tier2_dir = os.path.join(kb_root, '.athena', 'exports')

def write_tier2(data, slug, template_name, t2_fmt, title_str):
    """Route to Tier 2 renderer if format is png/html/pptx/pdf."""
    from export_tier2 import render_bar_png, render_pie_png, render_heatmap_png, render_chart_html, render_slides_marp
    os.makedirs(tier2_dir, exist_ok=True)

    if t2_fmt == 'png':
        out = os.path.join(tier2_dir, f'{slug}.png')
        if template_name == 'comparison_bar':
            ok, result = render_bar_png(data, title_str, out)
        elif template_name == 'distribution_pie':
            ok, result = render_pie_png(data, title_str, out)
        elif template_name == 'feature_matrix':
            ok, result = render_heatmap_png(data, title_str, out)
        else:
            ok, result = False, f'PNG export not supported for {template_name}'

    elif t2_fmt == 'html':
        out = os.path.join(tier2_dir, f'{slug}.html')
        chart_map = {
            'comparison_bar': 'bar', 'distribution_pie': 'pie',
            'timeline': 'timeline', 'relationship_graph': 'network',
        }
        chart_type = chart_map.get(template_name)
        if chart_type:
            ok, result = render_chart_html(chart_type, data, title_str, out)
        else:
            ok, result = False, f'HTML export not supported for {template_name}'

    elif t2_fmt in ('pptx', 'pdf'):
        # Marp: first generate slides markdown, then convert
        out = os.path.join(tier2_dir, f'{slug}.{t2_fmt}')
        # Need the slides markdown file — generate it first
        slides_md = os.path.join(export_dir, f'{slug}-slides.md')
        if not os.path.exists(slides_md):
            print(f'  Generate slides first: kb export topic_slides <source>')
            return False
        ok, result = render_slides_marp(slides_md, out, t2_fmt)
    else:
        ok, result = False, f'Unknown Tier 2 format: {t2_fmt}'

    if ok:
        rel = os.path.relpath(result, kb_root)
        print(f"  Exported to: {rel}")
    else:
        print(f"  {result}")
    return ok


if template == 'comparison_bar':
    # Source is a page with tables — parse the first table with numeric columns
    if source == 'kb_stats':
        # Bar chart of pages by source type
        counts = count_by_field(kb_root, 'source_type')
        data = [{'label': label, 'value': count} for label, count in counts]
        title = title or 'Pages by Source Type'
    else:
        # Parse table from page
        filepath = os.path.join(kb_root, source)
        if not os.path.exists(filepath):
            # Try finding by page name
            for root, dirs, files in os.walk(os.path.join(kb_root, 'wiki')):
                for f in files:
                    if source.lower() in f.lower():
                        filepath = os.path.join(root, f)
                        break
        rows = parse_markdown_table(filepath, table_index=0, numeric_columns=None)
        # Auto-detect numeric column
        data = []
        if rows:
            # Find first column with numbers
            for col in rows[0]:
                try:
                    vals = [r[col] for r in rows if r.get(col)]
                    if any(str(v).replace(',', '').replace('.', '').isdigit() for v in vals[:3]):
                        label_col = [c for c in rows[0] if c != col][0]
                        data = [{'label': str(r.get(label_col, '')), 'value': int(str(r.get(col, '0')).replace(',', ''))}
                                for r in rows if str(r.get(col, '')).replace(',', '').isdigit()]
                        title = title or col
                        break
                except (ValueError, IndexError):
                    continue

    slug = title.lower().replace(' ', '-')[:40] if title else 'comparison'
    if fmt in ('png', 'html'):
        write_tier2(data, slug, 'comparison_bar', fmt, title)
    else:
        chart = render_comparison_bar(data, title)
        page = f'---\ntitle: "{title}"\ntags: [export, chart]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{chart}\n'
        write_export(page, slug)

elif template == 'distribution_pie':
    if source == 'kb_stats':
        counts = count_by_field(kb_root, 'source_type')
        title = title or 'Pages by Source Type'
    elif source.startswith('tag:'):
        counts = count_by_field(kb_root, 'tags')
        title = title or 'Pages by Tag'
    else:
        counts = count_by_field(kb_root, source)
        title = title or f'Distribution by {source}'

    slug = title.lower().replace(' ', '-')[:40] if title else 'distribution'
    if fmt in ('png', 'html'):
        write_tier2(counts[:15], slug, 'distribution_pie', fmt, title)
    else:
        chart = render_distribution_pie(counts[:15], title)
        page = f'---\ntitle: "{title}"\ntags: [export, chart]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{chart}\n'
        write_export(page, slug)

elif template == 'feature_matrix':
    filepath = os.path.join(kb_root, source)
    if not os.path.exists(filepath):
        for root, dirs, files in os.walk(os.path.join(kb_root, 'wiki')):
            for f in files:
                if source.lower() in f.lower():
                    filepath = os.path.join(root, f)
                    break
    rows = parse_markdown_table(filepath, table_index=0)
    if rows:
        # First column is feature/capability, rest are competitors
        first_col = list(rows[0].keys())[0]
        data = [{'feature': r.get(first_col, ''), **{k: v for k, v in r.items() if k != first_col}} for r in rows]
        title = title or 'Feature Comparison'
        slug = title.lower().replace(' ', '-')[:40]
        if fmt == 'png':
            write_tier2(data, slug, 'feature_matrix', fmt, title)
        else:
            matrix = render_feature_matrix(data, title)
            page = f'---\ntitle: "{title}"\ntags: [export, chart]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{matrix}\n'
            write_export(page, slug)
    else:
        print("  No table found in source page.")

elif template == 'topic_slides':
    filepath = os.path.join(kb_root, source)
    if not os.path.exists(filepath):
        for root, dirs, files in os.walk(os.path.join(kb_root, 'wiki')):
            for f in files:
                if source.lower() in f.lower():
                    filepath = os.path.join(root, f)
                    break
    meta, body = _parse_frontmatter(filepath)
    sections = extract_page_sections(filepath)
    related = meta.get('related', [])
    related = [r.strip().strip('"').strip("'").strip('[[').strip(']]') for r in related]
    pg_title = title or meta.get('title', os.path.basename(filepath).replace('.md', ''))
    summary = meta.get('summary', '')
    slides = render_topic_slides(pg_title, summary, sections, related)
    slug = pg_title.lower().replace(' ', '-')[:40]
    # Always generate the markdown slides (needed for Marp too)
    page = f'---\ntitle: "{pg_title} — Slides"\ntags: [export, slides]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{slides}\n'
    md_path = write_export(page, f'{slug}-slides')

    if fmt in ('pptx', 'pdf', 'html') and fmt != 'slides_md':
        write_tier2(None, f'{slug}-slides', 'topic_slides', fmt, pg_title)
    else:
        print("  Enable Obsidian core plugin 'Slides' to present this deck.")

elif template == 'relationship_graph':
    if source == 'kb_stats':
        # Show top-level topic connections
        pages = aggregate_frontmatter(kb_root, filter_fn=lambda m: m.get('source_type') == 'topic')
        seeds = [p['filename'] for p in pages[:10]]
    elif source.startswith('tag:'):
        tag = source.split(':')[1]
        pages = aggregate_frontmatter(kb_root, filter_fn=lambda m: tag in (m.get('tags') or []))
        seeds = [p['filename'] for p in pages[:15]]
    else:
        seeds = [os.path.basename(source).replace('.md', '')]

    graph = build_subgraph(kb_root, seeds, depth=1)
    title = title or 'Knowledge Graph'
    slug = title.lower().replace(' ', '-')[:40]
    if fmt == 'html':
        write_tier2(graph, slug, 'relationship_graph', fmt, title)
    else:
        chart = render_relationship_graph(graph['nodes'], graph['edges'], title)
        page = f'---\ntitle: "{title}"\ntags: [export, chart]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{chart}\n'
        write_export(page, slug)

elif template == 'timeline':
    pages = aggregate_frontmatter(kb_root)
    events = [{'date': p.get('date_added', 'Unknown'), 'title': p.get('title', p.get('filename', ''))}
              for p in pages if p.get('date_added')]
    title = title or 'Knowledge Base Timeline'
    slug = 'timeline'
    if fmt == 'html':
        write_tier2(events, slug, 'timeline', fmt, title)
    else:
        chart = render_timeline(events, title)
        page = f'---\ntitle: "{title}"\ntags: [export, chart]\ndate_added: {time.strftime("%Y-%m-%d")}\n---\n\n{chart}\n'
        write_export(page, slug)

elif template == 'stats_summary':
    # Build a multi-chart dashboard
    type_counts = count_by_field(kb_root, 'source_type')
    tag_counts = count_by_field(kb_root, 'tags')
    total = sum(c for _, c in type_counts)

    stats = {
        'Total pages': total,
        'Source types': len(type_counts),
        'Unique tags': len(tag_counts),
    }

    charts = [
        {'title': 'Pages by Source Type', 'content': render_distribution_pie(type_counts[:10], 'Pages by Source Type')},
        {'title': 'Top 10 Tags', 'content': render_comparison_bar(
            [{'label': t, 'value': c} for t, c in tag_counts[:10]], 'Top 10 Tags')},
    ]

    content = render_stats_summary(stats, charts)
    write_export(content, 'kb-dashboard')

elif template == 'table_export':
    if source == 'kb_stats':
        data = aggregate_frontmatter(kb_root)
        # Flatten for export
        export_data = []
        for p in data:
            export_data.append({
                'title': p.get('title', ''),
                'source_type': p.get('source_type', ''),
                'tags': ', '.join(p.get('tags', [])) if isinstance(p.get('tags'), list) else p.get('tags', ''),
                'date_added': p.get('date_added', ''),
                'url': p.get('url', ''),
                'rel_path': p.get('rel_path', ''),
            })
    elif source.startswith('tag:'):
        tag = source.split(':')[1]
        data = aggregate_frontmatter(kb_root, filter_fn=lambda m: tag in (m.get('tags') or []))
        export_data = [{'title': p.get('title', ''), 'source_type': p.get('source_type', ''),
                        'tags': ', '.join(p.get('tags', [])) if isinstance(p.get('tags'), list) else '',
                        'date_added': p.get('date_added', ''), 'url': p.get('url', '')}
                       for p in data]
    else:
        filepath = os.path.join(kb_root, source)
        if not os.path.exists(filepath):
            for root, dirs, files in os.walk(os.path.join(kb_root, 'wiki')):
                for f in files:
                    if source.lower() in f.lower():
                        filepath = os.path.join(root, f)
                        break
        export_data = parse_markdown_table(filepath)

    if fmt == 'json':
        content = render_table_export(export_data, 'json')
        ext_dir = os.path.join(kb_root, '.athena', 'exports')
        os.makedirs(ext_dir, exist_ok=True)
        fpath = os.path.join(ext_dir, 'export.json')
        with open(fpath, 'w') as f:
            f.write(content)
        print(f"  Exported to: .athena/exports/export.json ({len(export_data)} rows)")
    else:
        content = render_table_export(export_data, 'csv')
        ext_dir = os.path.join(kb_root, '.athena', 'exports')
        os.makedirs(ext_dir, exist_ok=True)
        fpath = os.path.join(ext_dir, 'export.csv')
        with open(fpath, 'w') as f:
            f.write(content)
        print(f"  Exported to: .athena/exports/export.csv ({len(export_data)} rows)")

else:
    print(f"Unknown template: {template}")
    print("Run 'kb export --help' for available templates.")
    sys.exit(1)