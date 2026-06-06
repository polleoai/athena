import sys, os, re, glob

KB = sys.argv[1]
args = sys.argv[2:]

# ── Parse arguments ──────────────────────────────────
page_names = []
to_hub = None
i = 0
while i < len(args):
    if args[i] in ('--to', '--into', '-t', '-o'):
        if i + 1 < len(args):
            to_hub = args[i + 1]; i += 2; continue
        else:
            print("Error: --into requires a hub name"); sys.exit(1)
    elif args[i] in ('--help', '-h'):
        print("""Usage: kb move <page> [page2 ...] --into "Hub"

Move pages into a hub. If a page is already in another hub, it is
removed from the old hub first (auto-detected).

Arguments:
  page, page2, ...   Pages to move (exact filenames without .md)
  --into, --to       Target hub (required, must exist — use kb create first)

What it does:
  1. Auto-detects which hub(s) each page currently belongs to
  2. Removes from old hub(s) if any
  3. Adds to target hub (related links + table row)
  4. Updates each page's own related links

Examples:
  kb move "Page A" "Page B" --into "My Hub"        # move into hub
  kb move "Page A" --into "New Hub"                 # auto-removes from old hub
""")
        sys.exit(0)
    else:
        page_names.append(args[i])
        i += 1

# Validate names
for _n in page_names + ([to_hub] if to_hub else []):
    if '..' in _n or '/' in _n or '\\' in _n:
        print(f"Error: name contains unsafe characters: {_n}")
        sys.exit(1)

if not page_names or not to_hub:
    print("Error: need at least one <page> and --into <hub>")
    print('Usage: kb move <page> [page2 ...] --into "Hub"')
    sys.exit(1)

# ── Helpers ──────────────────────────────────────────
def find_wiki_page(name):
    for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
        if os.path.splitext(os.path.basename(f))[0] == name:
            return f
    return None

def find_current_hubs(page_name):
    """Find topic/hub/insight-group pages that reference this page anywhere
    membership-bearing — table row, frontmatter `related:`, or a body
    bullet list (`- [[Page]]`).

    Pre-fix this only checked table rows, missing hubs whose membership
    was carried in frontmatter or a Connections-style bullet list. That
    meant `kb move` could leave stale memberships behind, and the user
    would later have to lint or hand-edit to clean up. With detection now
    spanning all three idioms, move is complete in one pass.
    """
    hubs = []
    target_ref = f'[[{page_name}]]'
    bullet_ref_q = f'- "{target_ref}"'  # quoted YAML/list form
    bullet_ref_p = f'- {target_ref}'    # plain bullet form
    for search_dir in [os.path.join(KB, 'wiki', 'topics'), os.path.join(KB, 'wiki', 'insights')]:
        for f in glob.glob(os.path.join(search_dir, '*.md')):
            if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
                continue
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    content = fh.read()
            except (IOError, UnicodeDecodeError):
                continue
            # Split into frontmatter and body so we can check each idiom.
            fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            fm = fm_match.group(1) if fm_match else ''
            body = content[fm_match.end():] if fm_match else content

            found = False
            # 1. Frontmatter `related:` list contains the page
            if target_ref in fm:
                found = True
            # 2. Body table row mentions the page
            if not found:
                for line in body.split('\n'):
                    if line.startswith('|') and target_ref in line:
                        found = True; break
            # 3. Body bullet-list mentions the page (Connections section)
            if not found:
                for line in body.split('\n'):
                    s = line.lstrip()
                    if (s.startswith(bullet_ref_q) or s.startswith(bullet_ref_p)):
                        found = True; break
            if found:
                hub_name = os.path.splitext(os.path.basename(f))[0]
                hubs.append((hub_name, f))
    return hubs

# ── Validate pages ───────────────────────────────────
pages = []
for name in page_names:
    path = find_wiki_page(name)
    if not path:
        print(f"Error: page not found: {name}"); sys.exit(1)
    pages.append((name, path))

to_path = find_wiki_page(to_hub)
if not to_path:
    print(f"Error: hub not found: {to_hub}")
    print(f"  Create it first: kb create \"{to_hub}\" --topic")
    sys.exit(1)

# ── Auto-detect source hubs and preview ──────────────
print(f"Moving {len(pages)} page(s) → {to_hub}")
moves = []  # (page_name, page_path, old_hubs)
for name, path in pages:
    old_hubs = find_current_hubs(name)
    old_hubs = [(h, p) for h, p in old_hubs if h != to_hub]  # exclude target
    moves.append((name, path, old_hubs))
    if old_hubs:
        hub_names = ', '.join(h for h, _ in old_hubs)
        print(f"  {name}  ({hub_names} → {to_hub})")
    else:
        print(f"  {name}  (→ {to_hub})")
print()

# ── Snapshot for undo ─────────────────────────────────
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from snapshot import snapshot_files

files_to_modify = [to_path]  # target hub will be modified
for name, path, old_hubs in moves:
    files_to_modify.append(path)  # each moved page
    for _, hp in old_hubs:
        files_to_modify.append(hp)  # each old hub
files_to_modify = list(set(f for f in files_to_modify if os.path.exists(f)))

snapshot_files(KB, files_to_delete=[], files_to_modify=files_to_modify,
               operation="move", description=f'kb move {" ".join(n for n,_,_ in moves)} --into {to_hub}')

# ── Remove from old hubs ─────────────────────────────
# Surgical removal across three idioms — frontmatter `related:` list,
# body table row, body bullet line. Pre-fix this stripped any line
# mentioning [[name]], which could delete prose that happened to
# reference the page. Now the body strip is restricted to lines that
# are STRUCTURAL membership markers (start with `|` or `-`), leaving
# narrative mentions intact.
def _remove_page_from_hub(hub_text: str, name: str) -> tuple[str, bool]:
    target = f'[[{name}]]'
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', hub_text, re.DOTALL)
    fm = fm_match.group(1) if fm_match else ''
    body = hub_text[fm_match.end():] if fm_match else hub_text

    new_fm_lines = []
    for line in fm.split('\n'):
        s = line.lstrip()
        if (s.startswith(f'- "{target}"') or s.startswith(f"- '{target}'")
                or s == f'- {target}' or s.startswith(f'- {target}#')):
            continue  # drop this related: entry
        new_fm_lines.append(line)
    new_fm = '\n'.join(new_fm_lines)

    new_body_lines = []
    for line in body.split('\n'):
        # Strip table rows that contain the page wikilink as a structural cell
        if line.lstrip().startswith('|') and target in line:
            continue
        # Strip bullet entries that ARE the page reference (not prose)
        s = line.lstrip()
        if (s.startswith(f'- {target}') or s.startswith(f'- "{target}"')
                or s.startswith(f"- '{target}'")):
            continue
        new_body_lines.append(line)
    new_body = '\n'.join(new_body_lines)

    new_text = (f'---\n{new_fm}\n---\n' + new_body) if fm_match else new_body
    return new_text, new_text != hub_text

for name, path, old_hubs in moves:
    for hub_name, hub_path in old_hubs:
        with open(hub_path, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content, changed = _remove_page_from_hub(content, name)
        if changed:
            tmp = hub_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(new_content)
            os.replace(tmp, hub_path)
            print(f"  Removed {name} from: {hub_name}")

# ── Add to target hub ────────────────────────────────
with open(to_path, 'r', encoding='utf-8') as f:
    content = f.read()

added = 0
for name, path, _ in moves:
    if f'[[{name}]]' in content:
        continue
    content = re.sub(
        r'related:\s*(?:\[\])?\s*\n',
        r'related:\n  - "[[' + name + r']]"\n',
        content
    )
    # Try to add a table row
    table_match = re.search(r'(\|[^\n]*\|\n\|[-| ]+\|\n)((?:\|[^\n]*\|\n)*)', content)
    if table_match:
        try:
            with open(path, 'r') as f:
                pc = f.read()
            pm = re.match(r'^---\s*\n(.*?)\n---', pc, re.DOTALL)
            p_type = p_tags = ''
            if pm:
                for line in pm.group(1).split('\n'):
                    m = re.match(r'^source_type:\s*"?([^"]+)"?', line)
                    if m: p_type = m.group(1)
                    m = re.match(r'^tags:\s*(.+)', line)
                    if m: p_tags = m.group(1)
            new_row = f'| [[{name}]] | {p_type} | {p_tags} |\n'
            table_end = table_match.start() + len(table_match.group(0))
            content = content[:table_end] + new_row + content[table_end:]
        except (IOError, UnicodeDecodeError):
            pass
    added += 1

if added:
    tmp = to_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp, to_path)
    print(f"  Added {added} page(s) to: {to_hub}")
else:
    print(f"  All pages already in {to_hub}")

# ── Update each page's own related links ─────────────
for name, path, old_hubs in moves:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    changed = False
    if f'[[{to_hub}]]' not in content:
        content = re.sub(
            r'related:\s*(?:\[\])?\s*\n',
            r'related:\n  - "[[' + to_hub + r']]"\n',
            content
        )
        changed = True
    for hub_name, _ in old_hubs:
        old_ref = f'  - "[[{hub_name}]]"\n'
        if old_ref in content:
            content = content.replace(old_ref, '')
            changed = True
    if changed:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp, path)

# ── Update index.md count ────────────────────────────
index_path = os.path.join(KB, 'index.md')
if os.path.exists(index_path):
    with open(index_path, 'r') as f:
        idx = f.read()
    actual_wiki = len([f for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)
                       if '.gitkeep' not in f and '_TEMPLATE' not in f])
    actual_raw = len([f for f in glob.glob(os.path.join(KB, 'raw', '**', '*.md'), recursive=True)
                      if '.gitkeep' not in f and '_TEMPLATE' not in f])
    idx = re.sub(r'>\s*\d+\s+sources?\s*·\s*\d+\s+wiki pages?',
                 f'> {actual_raw} sources · {actual_wiki} wiki pages', idx)
    with open(index_path, 'w') as f:
        f.write(idx)

print()
print("Done.")
print("Run 'kb lint' to verify.")