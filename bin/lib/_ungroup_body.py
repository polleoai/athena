import sys, os, re, glob

KB = sys.argv[1]
args = sys.argv[2:]

# ── Parse arguments ──────────────────────────────────
hub_name = None
confirmed = False
move_to_arg = None
i = 0
while i < len(args):
    if args[i] in ('--yes', '-y'):
        confirmed = True; i += 1
    elif args[i] in ('--move-to', '--to'):
        if i + 1 < len(args):
            move_to_arg = args[i + 1]; i += 2; continue
        else:
            print("Error: --move-to requires a hub name"); sys.exit(1)
    elif args[i] in ('--help', '-h'):
        print("""Usage: kb ungroup <hub>

Dissolve a hub page. Children keep their own pages but lose the hub link.

Arguments:
  hub              Hub page to dissolve (exact filename without .md)
  --yes, -y        Skip confirmation prompt

What it does:
  1. Asks where to move children (another hub or leave independent)
  2. Removes the hub link from each child's related: section
  3. Asks what to do with the hub page:
     a. Delete it (soft-delete to trash)
     b. Keep it as a regular page (table removed, prose kept)
  4. Updates index counts

Children are NOT deleted — they become independent or move to another hub.

Examples:
  kb ungroup "Stanford AI Courses"
""")
        sys.exit(0)
    else:
        if hub_name is None:
            hub_name = args[i]
        i += 1

if not hub_name:
    print("Error: need a hub name")
    print('Usage: kb ungroup <hub>')
    sys.exit(1)

if '..' in hub_name or '/' in hub_name or '\\' in hub_name:
    print(f"Error: name contains unsafe characters: {hub_name}")
    sys.exit(1)

# ── Find the hub ─────────────────────────────────────
hub_path = None
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if os.path.splitext(os.path.basename(f))[0] == hub_name:
        hub_path = f
        break

if not hub_path:
    print(f"Error: hub not found: {hub_name}")
    sys.exit(1)

# ── Find children (pages linked in table rows) ───────
with open(hub_path, 'r', encoding='utf-8') as f:
    content = f.read()

children = []
body_start = content.find('---', content.find('---') + 3)
if body_start > 0:
    body = content[body_start + 3:]
    for line in body.split('\n'):
        if line.startswith('|') and '[[' in line:
            for m in re.finditer(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', line):
                child = m.group(1)
                if child != hub_name:
                    children.append(child)

# Also find children from related: that aren't external
# (pages that link back to this hub)
all_backlinking = []
for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
    if f == hub_path:
        continue
    if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
        continue
    with open(f, 'r') as fh:
        if f'[[{hub_name}]]' in fh.read():
            name = os.path.splitext(os.path.basename(f))[0]
            if name not in children:
                all_backlinking.append(name)

all_children = children + all_backlinking

if not all_children:
    print(f"'{hub_name}' has no children. Use 'kb remove' instead.")
    sys.exit(1)

# ── Find existing hubs to offer as destinations ──────
existing_hubs = []
for f in sorted(glob.glob(os.path.join(KB, 'wiki', 'topics', '*.md'))):
    name = os.path.splitext(os.path.basename(f))[0]
    if name != hub_name and name not in ('.gitkeep', '_TEMPLATE'):
        existing_hubs.append(name)

# ── Preview ──────────────────────────────────────────
print(f"Dissolving hub: {hub_name}")
print(f"Children ({len(all_children)}):")
for c in all_children:
    print(f"  - {c}")
print()

# Ask where to move children
print("Move children to:")
options = []
for i, h in enumerate(existing_hubs, 1):
    print(f"  {i}. {h}")
    options.append(h)
leave_idx = len(options) + 1
print(f"  {leave_idx}. Leave independent (no group)")
print()

move_to = move_to_arg  # set by --move-to flag, or None
if not confirmed and move_to is None:
    try:
        choice = input(f"Choice (1-{leave_idx}): ").strip()
        if not choice:
            print("Cancelled."); sys.exit(0)
        choice_num = int(choice)
        if choice_num == leave_idx:
            move_to = None  # leave independent
        elif 1 <= choice_num <= len(options):
            move_to = options[choice_num - 1]
        else:
            print("Invalid choice. Cancelled."); sys.exit(0)
    except ValueError:
        # User typed a name directly instead of a number
        move_to = choice
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled."); sys.exit(0)

# ── Snapshot for undo (hub + all children) ────────────
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
from snapshot import snapshot_files

files_to_snapshot = [hub_path]
for child_name in all_children:
    for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
        if os.path.splitext(os.path.basename(f))[0] == child_name:
            files_to_snapshot.append(f)
            break

snapshot_files(KB, files_to_delete=[], files_to_modify=files_to_snapshot,
               operation="ungroup", description=f'kb ungroup "{hub_name}" ({len(all_children)} children)')

# ── Remove hub link from children ────────────────────
updated = 0
for f in sorted(glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)):
    if f == hub_path:
        continue
    if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
        continue
    with open(f, 'r', encoding='utf-8') as fh:
        content = fh.read()
    original = content

    # Remove hub from related:
    content = content.replace(f'  - "[[{hub_name}]]"\n', '')
    # Remove inline references (in body text only, not frontmatter)
    # Split at frontmatter end to avoid touching related: of other hubs
    fm_end = content.find('---', content.find('---') + 3)
    if fm_end > 0:
        body = content[fm_end:]
        body = body.replace(f'[[{hub_name}]]', hub_name)
        content = content[:fm_end] + body

    if content != original:
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(content)
        updated += 1

print(f"Removed hub link from {updated} child pages")

# ── Move children to new hub if selected ─────────────
if move_to:
    new_hub_path = None
    for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True):
        if os.path.splitext(os.path.basename(f))[0] == move_to:
            new_hub_path = f
            break
    if new_hub_path:
        with open(new_hub_path, 'r', encoding='utf-8') as f:
            hub_content = f.read()
        for child in all_children:
            if f'[[{child}]]' not in hub_content:
                hub_content = re.sub(
                    r'related:\s*(?:\[\])?\s*\n',
                    r'related:\n  - "[[' + child + r']]"\n',
                    hub_content
                )
        with open(new_hub_path, 'w', encoding='utf-8') as f:
            f.write(hub_content)
        # Also add new hub to children's related:
        for f in sorted(glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)):
            name = os.path.splitext(os.path.basename(f))[0]
            if name in all_children:
                with open(f, 'r', encoding='utf-8') as fh:
                    content = fh.read()
                if f'[[{move_to}]]' not in content:
                    content = re.sub(
                        r'related:\s*(?:\[\])?\s*\n',
                        r'related:\n  - "[[' + move_to + r']]"\n',
                        content
                    )
                    with open(f, 'w', encoding='utf-8') as fh:
                        fh.write(content)
        print(f"Moved {len(all_children)} children to: {move_to}")
    else:
        print(f"Warning: hub '{move_to}' not found. Children left independent.")

# ── Ask what to do with the hub page ─────────────────
hub_action = None
if not confirmed:
    print("What to do with the hub page?")
    print("  a. Delete it (move to trash)")
    print("  b. Keep it as a regular page (remove table, keep content)")
    print()
    try:
        hub_choice = input("Choice (a/b): ").strip().lower()
        if hub_choice == 'b':
            hub_action = 'keep'
        else:
            hub_action = 'delete'
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled."); sys.exit(0)
else:
    hub_action = 'delete'  # default for --yes

sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))

if hub_action == 'delete':
    from snapshot import trash_files
    trash_files(KB, [hub_path], operation="ungroup", description=f'kb ungroup "{hub_name}" ({len(all_children)} children freed)')
    print(f"Trashed: {os.path.relpath(hub_path, KB)}")
    print(f"  Use 'kb undo' to restore")
else:
    # Keep hub but strip the children table, keep prose content
    with open(hub_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # Remove table rows (lines starting with |)
    lines = content.split('\n')
    new_lines = []
    in_table = False
    for line in lines:
        if line.startswith('|'):
            in_table = True
            continue  # skip table lines
        if in_table and not line.startswith('|') and line.strip() == '':
            in_table = False
            continue  # skip blank line after table
        in_table = False
        new_lines.append(line)
    content = '\n'.join(new_lines)
    # Remove children from related:
    for child in all_children:
        content = content.replace(f'  - "[[{child}]]"\n', '')
    # Change source_type from topic to webpage
    content = re.sub(r'^source_type:\s*"?topic"?', 'source_type: "webpage"', content, flags=re.MULTILINE)
    with open(hub_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Kept as regular page: {os.path.relpath(hub_path, KB)} (table removed)")

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