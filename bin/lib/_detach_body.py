import sys, os, re, glob

KB = sys.argv[1]
args = sys.argv[2:]

page_names = []
from_hub = None
i = 0
while i < len(args):
    if args[i] in ('--from', '--hub', '-f'):
        if i + 1 < len(args):
            from_hub = args[i + 1]; i += 2; continue
        else:
            print("Error: --from requires a hub name"); sys.exit(1)
    elif args[i] in ('--help', '-h'):
        print("""Usage: kb detach <page> [page2 ...] --from "Hub"

Remove pages from one specific hub WITHOUT deleting the pages or
touching their other hub memberships. Inverse of `kb attach`.

Arguments:
  page, page2, ...   Pages to detach (exact filenames without .md)
  --from, --hub      Source hub to detach from (required)

What it does:
  1. Removes the page from the target hub's related: + body table/list
  2. Removes the hub from the page's own related: list
  3. Leaves all other hub memberships intact
  4. Does NOT delete the page itself

Examples:
  kb detach "Adversarial ML Paper" --from "AI Security"
  # Page still exists; if it was attached to "Adversarial Attacks"
  # too, that membership is preserved.
""")
        sys.exit(0)
    else:
        page_names.append(args[i])
        i += 1

for _n in page_names + ([from_hub] if from_hub else []):
    if '..' in _n or '/' in _n or '\\' in _n:
        print(f"Error: name contains unsafe characters: {_n}")
        sys.exit(1)

if not page_names or not from_hub:
    print("Error: need at least one <page> and --from <hub>")
    print('Usage: kb detach <page> [page2 ...] --from "Hub"')
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

from_path = find_wiki_page(from_hub)
if not from_path:
    print(f"Error: hub not found: {from_hub}"); sys.exit(1)

print(f"Detaching {len(pages)} page(s) ← {from_hub}")
for name, _ in pages:
    print(f"  {name}")
print()

sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from snapshot import snapshot_files

files_to_modify = [from_path] + [p for _, p in pages]
files_to_modify = list(set(f for f in files_to_modify if os.path.exists(f)))
snapshot_files(KB, files_to_delete=[], files_to_modify=files_to_modify,
               operation="detach", description=f'kb detach {" ".join(n for n,_ in pages)} --from {from_hub}')

# Surgical removal of each page from the source hub: structural lines
# only (frontmatter `related:` entry, body table row, body bullet).
# Prose mentions of the page in the hub's narrative are preserved.
def _remove_page_from_hub(hub_text, name):
    target = f'[[{name}]]'
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', hub_text, re.DOTALL)
    fm = fm_match.group(1) if fm_match else ''
    body = hub_text[fm_match.end():] if fm_match else hub_text
    new_fm_lines = []
    for line in fm.split('\n'):
        s = line.lstrip()
        if (s.startswith(f'- "{target}"') or s.startswith(f"- '{target}'")
                or s == f'- {target}' or s.startswith(f'- {target}#')):
            continue
        new_fm_lines.append(line)
    new_body_lines = []
    for line in body.split('\n'):
        if line.lstrip().startswith('|') and target in line:
            continue
        s = line.lstrip()
        if (s.startswith(f'- {target}') or s.startswith(f'- "{target}"')
                or s.startswith(f"- '{target}'")):
            continue
        new_body_lines.append(line)
    new_text = (f'---\n' + '\n'.join(new_fm_lines) + '\n---\n' + '\n'.join(new_body_lines)) if fm_match else '\n'.join(new_body_lines)
    return new_text, new_text != hub_text

with open(from_path, 'r', encoding='utf-8') as f:
    hub_content = f.read()
hub_changed = False
detached_count = 0
for name, _ in pages:
    new_hub, changed = _remove_page_from_hub(hub_content, name)
    if changed:
        hub_content = new_hub
        hub_changed = True
        detached_count += 1
        print(f"  Detached {name} from {from_hub}")
if hub_changed:
    tmp = from_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: f.write(hub_content)
    os.replace(tmp, from_path)

# Remove the hub from each page's related: list
for name, path in pages:
    with open(path, 'r', encoding='utf-8') as f:
        pc = f.read()
    target = f'[[{from_hub}]]'
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', pc, re.DOTALL)
    if not fm_match: continue
    fm = fm_match.group(1)
    new_fm_lines = []
    for line in fm.split('\n'):
        s = line.lstrip()
        if (s.startswith(f'- "{target}"') or s.startswith(f"- '{target}'")
                or s == f'- {target}'):
            continue
        new_fm_lines.append(line)
    new_fm = '\n'.join(new_fm_lines)
    if new_fm != fm:
        new_text = f'---\n{new_fm}\n---\n' + pc[fm_match.end():]
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: f.write(new_text)
        os.replace(tmp, path)

print()
print(f"Done. Detached {detached_count} page(s) from {from_hub}.")
print("Run 'kb lint' to verify.")