import sys, os, re, glob

KB = sys.argv[1]
args = sys.argv[2:]

page_names = []
to_hub = None
i = 0
while i < len(args):
    if args[i] in ('--to', '--into', '--hub', '-t', '-o'):
        if i + 1 < len(args):
            to_hub = args[i + 1]; i += 2; continue
        else:
            print("Error: --to requires a hub name"); sys.exit(1)
    elif args[i] in ('--help', '-h'):
        print("""Usage: kb attach <page> [page2 ...] --to "Hub"

Add pages to a hub WITHOUT removing them from any other hub. Use this
for genuinely multi-categorical pages (one paper that belongs to
several topics). Use `kb move` instead when relocating from one
primary topic to another. Use `kb detach` to undo.

Arguments:
  page, page2, ...   Pages to add (exact filenames without .md)
  --to, --into, --hub   Target hub (required, must exist — use kb create first)

What it does:
  1. Adds each page to the target hub (related links + table row)
  2. Updates each page's own related: list to include the target hub
  3. Leaves all other hub memberships intact

Examples:
  kb attach "Adversarial ML Paper" --to "AI Security"
  kb attach "Adversarial ML Paper" --to "Adversarial Attacks"
  # Page is now in BOTH topics; kb move would relocate it instead.
""")
        sys.exit(0)
    else:
        page_names.append(args[i])
        i += 1

for _n in page_names + ([to_hub] if to_hub else []):
    if '..' in _n or '/' in _n or '\\' in _n:
        print(f"Error: name contains unsafe characters: {_n}")
        sys.exit(1)

if not page_names or not to_hub:
    print("Error: need at least one <page> and --to <hub>")
    print('Usage: kb attach <page> [page2 ...] --to "Hub"')
    sys.exit(1)

def find_wiki_page(name):
    for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
        if os.path.splitext(os.path.basename(f))[0] == name:
            return f
    return None

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

print(f"Attaching {len(pages)} page(s) → {to_hub} (preserving existing memberships)")
for name, _ in pages:
    print(f"  {name}")
print()

sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from snapshot import snapshot_files

files_to_modify = [to_path] + [p for _, p in pages]
files_to_modify = list(set(f for f in files_to_modify if os.path.exists(f)))
snapshot_files(KB, files_to_delete=[], files_to_modify=files_to_modify,
               operation="attach", description=f'kb attach {" ".join(n for n,_ in pages)} --to {to_hub}')

with open(to_path, 'r', encoding='utf-8') as f:
    content = f.read()

added = 0
already = 0
for name, path in pages:
    if f'[[{name}]]' in content:
        already += 1; continue
    new_content, n = re.subn(
        r'related:\s*(?:\[\])?\s*\n',
        r'related:\n  - "[[' + name + r']]"\n',
        content, count=1,
    )
    if n: content = new_content
    table_match = re.search(r'(\|[^\n]*\|\n\|[-| ]+\|\n)((?:\|[^\n]*\|\n)*)', content)
    if table_match:
        try:
            with open(path, 'r', encoding='utf-8') as f:
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
    with open(tmp, 'w', encoding='utf-8') as f: f.write(content)
    os.replace(tmp, to_path)
    print(f"  Attached {added} page(s) to: {to_hub}")
if already:
    print(f"  Already attached: {already} page(s)")

for name, path in pages:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    if f'[[{to_hub}]]' in content: continue
    new_content, n = re.subn(
        r'related:\s*(?:\[\])?\s*\n',
        r'related:\n  - "[[' + to_hub + r']]"\n',
        content, count=1,
    )
    if n:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: f.write(new_content)
        os.replace(tmp, path)

print()
print("Done.")
print("Run 'kb lint' to verify.")