import sys, os, re, glob

KB = sys.argv[1]
args = sys.argv[2:]

# ── Parse arguments ──────────────────────────────────
into_name = None
page_names = []
i = 0
while i < len(args):
    if args[i] in ('--yes', '-y'):
        # Issue #142: merge is already non-interactive (no input() prompt
        # anywhere in this command), but kb undo / kb remove / kb purge
        # all accept --yes. Accepting it here too lets automation scripts
        # pass --yes uniformly across kb subcommands.
        i += 1
        continue
    if args[i] in ('--into', '-o'):
        if i + 1 < len(args):
            into_name = args[i + 1]
            i += 2
            continue
        else:
            print("Error: --into requires a name")
            sys.exit(1)
    elif args[i] in ('--help', '-h'):
        print("""Usage: kb merge <page1> <page2> [page3 ...] [--into "Merged Name"]

Merge multiple wiki pages into one page with sections.

Arguments:
  page1, page2, ...   Names of wiki pages to merge (exact filenames without .md)
  --into, -o          Name for the merged page (default: first page name)

What it does:
  1. Reads all source pages and combines their content into sections
  2. Merges frontmatter (raw_paths, urls, tags, related links)
  3. Creates the new merged page
  4. Deletes the old individual pages
  5. Updates ALL wikilinks across the KB to point to the new page

Examples:
  kb merge "Stanford CS229 Cheatsheets" "Stanford CS229 ML Notes" --into "Stanford CS229"
  kb merge "Page A" "Page B"        # merged page uses first page's name
""")
        sys.exit(0)
    else:
        page_names.append(args[i])
        i += 1

# Deduplicate page names
page_names = list(dict.fromkeys(page_names))

if len(page_names) < 2:
    print("Error: need at least 2 distinct page names to merge")
    print("Usage: kb merge <page1> <page2> [page3 ...] [--into \"Merged Name\"]")
    sys.exit(1)

if not into_name:
    into_name = page_names[0]

# ── Find wiki files ──────────────────────────────────
def find_wiki_page(name):
    """Find a wiki page by name (filename without .md)."""
    for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
        if os.path.splitext(os.path.basename(f))[0] == name:
            return f
    return None

def extract_frontmatter(filepath):
    """Extract frontmatter dict and body content."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
    if not m:
        return {}, content
    fm = {}
    lines = m.group(1).split('\n')
    i = 0
    while i < len(lines):
        match = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', lines[i])
        if match:
            key = match.group(1)
            val = match.group(2).strip().strip('"')
            if not val or val == '':
                items = []
                while i + 1 < len(lines) and re.match(r'^\s+-\s+', lines[i + 1]):
                    i += 1
                    item = re.sub(r'^\s+-\s+"?', '', lines[i]).rstrip('"').strip()
                    items.append(item)
                if items:
                    fm[key] = items
                else:
                    fm[key] = val
            else:
                fm[key] = val
        i += 1
    return fm, m.group(2)

pages = []
for name in page_names:
    path = find_wiki_page(name)
    if not path:
        print(f"Error: wiki page not found: {name}")
        print(f"  Searched wiki/**/{name}.md")
        sys.exit(1)
    fm, body = extract_frontmatter(path)
    pages.append({
        'name': name,
        'path': path,
        'fm': fm,
        'body': body,
    })

# ── Preview ──────────────────────────────────────────
print(f"Merging {len(pages)} pages into: {into_name}")
print()
for p in pages:
    src_type = p['fm'].get('source_type', '?')
    print(f"  [{src_type:8s}] {p['name']}")
print()

# ── Build merged frontmatter ─────────────────────────
# Collect raw_paths (dedup while preserving order — re-merging a page
# that already had merged raw_paths would otherwise produce duplicates)
all_raw_paths = []
_seen_raw_paths = set()
def _add_raw(r):
    r = (r or '').strip()
    if r and r not in _seen_raw_paths:
        all_raw_paths.append(r)
        _seen_raw_paths.add(r)
for p in pages:
    fm = p['fm']
    rp = fm.get('raw_path', '')
    if rp:
        if isinstance(rp, list):
            for r in rp: _add_raw(r)
        else:
            _add_raw(rp)
    rps = fm.get('raw_paths', [])
    if isinstance(rps, list):
        for r in rps: _add_raw(r)

# Collect urls (dedup while preserving order — same reason as raw_paths)
all_urls = []
_seen_urls = set()
def _add_url(u):
    u = (u or '').strip()
    if u and u not in _seen_urls:
        all_urls.append(u)
        _seen_urls.add(u)
for p in pages:
    fm = p['fm']
    u = fm.get('url', '')
    if u:
        if isinstance(u, list):
            for x in u: _add_url(x)
        else:
            _add_url(u)
    us = fm.get('urls', [])
    if isinstance(us, list):
        for x in us: _add_url(x)

# Collect tags (union, deduplicated, preserve order)
all_tags = []
seen_tags = set()
for p in pages:
    fm = p['fm']
    tags = fm.get('tags', '')
    if isinstance(tags, str):
        tags = [t.strip().strip('"').strip("'") for t in tags.strip('[]').split(',')]
    for t in tags:
        t = t.strip()
        if t and t not in seen_tags:
            all_tags.append(t)
            seen_tags.add(t)

# Collect related (union, minus self-references to pages being merged)
merged_names = set(p['name'] for p in pages)
merged_names.add(into_name)
all_related = []
seen_related = set()
for p in pages:
    fm = p['fm']
    rels = fm.get('related', [])
    if isinstance(rels, str):
        rels = [rels]
    for r in rels:
        # Extract link name from [[Name]] format
        m = re.search(r'\[\[([^\]|]+)', r)
        link_name = (m.group(1) if m else r).strip()
        # Skip empty entries (a trailing `- ` in a YAML list parses as
        # an empty string — without this, merge accumulates them)
        if not link_name:
            continue
        if link_name not in merged_names and link_name not in seen_related:
            all_related.append(r)
            seen_related.add(link_name)

# Issue #144: source_type ordering by "substantiality" — a merged page's
# source_type should reflect the most authoritative source, not whichever
# arg the user typed first. Books/papers carry richer content than
# webpages or X-posts; let those win even if listed second.
_SOURCE_TYPE_RANK = {
    'book': 6, 'paper': 5, 'repo': 4,
    'video': 3, 'image': 2, 'webpage': 1,
}
def _rank(p):
    return _SOURCE_TYPE_RANK.get(p['fm'].get('source_type', 'webpage'), 0)
source_type = max(pages, key=_rank)['fm'].get('source_type', 'webpage')

# Use earliest date_added
dates = [p['fm'].get('date_added', '9999') for p in pages]
import datetime
_today = datetime.date.today().isoformat()
date_added = min(dates) if dates else _today

# ── Build merged content ─────────────────────────────
merged_lines = []
merged_lines.append('---')
merged_lines.append(f'title: "{into_name}"')
merged_lines.append(f'source_type: "{source_type}"')

if len(all_raw_paths) == 1:
    merged_lines.append(f'raw_path: "{all_raw_paths[0]}"')
elif len(all_raw_paths) > 1:
    merged_lines.append('raw_paths:')
    for rp in all_raw_paths:
        merged_lines.append(f'  - "{rp}"')

if len(all_urls) == 1:
    merged_lines.append(f'url: "{all_urls[0]}"')
elif len(all_urls) > 1:
    merged_lines.append('urls:')
    for u in all_urls:
        merged_lines.append(f'  - "{u}"')

merged_lines.append(f'date_added: {date_added}')
merged_lines.append(f'last_updated: {_today}')
merged_lines.append(f'tags: [{", ".join(all_tags)}]')

if all_related:
    merged_lines.append('related:')
    for r in all_related:
        if r.startswith('[['):
            merged_lines.append(f'  - "{r}"')
        else:
            merged_lines.append(f'  - {r}')

# Issue #141: pick the summary from the most-substantial source page
# (matches the source_type selection above) and write it WHOLE, not a
# 120-char truncation. The legacy `[:120] + "..."` shape predates the
# LLM regen pipeline, which now writes 250-450 char paragraphs that get
# silently lost on every merge. json.dumps gives safe YAML-compatible
# quoting even when the summary contains backticks, em-dashes, or
# embedded quotes.
import json as _json_summary
summaries_with_rank = [
    (p['fm'].get('summary', ''), _rank(p)) for p in pages if p['fm'].get('summary')
]
if summaries_with_rank:
    summaries_with_rank.sort(key=lambda t: -t[1])
    chosen_summary = summaries_with_rank[0][0]
    # ensure_ascii=False keeps em-dashes, arrows, etc. as their actual
    # characters rather than \uXXXX escapes; YAML treats both as valid
    # but the unicode-escaped form looks broken to readers.
    merged_lines.append(f'summary: {_json_summary.dumps(chosen_summary, ensure_ascii=False)}')

merged_lines.append('---')
merged_lines.append('')

# Add sections for each page
for p in pages:
    fm = p['fm']
    title = fm.get('title', p['name'])
    url = fm.get('url', '')
    rp = fm.get('raw_path', '')

    merged_lines.append(f'## {title}')
    links = []
    if url:
        links.append(f'[Source]({url})')
    if rp:
        links.append(f'Raw: `{rp}`')
    if links:
        merged_lines.append(' · '.join(links))
    merged_lines.append('')

    # Add body content (skip leading blank lines)
    body = p['body'].strip()
    # Remove redundant title if body starts with the same title
    body = re.sub(r'^#+\s+' + re.escape(title.split('—')[0].strip()) + r'.*\n*', '', body, count=1)
    merged_lines.append(body)
    merged_lines.append('')
    merged_lines.append('---')
    merged_lines.append('')

merged_content = '\n'.join(merged_lines)

# ── Determine output path ────────────────────────────
# Issue #144: derive out_dir from source_type (the *winning* one chosen
# above), not from pages[0]'s filesystem location. Otherwise merging
# X-post + Drive PDF lands the merged page in webpages/ even when the
# frontmatter says source_type: paper — wiki classification disagrees
# with the directory layout.
_SOURCE_TYPE_DIRS = {
    'paper': 'wiki/format/papers',
    'repo': 'wiki/format/repos',
    'webpage': 'wiki/format/webpages',
    'video': 'wiki/format/videos',
    'image': 'wiki/format/images',
    'book': 'wiki/format/books',
}
out_dir = os.path.join(KB, _SOURCE_TYPE_DIRS.get(source_type, 'wiki/format/webpages'))
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, f'{into_name}.md')

# Reject names with path traversal
if '..' in into_name or '/' in into_name or '\\' in into_name:
    print(f"Error: name contains unsafe characters: {into_name}")
    sys.exit(1)

source_paths = set(p['path'] for p in pages)
if os.path.exists(out_path) and out_path not in source_paths:
    print(f"Error: {out_path} already exists and is not one of the merge sources")
    sys.exit(1)

# ── Snapshot for undo (trash old pages + copy files that will have links cleaned)
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from snapshot import snapshot_files

files_to_delete = [p['path'] for p in pages if p['path'] != out_path]
# Find all wiki files that reference the old page names (will be modified)
files_to_modify = []
old_names = set(p['name'] for p in pages if p['name'] != into_name)
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
        continue
    if f in source_paths:
        continue
    try:
        with open(f, 'r') as fh:
            fc = fh.read()
        if any(f'[[{n}]]' in fc for n in old_names):
            files_to_modify.append(f)
    except (IOError, UnicodeDecodeError):
        pass

snapshot_files(KB, files_to_delete, files_to_modify,
               operation="merge", description=f'kb merge {" + ".join(p["name"] for p in pages)} --into {into_name}')

# ── Write merged page (validate via canonical schema) ───────────
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from wiki_schema import validate_wiki_frontmatter, SchemaError  # type: ignore
try:
    validate_wiki_frontmatter(merged_content)
except SchemaError as e:
    print(f"Error: merged content fails wiki schema: {e}")
    print("(merge aborted — fix source pages first)")
    sys.exit(1)
# Atomic write — see write_wiki_page for rationale.
tmp_path = out_path + '.tmp'
with open(tmp_path, 'w', encoding='utf-8') as f:
    f.write(merged_content)
os.replace(tmp_path, out_path)
print(f"Created: {os.path.relpath(out_path, KB)}")
print(f"Trashed {len(files_to_delete)} old page(s)")

# ── Mark loser raw files so orphan-raw synth doesn't recreate wiki pages ─
# Without this marker, the next `kb lint` sees raw files whose owning wiki
# page just got trashed, treats them as orphan raws, and synthesizes a
# fresh wiki page for each — re-introducing the very duplicates we just
# merged. The marker `merged_into:` tells lint #2 (orphan synthesis) to
# leave these raws alone; they are reachable via the winner's frontmatter
# `raw_paths:` list.
_loser_raws = []
for p in pages:
    if p['path'] == out_path:
        continue
    fm_p = p.get('fm') or {}
    rps = []
    rp_single = fm_p.get('raw_path')
    if isinstance(rp_single, str) and rp_single.strip():
        rps.append(rp_single.strip().strip('"').strip("'"))
    rp_list = fm_p.get('raw_paths') or []
    if isinstance(rp_list, list):
        for r in rp_list:
            r = str(r).strip().strip('"').strip("'")
            if r: rps.append(r)
    _loser_raws.extend(rps)

_marked = 0
for raw_rel in set(_loser_raws):
    raw_abs = os.path.join(KB, raw_rel)
    if not os.path.isfile(raw_abs):
        continue
    try:
        with open(raw_abs, 'r', encoding='utf-8') as fh:
            text = fh.read()
    except (IOError, UnicodeDecodeError):
        continue
    if 'merged_into:' in text[:500]:
        continue  # already marked from a prior merge
    if text.startswith('---'):
        # Insert merged_into into existing frontmatter
        end = text.find('\n---', 3)
        if end == -1:
            continue
        fm = text[:end]
        body = text[end:]
        new_fm = fm + f'\nmerged_into: "{into_name}"'
        new_text = new_fm + body
    else:
        # No frontmatter — wrap in minimal one
        new_text = f'---\nmerged_into: "{into_name}"\n---\n\n' + text
    with open(raw_abs, 'w', encoding='utf-8') as fh:
        fh.write(new_text)
    _marked += 1
if _marked:
    print(f"Marked {_marked} loser raw file(s) with merged_into → {into_name}")

# ── Update all wikilinks ─────────────────────────────
renames = {name: into_name for name in page_names if name != into_name}

if renames:
    updated_files = 0
    for f in sorted(glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)):
        if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
            continue
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                content = fh.read()
        except (IOError, UnicodeDecodeError):
            continue

        original = content
        for old_name, new_name in renames.items():
            content = content.replace(f'[[{old_name}]]', f'[[{new_name}]]')
            content = content.replace(f'"[[{old_name}]]"', f'"[[{new_name}]]"')
            # Handle [[old|display]] format
            content = re.sub(
                r'\[\[' + re.escape(old_name) + r'\|([^\]]+)\]\]',
                f'[[{new_name}|\\1]]',
                content
            )

        # Deduplicate related: entries
        lines = content.split('\n')
        in_related = False
        seen = set()
        new_lines = []
        for line in lines:
            if re.match(r'^related:', line):
                in_related = True
                new_lines.append(line)
                continue
            if in_related:
                if re.match(r'^\s+-\s+', line):
                    m = re.search(r'\[\[([^\]|]+)', line)
                    if m:
                        target = m.group(1)
                        if target in seen:
                            continue
                        seen.add(target)
                else:
                    in_related = False
            new_lines.append(line)
        content = '\n'.join(new_lines)

        if content != original:
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(content)
            updated_files += 1

    print(f"Updated wikilinks in {updated_files} files")

# ── Update index.md count ────────────────────────────
index_path = os.path.join(KB, 'index.md')
if os.path.exists(index_path):
    with open(index_path, 'r') as f:
        idx = f.read()
    actual_wiki = len([f for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)
                       if '.gitkeep' not in f and '_TEMPLATE' not in f])
    actual_raw = len([f for f in glob.glob(os.path.join(KB, 'raw', '**', '*.md'), recursive=True)
                      if '.gitkeep' not in f and '_TEMPLATE' not in f])
    idx = re.sub(
        r'>\s*\d+\s+sources?\s*·\s*\d+\s+wiki pages?',
        f'> {actual_raw} sources · {actual_wiki} wiki pages',
        idx
    )
    with open(index_path, 'w') as f:
        f.write(idx)

print()
print(f"Done. Merged {len(pages)} pages → {into_name}")
print(f"Run 'kb lint' to verify.")