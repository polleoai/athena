# AUTO-LIFTED VERBATIM from the `lint)` heredoc body in bin/kb-legacy.
# Do NOT edit by hand. This file is the lint check suite, run at MODULE
# scope by cmd_lint.handle() via exec() -- module scope is REQUIRED because
# the body's nested helpers (e.g. `check`) use `global issues, auto_fixed,
# total_checks`, which only binds module-level names. The sole edit vs the
# heredoc is `KB = sys.argv[1]` -> `KB = root` (root injected by the caller).
# Decomposition into sub-modules is explicitly deferred.

import re, os, glob, sys
from pathlib import Path

KB = root

# Share one source of truth for naming rules with the ingest path.
sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
try:
    from wiki_page import apply_naming_convention
except ImportError:
    def apply_naming_convention(title, url, source_type, truncate=True):
        return title  # Safe no-op fallback if the lib isn't importable

issues = 0
auto_fixed = 0
total_checks = 0

def header(title):
    pass  # Silent — only problems get printed

def check(name, items, show_limit=10, fixed=False):
    global issues, auto_fixed, total_checks
    total_checks += 1
    count = len(items)
    if count == 0:
        return  # Silent when clean
    if fixed:
        auto_fixed += count
        # Brief note for auto-fixes
        print(f"  Fixed {count}: {name}")
    else:
        issues += count
        print(f"  ✗ {name}: {count}")
        for item in items[:show_limit]:
            print(f"    - {item}")
        if count > show_limit:
            print(f"    ... and {count - show_limit} more")

def extract_frontmatter(filepath):
    """Extract frontmatter as dict from a wiki page.
    Handles both single values and YAML lists (raw_paths, urls)."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (IOError, UnicodeDecodeError) as e:
        print(f"  WARNING: cannot read {filepath}: {e}", file=sys.stderr)
        return {}, ""
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
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
            # Parse inline YAML list: [item1, item2]
            if val.startswith('[') and val.endswith(']'):
                items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',') if v.strip()]
                fm[key] = items
                i += 1
                continue
            # Check if next lines are YAML list items
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
    return fm, content

def extract_url_from_raw(filepath):
    """Extract source URL from a raw page.

    Two formats coexist:
      * Canonical writer (wiki_schema.write_raw_page): YAML frontmatter
        with `source: "https://..."` (or legacy raws with `url:`).
      * Pre-canonical bullet form: `- **URL:** https://...` in the body.

    Frontmatter is checked first because canonical raws are now the norm;
    only fall through to the bullet form for legacy raws still on disk.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            head = f.read(8000)
    except (IOError, UnicodeDecodeError):
        return None
    m = re.search(r'^\s*(?:source|url):\s*"?(https?://[^\s"]+)"?\s*$', head, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r'^-\s+\*\*URL:\*\*\s+(\S+)', head, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None

# ─────────────────────────────────────────────────────
print("Knowledge Base Health Check")
print("===========================")

# Gather all wiki pages
wiki_pages = {}  # basename (no ext) -> filepath
wiki_files = []
for f in sorted(glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)):
    base = os.path.basename(f)
    if base in ('.gitkeep', '_TEMPLATE.md'):
        continue
    name = os.path.splitext(base)[0]
    wiki_pages[name] = f
    if '/dashboards/' not in f:
        wiki_files.append(f)

# Gather all raw files — uses configured category paths + artifact exts.
raw_files = {}  # filepath -> url
from config import raw_dir as _raw_dir, raw_categories
for cat_name, cat_cfg in raw_categories().items():
    full_d = os.path.join(KB, _raw_dir(cat_name))
    for f in sorted(glob.glob(os.path.join(full_d, '*.md'))):
        base = os.path.basename(f)
        if base in ('.gitkeep', '_TEMPLATE.md'):
            continue
        rel = os.path.relpath(f, KB)
        url = extract_url_from_raw(f)
        raw_files[rel] = url

# ═══════════════════════════════════════════════════
header("1. WIKILINK INTEGRITY")
# ═══════════════════════════════════════════════════

broken_links = set()
fixed_broken_links = []
for f in wiki_files:
    if '/dashboards/' in f:
        continue  # Dashboard pages are generated — skip stale link check
    _, content = extract_frontmatter(f)
    # Exclude Keywords section from broken link check — tags may not have wiki pages
    content_for_links = content.split('## Keywords')[0] if '## Keywords' in content else content
    dead_links_in_file = []
    for link in re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', content_for_links):
        if link not in wiki_pages:
            # Skip links to raw files (Local Copy links) — they're valid file paths
            if link.startswith('raw/') and os.path.exists(os.path.join(KB, link)):
                continue
            dead_links_in_file.append(link)

    if dead_links_in_file:
        bn = os.path.basename(f)
        # Auto-fix: remove dead links from keyword/index pages and Connections sections
        is_keyword = '/keywords/' in f
        if is_keyword:
            with open(f, 'r', encoding='utf-8') as fh:
                text = fh.read()
            for dead in dead_links_in_file:
                # Remove lines containing the dead link
                text = re.sub(r'[^\n]*\[\[' + re.escape(dead) + r'(?:\|[^\]]+?)?\]\][^\n]*\n?', '', text)
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(text)
            fixed_broken_links.extend([f"{dead}  (from {bn})" for dead in dead_links_in_file])
        else:
            # Content pages: remove from related: and Connections, keep inline references as broken
            with open(f, 'r', encoding='utf-8') as fh:
                lines = fh.readlines()
            new_lines = []
            removed = set()
            for line in lines:
                skip = False
                for dead in dead_links_in_file:
                    # Remove from related: list items and Connections sections
                    if line.strip().startswith('- ') and f'[[{dead}]]' in line:
                        skip = True
                        removed.add(dead)
                        break
                if not skip:
                    new_lines.append(line)
            if removed:
                with open(f, 'w', encoding='utf-8') as fh:
                    fh.writelines(new_lines)
                fixed_broken_links.extend([f"{dead}  (from {bn})" for dead in removed])
            # Auto-fix remaining inline broken links: unwrap [[dead link]] → plain text
            remaining = set(dead_links_in_file) - removed
            if remaining:
                with open(f, 'r', encoding='utf-8') as fh:
                    text = fh.read()
                for dead in remaining:
                    text = text.replace(f'[[{dead}]]', dead)
                    text = re.sub(r'\[\[' + re.escape(dead) + r'\|([^\]]+)\]\]', r'\1', text)
                with open(f, 'w', encoding='utf-8') as fh:
                    fh.write(text)
                fixed_broken_links.extend([f"{dead}  (from {bn}, unwrapped to plain text)" for dead in remaining])

check("Broken wikilinks (auto-fixed)", fixed_broken_links, fixed=True)

# ═══════════════════════════════════════════════════
header("2. RAW ↔ WIKI MAPPING")
# ═══════════════════════════════════════════════════

# Check broken raw_path references (handles both raw_path and raw_paths)
broken_paths = []
wiki_raw_paths = set()  # all raw_paths referenced by wiki pages
wiki_urls = set()  # all urls in wiki pages
for f in wiki_files:
    fm, _ = extract_frontmatter(f)
    # Collect all raw paths (single or list)
    paths = []
    if 'raw_path' in fm and fm['raw_path']:
        paths = [fm['raw_path']] if isinstance(fm['raw_path'], str) else fm['raw_path']
    if 'raw_paths' in fm:
        rps = fm['raw_paths']
        paths += rps if isinstance(rps, list) else [rps]
    for rp in paths:
        if rp:
            wiki_raw_paths.add(rp)
            full_rp = os.path.join(KB, rp)
            if not os.path.exists(full_rp):
                broken_paths.append(f"{os.path.basename(f)} → {rp}")
    # Collect all urls (single or list)
    urls = []
    if 'url' in fm and fm['url']:
        urls = [fm['url']] if isinstance(fm['url'], str) else fm['url']
    if 'urls' in fm:
        us = fm['urls']
        urls += us if isinstance(us, list) else [us]
    for u in urls:
        wiki_urls.add(u.rstrip('/'))
check("Broken raw_path (wiki → raw file missing)", broken_paths)

# Check wiki located in a category dir that disagrees with its raw_path's
# category. Witnessed on the Windows VM matrix (AT-4): a youtube video raw
# (raw/videos/) was given a wiki under wiki/format/webpages/ because
# create_wiki_page matched the category with forward slashes against a native
# backslash raw_path (`raw\videos\...`) → miscategorized as a webpage. Source
# fixed in wiki_page._source_type_and_subdir_from_raw_path (+ test_wiki_page_
# category_path.py); this surfaces any legacy stragglers for re-creation.
miscategorized_wiki = []
try:
    from config import raw_categories, wiki_format_dir
    _cat_to_wiki_sub = {c: os.path.basename(wiki_format_dir(c)) for c in raw_categories()}
    for f in wiki_files:
        rel = os.path.relpath(f, KB).replace('\\', '/')
        m = re.search(r'wiki/format/([^/]+)/', rel)
        if not m:
            continue
        actual_sub = m.group(1)
        fm, _ = extract_frontmatter(f)
        rp = fm.get('raw_path')
        if not rp or not isinstance(rp, str):
            continue
        rp_norm = rp.replace('\\', '/')
        expected_sub = None
        for cat_name in raw_categories():
            if f'/{cat_name}/' in rp_norm or rp_norm.startswith(f'{cat_name}/'):
                expected_sub = _cat_to_wiki_sub.get(cat_name)
                break
        if expected_sub and actual_sub != expected_sub:
            miscategorized_wiki.append(
                f"{os.path.basename(f)} in format/{actual_sub} but raw_path "
                f"is {rp} (expected format/{expected_sub})"
            )
except Exception as _e:
    print(f"  WARNING: miscategorization check skipped: {_e}", file=sys.stderr)
# Gate the check() on a non-empty result so total_checks stays byte-identical to
# the frozen Bash oracle (kb-legacy, which has no such check) on vaults with none
# — same discipline as the browser_captured_* checks below.
if miscategorized_wiki:
    check("Wiki miscategorized vs raw_path category (re-create to relocate)", miscategorized_wiki)

# Check raw files with no wiki page
# Video course transcripts are OK if referenced in the course wiki page body
# Match by file path references OR by YouTube video ID in watch links
course_transcript_refs = set()
course_video_ids = set()
for f in wiki_files:
    _, content = extract_frontmatter(f)
    for ref in re.findall(r'raw/videos/video-[a-zA-Z0-9_-]+\.md', content):
        course_transcript_refs.add(ref)
    # Also collect YouTube video IDs from [Watch](url) links in course pages
    for vid_id in re.findall(r'youtube\.com/watch\?v=([a-zA-Z0-9_-]+)', content):
        course_video_ids.add(vid_id)

orphan_raw = []
orphan_raw_fixed = []
for raw_rel, raw_url in raw_files.items():
    # Check if any wiki page references this raw file by path or URL
    if raw_rel in wiki_raw_paths:
        continue
    if raw_rel in course_transcript_refs:
        continue
    if raw_url and raw_url.rstrip('/') in wiki_urls:
        continue
    # Check if this is a video transcript whose YouTube ID is referenced in a course page
    vid_match = re.match(r'raw/videos/video-([a-zA-Z0-9_-]+)\.md', raw_rel)
    if vid_match and vid_match.group(1) in course_video_ids:
        continue
    # Skip raw files marked as merged-into another page. Without this,
    # orphan synthesis recreates a wiki page for content that was just
    # consolidated by `kb merge`, producing the duplicate-URL ↔ orphan-
    # synth oscillation that flapped _Contents.md between two states.
    abs_raw_check = os.path.join(KB, raw_rel)
    try:
        with open(abs_raw_check, 'r', encoding='utf-8') as _fh:
            _head = _fh.read(800)
        if re.search(r'^merged_into\s*:', _head, re.MULTILINE):
            continue
    except (IOError, UnicodeDecodeError):
        pass
    # Auto-fix: delete thin orphan raw files (<500 bytes of actual body).
    # Body detection: legacy raws had a `## Content` marker delimiting
    # the body; canonical-writer raws (write_raw_page) emit just
    # frontmatter + H1 + body with no marker. Detect both formats —
    # without the dual-format check, the canonical writer's output was
    # mistakenly seen as having 0-byte bodies and trashed.
    abs_raw = os.path.join(KB, raw_rel)
    try:
        with open(abs_raw, 'r', encoding='utf-8') as fh:
            raw_content = fh.read()
        if '## Content' in raw_content:
            # Legacy format
            parts = raw_content.split('## Content', 1)
            body = parts[1].strip() if len(parts) > 1 else ''
        else:
            # Canonical writer format: body is everything after the
            # closing `---` of frontmatter. If no frontmatter, body is
            # the whole file.
            fm_match = re.match(r'^---\s*\n.*?\n---\s*\n', raw_content, re.DOTALL)
            body = raw_content[fm_match.end():].strip() if fm_match else raw_content.strip()
        if len(body) < 500:
            import datetime as _dt_raw
            trash_dir = os.path.join(KB, '.kb-trash', f"{_dt_raw.datetime.now().strftime('%Y%m%d_%H%M%S')}_thin_raw")
            os.makedirs(trash_dir, exist_ok=True)
            import shutil
            shutil.move(abs_raw, os.path.join(trash_dir, os.path.basename(abs_raw)))
            # Atomically move matching asset directory (issue #113). Lint
            # otherwise surfaces the orphan asset dir on the next pass —
            # an issue caused by THIS lint pass, not a real KB problem.
            slug = os.path.splitext(os.path.basename(raw_rel))[0]
            asset_dir = os.path.join(KB, 'raw', 'assets', slug)
            if os.path.isdir(asset_dir):
                try:
                    shutil.move(asset_dir, os.path.join(trash_dir, os.path.basename(asset_dir)))
                except OSError:
                    pass
            # Also trash the binary side-file (PDF/DOCX/etc.) if present —
            # same atomicity argument as the asset dir.
            for ext in ('.pdf', '.docx', '.xlsx', '.pptx', '.epub'):
                side = abs_raw[:-3] + ext  # replace .md with .<ext>
                if os.path.isfile(side):
                    try:
                        shutil.move(side, os.path.join(trash_dir, os.path.basename(side)))
                    except OSError:
                        pass
            orphan_raw_fixed.append(f"{raw_rel} (thin content, trashed)")
            continue
    except: pass

    # Auto-fix: synthesize a wiki page from the raw content (fallback path,
    # no LLM required). This recovers orphans left behind when capture ran
    # but wiki creation didn't — a common state after a mid-ingest interrupt.
    # We use _find_orphan_raws-style URL extraction (body first, then slug)
    # so YouTube playlist IDs and other case-sensitive tokens survive.
    try:
        from kb_commands import _extract_url_from_raw_body, _reconstruct_url_from_slug
        from wiki_page import build_fallback_data, build_wiki_page, find_related_topics
        # URL resolution strategy:
        #   - Tweets: slug reconstruction first (case-preserved, deterministic),
        #     then body extraction. Body extractor now prioritizes `source:`
        #     frontmatter from the canonical writer, which is deterministic —
        #     the historical concern about picking citation URLs from tweet
        #     bodies only applied when bare-URL scan was used. The slug regex
        #     fails on usernames containing underscores (e.g. pseudo_sid26
        #     → pseudo-sid26 in slug) so the body fallback is required.
        #   - Everything else: body extraction first (case preservation for
        #     YouTube playlist IDs etc.), then slug reconstruction.
        stem_for_rel = os.path.splitext(os.path.basename(raw_rel))[0]
        is_tweet = stem_for_rel.startswith('x-com-') or stem_for_rel.startswith('twitter-com-')
        if is_tweet:
            synthesis_url = _reconstruct_url_from_slug(raw_rel) or _extract_url_from_raw_body(abs_raw)
        else:
            synthesis_url = _extract_url_from_raw_body(abs_raw) or _reconstruct_url_from_slug(raw_rel)
        if synthesis_url:
            # Determine source_type from the raw category path
            from config import raw_categories
            cat_source_type = None
            cat_name_for_subdir = None
            for cname, cfg in raw_categories().items():
                if f'/{cname}/' in raw_rel or raw_rel.startswith(f'{cname}/'):
                    cat_source_type = cfg['source_type']
                    cat_name_for_subdir = cname
                    break
            if cat_source_type and cat_name_for_subdir:
                # Title hint: first non-trivial H1 in the raw
                hint_title = None
                h1 = re.search(r'^#\s+(.+)$', raw_content, re.MULTILINE)
                if h1 and h1.group(1).strip().lower() not in ('tweet', 'untitled page', 'untitled'):
                    hint_title = h1.group(1).strip()
                data = build_fallback_data(raw_content, synthesis_url, cat_source_type, hint_title)
                # Find related topic pages for cross-linking
                try:
                    related_topics = find_related_topics(data['title'], KB)
                    data['related'] = (data.get('related') or []) + related_topics
                except Exception:
                    pass
                # Build the wiki page
                wiki_subdir = os.path.join('wiki', 'format', cat_name_for_subdir)
                wiki_dir = os.path.join(KB, wiki_subdir)
                os.makedirs(wiki_dir, exist_ok=True)
                # Pick a filename from the (now-normalized) title
                safe_name = re.sub(r'[/*?"<>|]', '', data['title'])[:75]
                wiki_path = os.path.join(wiki_dir, safe_name + '.md')
                if not os.path.exists(wiki_path):
                    # Also search by URL — an existing wiki page might already
                    # synthesize this URL under a different filename.
                    existing_for_url = None
                    if synthesis_url:
                        norm_url = synthesis_url.rstrip('/')
                        for cand in glob.glob(os.path.join(wiki_dir, '*.md')):
                            try:
                                cand_fm, _ = extract_frontmatter(cand)
                            except Exception:
                                continue
                            cand_url = (cand_fm.get('url') or '').strip().strip('"').rstrip('/')
                            cand_urls = cand_fm.get('urls', [])
                            if isinstance(cand_urls, str):
                                cand_urls = [cand_urls]
                            if cand_url == norm_url or any(
                                (u or '').strip().strip('"').rstrip('/') == norm_url for u in cand_urls
                            ):
                                existing_for_url = cand
                                break
                    if existing_for_url:
                        wiki_path = existing_for_url  # fall through to raw-link merge below
                if os.path.exists(wiki_path):
                    # An existing wiki page covers this URL/title. Instead of
                    # duplicating, append the orphan raw to its raw_paths list.
                    with open(wiki_path, 'r', encoding='utf-8') as fh:
                        page_text = fh.read()
                    # Parse current raw paths
                    existing_fm, _ = extract_frontmatter(wiki_path)
                    current_paths = []
                    if isinstance(existing_fm.get('raw_path'), str) and existing_fm['raw_path']:
                        current_paths.append(existing_fm['raw_path'])
                    if isinstance(existing_fm.get('raw_paths'), list):
                        current_paths.extend(existing_fm['raw_paths'])
                    if raw_rel in current_paths:
                        continue  # already linked
                    new_paths = current_paths + [raw_rel]
                    # Convert to raw_paths: list form (replacing single raw_path if present)
                    page_text = re.sub(
                        r'^raw_path:\s*"[^"\n]*"\s*\n', '', page_text, count=1, flags=re.MULTILINE
                    )
                    page_text = re.sub(
                        r'^raw_paths:\s*\n(?:\s+-\s*"[^"\n]*"\s*\n)+', '', page_text, count=1, flags=re.MULTILINE
                    )
                    paths_block = 'raw_paths:\n' + ''.join(
                        f'  - "{p}"\n' for p in dict.fromkeys(new_paths)  # dedupe
                    )
                    # Insert after the source_type line (frontmatter early)
                    page_text = re.sub(
                        r'^(source_type:[^\n]+\n)', r'\1' + paths_block,
                        page_text, count=1, flags=re.MULTILINE,
                    )
                    with open(wiki_path, 'w', encoding='utf-8') as fh:
                        fh.write(page_text)
                    orphan_raw_fixed.append(f"{raw_rel} (linked to existing {os.path.basename(wiki_path)})")
                    continue
                # No collision — create a new wiki page. Validate via the
                # canonical schema gate and write atomically (tmp +
                # rename) so a crash mid-synthesis never leaves a
                # half-written page that lint then has to puzzle out.
                page_text = build_wiki_page(
                    title=data['title'],
                    summary=data['summary'],
                    tags=data['tags'],
                    related=data['related'],
                    body=data['body'],
                    source_type=cat_source_type,
                    raw_path=raw_rel,
                    url=synthesis_url,
                )
                from wiki_schema import validate_wiki_frontmatter as _vwf, SchemaError as _SE  # type: ignore
                try:
                    _vwf(page_text)
                except _SE as _e:
                    orphan_raw.append(f"{raw_rel} (synthesis schema error: {_e})")
                    continue
                tmp_path = wiki_path + '.tmp'
                with open(tmp_path, 'w', encoding='utf-8') as fh:
                    fh.write(page_text)
                os.replace(tmp_path, wiki_path)
                orphan_raw_fixed.append(f"{raw_rel} (synthesized → {os.path.basename(wiki_path)})")
                continue
    except Exception as _e:
        # Synthesis is best-effort. If it fails, fall through to reporting.
        pass

    orphan_raw.append(f"{raw_rel}  ({raw_url or 'no url'})")
check("Orphan raw files (auto-fixed: thin trashed / orphans synthesized)", orphan_raw_fixed, fixed=True)
check("Orphan raw files (no wiki page)", orphan_raw)

# Check wiki pages with empty/missing raw_path (format pages only)
# Entities, topics, comparisons, and course aggregation pages legitimately have empty raw_paths
missing_raw_path = []
missing_raw_path_fixed = []
for f in wiki_files:
    if '/topics/' in f or '/dashboards/' in f or '/entities/' in f or '/comparisons/' in f or '/insights/' in f or '/journal/' in f or '/keywords/' in f or '/profile/' in f or '/exports/' in f or '/sessions/' in f:
        continue
    bn_rp = os.path.basename(f)
    if bn_rp.startswith('_'):
        continue
    fm, _ = extract_frontmatter(f)
    st = fm.get('source_type', '')
    if st in ('topic', 'entity', 'comparison', 'insight', 'journal', 'keyword', 'project'):
        continue
    # Video aggregate pages (course pages covering multiple lectures) don't have a single raw file
    if st == 'video' and '/videos/' in f:
        _, body = extract_frontmatter(f)
        if 'Lecture' in body or 'lecture' in body:
            continue  # Course aggregate page — expected to have no single raw_path
    # Auto-fix empty-string raw_path (LLM wrote "" instead of omitting)
    rp = fm.get('raw_path', '')
    if rp == '' or rp == '""':
        # Remove the empty string — no raw file is fine for metadata-only pages
        with open(f, 'r', encoding='utf-8') as fh:
            text = fh.read()
        text = re.sub(r'raw_path:\s*""?\s*\n', 'raw_path:\n', text)
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(text)
        rp = ''

    # Check both raw_path (single) and raw_paths (list)
    # Empty raw_path is valid for metadata-only ingests (no raw file was captured)
    has_path = False
    if rp and rp not in ('', '""', "''"):
        has_path = True
    rps = fm.get('raw_paths', [])
    if isinstance(rps, list) and len(rps) > 0:
        has_path = True
    if not has_path:
        # Only flag if there's a URL that SHOULD have a raw file
        wiki_url = fm.get('url', '')
        if wiki_url and st in ('paper', 'repo', 'webpage'):
            # Check if a raw file exists for this URL
            raw_exists = False
            for raw_dir in ['raw/papers', 'raw/webpages', 'raw/repos']:
                for rf in glob.glob(os.path.join(KB, raw_dir, '*.md')):
                    try:
                        with open(rf, 'r', encoding='utf-8') as rfh:
                            raw_head = rfh.read(500)
                        if wiki_url in raw_head:
                            # Auto-fix: set raw_path to the found file
                            rel_rf = os.path.relpath(rf, KB)
                            with open(f, 'r', encoding='utf-8') as fh:
                                text = fh.read()
                            text = re.sub(r'raw_path:.*\n', f'raw_path: "{rel_rf}"\n', text, count=1)
                            with open(f, 'w', encoding='utf-8') as fh:
                                fh.write(text)
                            missing_raw_path_fixed.append(f"{os.path.basename(f)} → {rel_rf}")
                            raw_exists = True
                            break
                    except: pass
                if raw_exists: break
            if not raw_exists:
                # Only flag if the page itself has minimal content (likely incomplete)
                _, body = extract_frontmatter(f)
                if len(body.strip()) < 200:
                    missing_raw_path.append(os.path.basename(f))
                # Pages with substantial content but no raw file are valid (e.g., gist, manual ingest)
check("Wiki pages: raw_path auto-matched", missing_raw_path_fixed, fixed=True)
check("Wiki pages with empty raw_path", missing_raw_path)

# Check duplicate raw_path references (two wiki pages → same raw file)
from collections import Counter
rp_counter = Counter()
for f in wiki_files:
    fm, _ = extract_frontmatter(f)
    # Collect all raw paths from both fields
    paths = []
    rp = fm.get('raw_path', '')
    if rp:
        paths.append(rp) if isinstance(rp, str) else paths.extend(rp)
    rps = fm.get('raw_paths', [])
    if isinstance(rps, list):
        paths.extend(rps)
    elif rps:
        paths.append(rps)
    for p in paths:
        if p:
            rp_counter[p] += 1
dup_raw = [f"{rp} (referenced {c} times)" for rp, c in rp_counter.items() if c > 1]
check("Duplicate raw_path (multiple wiki pages → same raw)", dup_raw)

# ═══════════════════════════════════════════════════
header("3. ORPHAN & CONNECTIVITY")
# ═══════════════════════════════════════════════════

def add_backlink(target_file, source_page_name):
    """Add a back-link to target_file's related: field. Creates related: if missing.

    Returns True if a write happened, False otherwise. The early-return for
    "already linked" must look at the *frontmatter only* — checking the full
    raw_content meant a body-level wikilink silently blocked the related:
    insert and the caller reported the pair as "fixed" on every lint run."""
    with open(target_file, 'r', encoding='utf-8') as fh:
        raw_content = fh.read()
    fm_match = re.match(r'^---\s*\n(.*?)\n---', raw_content, re.DOTALL)
    fm_block = fm_match.group(1) if fm_match else ''
    if f'[[{source_page_name}]]' in fm_block:
        return False
    lines = raw_content.split('\n')
    lines = [l + '\n' for l in lines]
    if lines and lines[-1].endswith('\n\n'):
        lines[-1] = lines[-1][:-1]
    backlink_line = f'  - "[[{source_page_name}]]"\n'
    in_fm = False; in_related = False; insert_idx = None; closing_fm_idx = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == '---' and not in_fm: in_fm = True; continue
        if stripped == '---' and in_fm:
            if in_related: insert_idx = idx
            closing_fm_idx = idx; break
        if in_fm and stripped.startswith('related:'): in_related = True; continue
        if in_related:
            if line.startswith('  - '): insert_idx = idx + 1
            else:
                if insert_idx is None: insert_idx = idx
                in_related = False
    if insert_idx is not None: lines.insert(insert_idx, backlink_line)
    elif closing_fm_idx is not None: lines.insert(closing_fm_idx, f'related:\n{backlink_line}')
    else: return False
    with open(target_file, 'w', encoding='utf-8') as fh: fh.writelines(lines)
    return True

# Build page name → file path map (used by multiple checks)
page_file_map = {}
for f in wiki_files:
    name = os.path.splitext(os.path.basename(f))[0]
    page_file_map[name] = f

# Orphan wiki pages (no inbound wikilinks)
# Exclude: dashboards, exports, journals, sessions, keywords, profile, guard files
orphan_exclude = {'/dashboards/', '/exports/', '/journal/', '/sessions/', '/keywords/', '/profile/', '/projects/'}
inbound = {}
for f in wiki_files:
    if any(ex in f for ex in orphan_exclude):
        continue
    bn = os.path.splitext(os.path.basename(f))[0]
    if bn.startswith('_'):
        continue
    inbound[bn] = 0
for f in wiki_files:
    _, content = extract_frontmatter(f)
    for link in re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', content):
        if link in inbound:
            inbound[link] += 1
orphan_names = sorted([n for n, c in inbound.items() if c == 0])

# Auto-fix orphans: find the best topic page for each and add cross-reference
# Topic pages are in wiki/topics/ and have many outbound links
topic_pages = {}
for f in wiki_files:
    if '/topics/' not in f: continue
    name = os.path.splitext(os.path.basename(f))[0]
    fm, content = extract_frontmatter(f)
    tags = fm.get('tags', [])
    if isinstance(tags, list):
        topic_pages[name] = {'path': f, 'tags': set(t for t in tags if isinstance(t, str))}

fixed_orphans = []
remaining_orphans = []
for orphan_name in orphan_names:
    # Find the orphan's file to get its tags
    orphan_file = page_file_map.get(orphan_name)
    if not orphan_file:
        remaining_orphans.append(orphan_name)
        continue
    fm, _ = extract_frontmatter(orphan_file)
    orphan_tags = set(fm.get('tags', []) if isinstance(fm.get('tags'), list) else [])

    # Find best matching topic by tag overlap OR title keyword match
    # Also check all wiki pages (not just topics) for keyword matching
    best_topic = None
    best_score = 0
    orphan_words = set(orphan_name.lower().split()) - {'—', '-', 'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with'}
    # First try topics (preferred)
    for topic_name, topic_info in topic_pages.items():
        score = 0
        tag_overlap = len(orphan_tags & topic_info['tags'])
        score += tag_overlap * 2
        topic_words = set(topic_name.lower().split()) - {'—', '-', 'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with'}
        word_overlap = len(orphan_words & topic_words)
        score += word_overlap * 3  # weight topic matches higher
        if score > best_score:
            best_score = score
            best_topic = topic_name

    # If no topic match, try any wiki page with 3+ word overlap
    if best_score == 0:
        for pg_name, pg_file in page_file_map.items():
            if pg_name == orphan_name: continue
            if '/topics/' in pg_file: continue  # already checked
            pg_words = set(pg_name.lower().split()) - {'—', '-', 'the', 'a', 'an', 'of', 'for', 'and', 'in', 'on', 'to', 'with'}
            word_overlap = len(orphan_words & pg_words)
            if word_overlap >= 2 and word_overlap > best_score:
                best_score = word_overlap
                best_topic = pg_name

    if best_topic and best_score > 0:
        # Add orphan to topic's related: and Connections
        if best_topic not in topic_pages: continue
        topic_file = topic_pages[best_topic]['path']
        bl_result = add_backlink(topic_file, orphan_name)
        if bl_result:
            # Also add to body Connections section
            with open(topic_file, 'r', encoding='utf-8') as fh:
                text = fh.read()
            if f'[[{orphan_name}]]' not in text:
                if '## Connections' in text:
                    text = text.replace('## Connections\n', f'## Connections\n\n- [[{orphan_name}]]\n', 1)
                else:
                    text = text.rstrip() + f'\n\n## Connections\n\n- [[{orphan_name}]]\n'
                with open(topic_file, 'w', encoding='utf-8') as fh:
                    fh.write(text)
            fixed_orphans.append(f"{orphan_name} → [[{best_topic}]]")
        else:
            remaining_orphans.append(orphan_name)
    else:
        remaining_orphans.append(orphan_name)

check("Orphan wiki pages (auto-linked to topics)", fixed_orphans, fixed=True)
check("Orphan wiki pages (no matching topic)", remaining_orphans)

# Fix `related: []` → `related:` (empty inline list breaks YAML list items below it)
empty_related_fixed = []
for f in wiki_files:
    with open(f, 'r', encoding='utf-8') as fh:
        content = fh.read()
    if 'related: []' in content:
        # Check if there are list items after `related: []`
        lines = content.split('\n')
        fixed = False
        for idx, line in enumerate(lines):
            if line.strip() == 'related: []':
                # Check if next line is a list item
                if idx + 1 < len(lines) and lines[idx + 1].strip().startswith('- '):
                    lines[idx] = 'related:'
                    fixed = True
                    break
                else:
                    # Truly empty — just fix the syntax
                    lines[idx] = 'related:'
                    fixed = True
                    break
        if fixed:
            # Also deduplicate related items
            new_lines = []
            seen_related = set()
            in_related = False
            for line in lines:
                if line.strip() == 'related:':
                    in_related = True
                    new_lines.append(line)
                    continue
                if in_related and line.startswith('  - '):
                    if line.strip() not in seen_related:
                        seen_related.add(line.strip())
                        new_lines.append(line)
                    continue
                else:
                    in_related = False
                new_lines.append(line)
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(new_lines))
            empty_related_fixed.append(os.path.basename(f))
check("Fixed related: [] syntax", empty_related_fixed, fixed=True)

# Unidirectional cross-references (A's related: lists B, but B's related: doesn't list A)
unidirectional = []
unidirectional_skipped = []
# page_file_map already built above in section 3

# Build map: page name → set of related page names from frontmatter
related_map = {}
for f in wiki_files:
    fm, _ = extract_frontmatter(f)
    name = os.path.splitext(os.path.basename(f))[0]
    related_raw = fm.get('related', [])
    related_names = set()
    if isinstance(related_raw, list):
        for r in related_raw:
            m = re.match(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', str(r))
            if m:
                related_names.add(m.group(1))
            elif isinstance(r, str) and r.strip():
                related_names.add(r.strip())
    related_map[name] = related_names

for page_a, related_set in related_map.items():
    # Skip topics/dashboards/entities as source — they're hubs linking to many pages
    source_path = page_file_map.get(page_a, '')
    if '/dashboards/' in source_path or '/topics/' in source_path or '/entities/' in source_path:
        continue
    for page_b in related_set:
        # Skip topic/dashboard/entity pages as targets — they're hubs, not peers
        target_path = page_file_map.get(page_b, '')
        if '/topics/' in target_path or '/dashboards/' in target_path or '/entities/' in target_path:
            continue
        # page_b exists as a wiki page but doesn't link back to page_a
        if page_b in page_file_map and page_a not in related_map.get(page_b, set()):
            target_file = page_file_map[page_b]
            if add_backlink(target_file, page_a):
                # Update in-memory map so we don't fix the same pair twice
                related_map.setdefault(page_b, set()).add(page_a)
                unidirectional.append(f"{page_a} \u2192 {page_b} (auto-fixed)")
            else:
                # add_backlink returned False \u2014 the pair already has a body-level
                # mention but no frontmatter related: entry. Surface as a
                # non-blocking warning instead of a "fixed" claim, so lint stays
                # idempotent.
                unidirectional_skipped.append(f"{page_a} \u2192 {page_b} (linked in body, missing in related:)")
check("Unidirectional cross-references", unidirectional, fixed=True)
check("Unidirectional cross-references \u2014 body link present but related: missing (manual)", unidirectional_skipped)

# ═══════════════════════════════════════════════════
header("4. FRONTMATTER VALIDATION")
# ═══════════════════════════════════════════════════

CANONICAL_TAGS = {
    'agent', 'ai-agents', 'ai-engineering', 'ai-platforms', 'architecture', 'book', 'chart', 'cs153',
    'claude-code', 'competitor', 'course', 'curated-list', 'dashboard',
    'deep-learning', 'devsecops', 'diagram', 'exercises', 'export', 'finance', 'insight',
    'interview-prep', 'journal', 'karpathy', 'keyword-index', 'knowledge-base',
    'knowledge-management', 'learning', 'llm', 'marketing', 'math', 'mcp', 'memory', 'meta', 'ml',
    'obsidian', 'offensive-security', 'paper', 'pdf', 'platform-engineering',
    'product-design', 'productivity', 'profile', 'project',
    'prompt-engineering', 'python', 'rag', 'repo', 'second-brain', 'security', 'session',
    'skills', 'slides', 'tool', 'tools', 'topic', 'user-model', 'video', 'visualization',
    'vulnerability-research', 'webpage', 'workflow', 'writing-style',
}

VALID_SOURCE_TYPES = {
    'paper', 'repo', 'webpage', 'image', 'video', 'topic', 'entity', 'comparison',
    'insight', 'journal', 'keyword', 'project',
}

# Tag aliases: user-configured mapping from non-canonical → canonical.
# When lint finds a non-canonical tag, it first checks aliases and rewrites
# in place; only truly unmapped tags bubble up as "need attention".
from config import naming as _naming
_TAG_ALIASES = {k: v for k, v in _naming().get('tag_aliases', {}).items()
                if not k.startswith('_')}

def _rewrite_tags_in_frontmatter(text, replacements):
    """Given a wiki page's full text and a {old: new} tag replacement map,
    rewrite inline `tags: [a, b]` and YAML-list `tags:\\n  - a\\n  - b` forms.
    Returns the new text and True/False for whether a change was made."""
    changed = [False]
    def _apply(tag_list):
        out = []
        for t in tag_list:
            new = replacements.get(t, t)
            if new != t: changed[0] = True
            if new not in out: out.append(new)
        return out
    # Inline form
    def _repl_inline(m):
        inner = m.group(1)
        tags = [t.strip().strip('"').strip("'") for t in inner.split(',') if t.strip()]
        return 'tags: [' + ', '.join(_apply(tags)) + ']'
    text = re.sub(r'^tags:\s*\[([^\]]*)\]', _repl_inline, text, count=1, flags=re.MULTILINE)
    # YAML list form (less common in Athena, but handle it)
    def _repl_list(m):
        header, block = m.group(1), m.group(2)
        tags = [re.sub(r'^\s*-\s*', '', line).strip().strip('"').strip("'")
                for line in block.split('\n') if line.strip()]
        new_tags = _apply(tags)
        new_block = '\n'.join(f'  - {t}' for t in new_tags) + '\n'
        return header + new_block
    text = re.sub(r'(^tags:\s*\n)((?:\s+-\s*.+\n)+)', _repl_list, text, count=1, flags=re.MULTILINE)
    return text, changed[0]

non_canonical_tags = set()
aliased_count = 0
missing_tags = []
invalid_source_type = []
missing_title = []
missing_url = []
missing_date = []
suspect_non_athena = []

for f in wiki_files:
    if '/dashboards/' in f:
        continue
    bn = os.path.basename(f)
    if bn.startswith('_'):
        continue
    fm, content = extract_frontmatter(f)

    # Check title
    if not fm.get('title'):
        missing_title.append(bn)

    # Check source_type
    st = fm.get('source_type', '')
    if st and st not in VALID_SOURCE_TYPES:
        invalid_source_type.append(f"{bn}: {st}")

    # Check URL (skip topic/entity/comparison/image pages; accept url or urls)
    has_url = bool(fm.get('url')) or bool(fm.get('urls'))
    if st and st not in ('topic', 'entity', 'comparison', 'image', 'insight', 'journal', 'keyword', 'project') and not has_url:
        missing_url.append(bn)

    # Skip schema enforcement for redirect stubs — they are minimal-by-design
    # forwarders. Adding date_added/tags/title to them produced the
    # "Invalid properties" + duplicate-key bug we hit on the Trivy stub.
    _redir = fm.get('redirect', '')
    _is_stub = (_redir is True) or (str(_redir).strip().strip('"').strip("'").lower() == 'true')
    if _is_stub:
        continue

    # Check and auto-fix date_added
    if not fm.get('date_added'):
        import datetime as _dt
        file_date = _dt.date.fromtimestamp(os.path.getmtime(f)).isoformat()
        with open(f, 'r', encoding='utf-8') as fh:
            text = fh.read()
        if 'date_added:' in text:
            # Field exists but parsed empty — replace the existing line.
            text = re.sub(r'date_added:.*', f'date_added: {file_date}', text, count=1)
        else:
            # Field is absent — insert exactly ONCE, before the closing ---.
            # The previous logic inserted twice (once after opening ---, once
            # before closing ---), producing a duplicate-key YAML bug that
            # Obsidian rendered as "Invalid properties".
            fm_end = text.find('\n---', text.find('---') + 3)
            if fm_end > 0:
                text = text[:fm_end] + f'\ndate_added: {file_date}' + text[fm_end:]
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(text)
        missing_date.append(f"{bn} → {file_date}")

    # Check and auto-fix missing title
    if not fm.get('title'):
        title = os.path.splitext(bn)[0]
        with open(f, 'r', encoding='utf-8') as fh:
            text = fh.read()
        if 'title:' in text:
            text = re.sub(r'title:.*', f'title: "{title}"', text, count=1)
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(text)
        missing_title.append(f"{bn} → \"{title}\"")

    # Check tags — handle both inline [a, b] and YAML list formats
    tags = []
    tm_inline = re.search(r'^tags:\s*\[([^\]]*)\]', content, re.MULTILINE)
    tm_list = re.findall(r'^tags:\s*$\n((?:\s+-\s*.+\n)*)', content, re.MULTILINE)
    if tm_inline:
        tags = [t.strip().strip('"').strip("'") for t in tm_inline.group(1).split(',') if t.strip()]
    elif tm_list:
        tags = [re.sub(r'^\s*-\s*', '', line).strip().strip('"').strip("'") for line in tm_list[0].strip().split('\n') if line.strip()]

    if tags:
        # First: apply user-configured aliases to map known non-canonical →
        # canonical (idempotent, edits file). Remaining unmapped tags are
        # then reported as "Non-canonical tags".
        needs_rewrite = {t: _TAG_ALIASES[t] for t in tags
                         if t and t in _TAG_ALIASES and _TAG_ALIASES[t] != t}
        if needs_rewrite:
            with open(f, 'r', encoding='utf-8') as fh:
                text = fh.read()
            new_text, did = _rewrite_tags_in_frontmatter(text, needs_rewrite)
            if did:
                with open(f, 'w', encoding='utf-8') as fh:
                    fh.write(new_text)
                aliased_count += 1
                # Re-evaluate tags after rewrite
                tags = [_TAG_ALIASES.get(t, t) for t in tags]
                tags = list(dict.fromkeys(tags))  # dedupe preserving order
        for t in tags:
            if t and t not in CANONICAL_TAGS:
                non_canonical_tags.add(f"{t}  (in {bn})")
    elif 'tags:' not in content.split('---')[1] if '---' in content else '':
        # No tags field at all — auto-fix
        with open(f, 'r', encoding='utf-8') as fh:
            text = fh.read()
        fm_end = text.find('\n---', text.find('---') + 3)
        if fm_end > 0 and 'tags:' not in text[:fm_end]:
            text = text[:fm_end] + '\ntags: []' + text[fm_end:]
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(text)
            missing_tags.append(f"{bn}")

if aliased_count:
    print(f"  ✓ Tag aliases applied: {aliased_count} pages rewrote non-canonical tags via naming.tag_aliases")
    auto_fixed += aliased_count
check("Non-canonical tags (add to naming.tag_aliases or canonical list)", sorted(non_canonical_tags))
check("Missing tags (auto-added empty)", missing_tags, fixed=True)
check("Missing title (auto-fixed from filename)", missing_title, fixed=True)
check("Invalid source_type", invalid_source_type)
check("Missing url (non-topic pages)", missing_url)
check("Missing date_added (auto-fixed from file date)", missing_date, fixed=True)

# Detect pages likely created by a non-Athena session (missing 3+ standard fields)
for f in wiki_files:
    if '/dashboards/' in f or '/keywords/' in f:
        continue
    bn = os.path.basename(f)
    if bn.startswith('_'):
        continue
    fm, content = extract_frontmatter(f)
    if not fm:
        suspect_non_athena.append(f"{bn} (no frontmatter)")
        continue
    # Redirect stubs are minimal-by-design forwarders — skip the standard
    # field check so they don't false-positive as "non-Athena content".
    _redir = fm.get('redirect')
    if isinstance(_redir, str) and _redir.strip().strip('"').strip("'").lower() == 'true':
        continue
    missing_count = 0
    if not fm.get('title'): missing_count += 1
    if not fm.get('source_type'): missing_count += 1
    if not fm.get('date_added'): missing_count += 1
    if not fm.get('tags'): missing_count += 1
    if not fm.get('summary'): missing_count += 1
    has_connections = bool(re.search(r'^## Connections', content, re.MULTILINE))
    if not has_connections: missing_count += 1
    if missing_count >= 4:
        suspect_non_athena.append(f"{bn} (missing {missing_count}/6 standard fields)")
check("Suspect non-Athena pages (missing 4+ standard fields)", suspect_non_athena)

# ═══════════════════════════════════════════════════
header("5. INDEX CONSISTENCY")
# ═══════════════════════════════════════════════════

# Check index.md counts match reality
index_path = os.path.join(KB, 'index.md')
try:
    with open(index_path, 'r') as f:
        index_content = f.read()

    m = re.search(r'(\d+)\s+sources?\s*·\s*(\d+)\s+wiki pages?', index_content)
    if m:
        claimed_sources = int(m.group(1))
        claimed_wiki = int(m.group(2))
        actual_sources = len(raw_files)
        actual_wiki = len([f for f in glob.glob(os.path.join(KB, 'wiki', '**', '*.md'), recursive=True)
                          if '.gitkeep' not in f and '_TEMPLATE' not in f])

        count_drift = []
        if claimed_sources != actual_sources or claimed_wiki != actual_wiki:
            # Auto-fix: update index.md counts
            new_line = f"{actual_sources} sources · {actual_wiki} wiki pages"
            index_content = re.sub(r'\d+\s+sources?\s*·\s*\d+\s+wiki pages?', new_line, index_content)
            with open(index_path, 'w') as fw:
                fw.write(index_content)
            if claimed_sources != actual_sources:
                count_drift.append(f"Sources: {claimed_sources} → {actual_sources}")
            if claimed_wiki != actual_wiki:
                count_drift.append(f"Wiki pages: {claimed_wiki} → {actual_wiki}")
        check("Index count drift", count_drift, fixed=True)
    else:
        check("Index header parse", ["Could not parse source/wiki counts from index.md"])
except Exception as e:
    check("Index read", [str(e)])

# ═══════════════════════════════════════════════════
header("6. URL CONSISTENCY")
# ═══════════════════════════════════════════════════

# URL normalization — defer to url_canonical.canonicalize so the lint's
# duplicate detector agrees with the writer-side _find_wiki_page_for_url
# check. Without this alignment, the writer can prevent (e.g.) a LinkedIn
# /feed/update vs /posts duplicate while the lint silently accepts the
# same shape — direct file edits or non-Python write paths slip through.
def normalize_url(url):
    """Canonicalize a URL for duplicate-detection comparison.

    Falls back to a tracking-param-strip if url_canonical raises on a
    pathological URL — the lint must keep running over the rest of the
    vault even if a single row has a malformed URL."""
    if not url:
        return ''
    try:
        from url_canonical import canonicalize as _kanon  # type: ignore
        return _kanon(url).url
    except Exception:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(url.rstrip('/'))
        params = parse_qs(p.query)
        if any(d in p.netloc for d in ['linkedin.com', 'x.com', 'twitter.com', 'facebook.com']):
            return urlunparse(p._replace(query=''))
        noise = {'tab', 's', 't', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'ref', 'usp', 'rcm'}
        cleaned = {k: v for k, v in params.items() if k not in noise}
        clean_query = urlencode(cleaned, doseq=True)
        return urlunparse(p._replace(query=clean_query))

# Check for duplicate URLs — normalize before comparing (strip tracking params)
url_to_wiki = {}
dup_urls_fixed = []
dup_urls_remaining = []
trash_base = os.path.join(KB, '.kb-trash')
for f in wiki_files:
    fm, _ = extract_frontmatter(f)
    raw_url = fm.get('url', '').rstrip('/')
    url = normalize_url(raw_url) if raw_url else ''
    if url:
        if url in url_to_wiki:
            # Duplicate found — pick the page whose ORIGINAL URL has more
            # decoration (author handle, title slug, variant token), since
            # that's the user-meaningful share form. The URN-only form
            # (`/posts/ugcpost-<ID>`) typically comes from a degraded
            # capture (Web Clipper running on a partially-loaded page,
            # autoingest synthesizing from a thin raw, etc.) and produces
            # a generic title like "ClaudeBleed is not merely…" instead
            # of a descriptive author-attributed one. Tiebreaker: bigger
            # body. Bug surfaced 0.10.12 — the previous size-only rule
            # kept the wrong page because the chrome-heavy variant was
            # accidentally fatter than the cleaned canonical one.
            existing = url_to_wiki[url]
            existing_fm, _ = extract_frontmatter(existing)
            f_fm, _ = extract_frontmatter(f)
            existing_url = existing_fm.get('url', '')
            f_url = f_fm.get('url', '')
            # Richer = longer original URL (more decoration), then bigger body as tie.
            existing_score = (len(existing_url), os.path.getsize(existing))
            f_score = (len(f_url), os.path.getsize(f))
            keep = existing if existing_score >= f_score else f
            trash_file = f if keep == existing else existing
            # Move to trash. ALSO move the corresponding raw — otherwise
            # the orphan-raw lint check (#2) will synthesize a new wiki
            # from the trashed page's raw on the very next lint run,
            # producing the same duplicate over and over (the lint
            # churn loop discovered 0.10.12). The trash bundle keeps
            # wiki + raw together so `kb undo` restores both atomically.
            import datetime as _dt_dup
            trash_dir = os.path.join(trash_base, f"{_dt_dup.datetime.now().strftime('%Y%m%d_%H%M%S')}_dup_merge")
            os.makedirs(trash_dir, exist_ok=True)
            import shutil
            shutil.move(trash_file, os.path.join(trash_dir, os.path.basename(trash_file)))
            # Read the trashed wiki's frontmatter (from the trashed copy)
            # to find its raw_path, then trash that raw too.
            trashed_wiki_path = os.path.join(trash_dir, os.path.basename(trash_file))
            try:
                trashed_fm, _ = extract_frontmatter(trashed_wiki_path)
                trashed_raw_rel = trashed_fm.get('raw_path', '')
                # Sanity: raw_path must be inside raw/ AND must NOT match the
                # raw of the kept wiki (defense against same-raw mis-fixture
                # — never trash the raw the kept wiki still depends on).
                kept_fm, _ = extract_frontmatter(keep)
                kept_raw_rel = kept_fm.get('raw_path', '')
                if (trashed_raw_rel and trashed_raw_rel.startswith('raw/')
                        and trashed_raw_rel != kept_raw_rel):
                    trashed_raw_abs = os.path.join(KB, trashed_raw_rel)
                    if os.path.exists(trashed_raw_abs):
                        shutil.move(trashed_raw_abs, os.path.join(trash_dir, os.path.basename(trashed_raw_abs)))
            except Exception:
                pass  # raw cleanup is best-effort; never block the wiki merge
            dup_urls_fixed.append(f"{url}: kept [[{os.path.splitext(os.path.basename(keep))[0]}]], trashed {os.path.basename(trash_file)} + raw")
            url_to_wiki[url] = keep
        else:
            url_to_wiki[url] = f
check("Duplicate URLs (auto-merged)", dup_urls_fixed, fixed=True)

# Check that wiki URL matches raw file URL
url_mismatches = []
for f in wiki_files:
    fm, _ = extract_frontmatter(f)
    wiki_url = fm.get('url', '').rstrip('/')
    rp = fm.get('raw_path', '')
    if not wiki_url or not rp:
        continue
    full_rp = os.path.join(KB, rp)
    if not os.path.exists(full_rp):
        continue
    raw_url = extract_url_from_raw(full_rp)
    if raw_url and normalize_url(raw_url) != normalize_url(wiki_url):
        url_mismatches.append(f"{os.path.basename(f)}: wiki={wiki_url} raw={raw_url}")
check("URL mismatch (wiki url ≠ raw file url)", url_mismatches)

# GitHub repos captured via the Obsidian plugin's generic DOM walker land with
# clipped_via: browser-capture — every <img> force-sized to width="600", the
# README's markdown ![]() thumbnails dropped, and invisible spacer/icon images
# retained. The correct path (bin/kb-capture) fetches the real README via the
# GitHub API (or raw.githubusercontent) and rewrites image paths. Surface any
# repo raw still on the wrong path so it can be re-captured. NOT auto-fixed:
# re-capture is a network operation (discover-and-surface, never auto-fetch).
# Witnessed: roboflow/notebooks, 2026-06-08.
browser_captured_repos = []
for rf in sorted(glob.glob(os.path.join(KB, 'raw', 'repos', 'artifacts', '*.md'))):
    try:
        with open(rf, 'r', encoding='utf-8') as fh:
            head = fh.read(2000)
    except (IOError, UnicodeDecodeError):
        continue
    if re.search(r'^clipped_via:\s*"?browser-capture"?\s*$', head, re.MULTILINE):
        browser_captured_repos.append(
            f"{os.path.basename(rf)}: re-capture via `kb add <url>` "
            f"(DOM-walker images are degraded)")
# Python-only check (the frozen Bash oracle has none); the check() call is
# gated on a non-empty result so total_checks stays byte-identical to the
# oracle on vaults with no DOM-walked repos (the parity fixture seeds none).
if browser_captured_repos:
    check("GitHub repos captured via DOM walker (re-capture for correct images)",
          browser_captured_repos)

# X/Twitter status posts captured via the generic DOM walker land the same way
# (clipped_via: browser-capture). For a long-form X Article the visible tweet
# body is just a t.co pointer (lang="zxx"), so the walker titles the page with
# the shortlink and grabs a truncated preview. The correct path (fetch_tweet.py
# → cdn.syndication.twimg.com) returns the real author, full text, media, and
# the Article title/preview/cover. Surface any tweet raw still on the wrong
# path. Gated on the tweet source pattern — generic webpages legitimately use
# the DOM walker. NOT auto-fixed (re-capture is a network op). Witnessed:
# FakeMaidenMaker/status/2064900447375085823, 2026-06-12.
browser_captured_tweets = []
for rf in sorted(glob.glob(os.path.join(KB, 'raw', 'webpages', 'artifacts', '*.md'))):
    try:
        with open(rf, 'r', encoding='utf-8') as fh:
            head = fh.read(2000)
    except (IOError, UnicodeDecodeError):
        continue
    if not re.search(r'^clipped_via:\s*"?browser-capture"?\s*$', head, re.MULTILINE):
        continue
    if re.search(r'^source:\s*"?https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/',
                 head, re.MULTILINE):
        browser_captured_tweets.append(
            f"{os.path.basename(rf)}: re-capture via `kb add <url>` "
            f"(DOM-walker truncates tweets/X Articles)")
if browser_captured_tweets:
    check("X/Twitter posts captured via DOM walker (re-capture for full content)",
          browser_captured_tweets)

# X Articles stored preview-only (full body needs the cookie-reuse deep-capture).
# Identified by the article-preview marker line emitted by fetch_tweet. Surface
# (never auto-fetch) so they can be re-captured once an X session is imported.
x_article_preview = []
for rf in sorted(glob.glob(os.path.join(KB, 'raw', 'webpages', 'artifacts', '*.md'))):
    try:
        with open(rf, 'r', encoding='utf-8') as fh:
            txt = fh.read()
    except (IOError, UnicodeDecodeError):
        continue
    if 'This is a long-form X Article. Read the full piece:' in txt:
        x_article_preview.append(
            f"{os.path.basename(rf)}: preview-only X Article "
            f"(re-capture with an X session for the full body)")
if x_article_preview:
    check("X Articles stored preview-only (re-capture for full body)", x_article_preview)

# ═══════════════════════════════════════════════════
header("7. OBSIDIAN RENDER VERIFICATION")
# ═══════════════════════════════════════════════════

# Check if Obsidian Local REST API is available
import urllib.request, urllib.parse, ssl, json as _json
api_key_path = os.path.join(KB, '.obsidian-api-key')
obsidian_ok = False
if os.path.exists(api_key_path):
    with open(api_key_path, 'r') as f:
        api_key = f.read().strip()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request('https://localhost:27124/',
                                     headers={'Authorization': f'Bearer {api_key}'})
        resp = urllib.request.urlopen(req, timeout=3, context=ctx)
        if resp.status == 200:
            obsidian_ok = True
    except (IOError, OSError):
        pass

if obsidian_ok:
    render_issues = []
    render_fixed = []
    # Check each wiki page via the API
    for f in wiki_files:
        if '/dashboards/' in f:
            continue
        rel = os.path.relpath(f, KB)
        encoded = urllib.parse.quote(rel, safe='/')
        try:
            req = urllib.request.Request(
                f'https://localhost:27124/vault/{encoded}',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Accept': 'application/vnd.olrapi.note+json'
                })
            resp = urllib.request.urlopen(req, timeout=3, context=ctx)
            data = _json.loads(resp.read().decode())
            content = data.get('content', '')
            fm = data.get('frontmatter', {})
            bn = os.path.basename(f)
            title = fm.get('title', '')

            # Check 1+2: Duplicate heading — auto-fix by removing body H1
            # Only match EXACT line (not substring/prefix), and only report fixed if file changed
            page_name = os.path.splitext(bn)[0]
            has_dup_title = title and f'\n# {title}\n' in content
            has_dup_fname = f'\n# {page_name}\n' in content
            if has_dup_title or has_dup_fname:
                try:
                    with open(f, 'r', encoding='utf-8') as fh:
                        raw_text = fh.read()
                    original = raw_text
                    if has_dup_title and f'\n# {title}\n' in raw_text:
                        raw_text = raw_text.replace(f'\n# {title}\n', '\n', 1)
                    if has_dup_fname and f'\n# {page_name}\n' in raw_text:
                        raw_text = raw_text.replace(f'\n# {page_name}\n', '\n', 1)
                    if raw_text != original:
                        with open(f, 'w', encoding='utf-8') as fh:
                            fh.write(raw_text)
                        render_fixed.append(f"{bn}: removed duplicate H1")
                except:
                    render_issues.append(f"{bn}: duplicate heading (could not auto-fix)")

            # Check 3: Empty body (only frontmatter, no content)
            body_text = content.strip()
            if len(body_text) < 10:
                render_issues.append(f"{bn}: body is empty or nearly empty")

        except (IOError, OSError, ValueError):
            pass  # Skip pages that can't be read via API

    check("Obsidian render (auto-fixed duplicate H1)", render_fixed, fixed=True)
    check("Obsidian render issues", render_issues)
else:
    print("  - Obsidian REST API not available (skipped)")
    print("    Install plugin: Local REST API, save key to .obsidian-api-key")

# ═══════════════════════════════════════════════════
# CHECK: Search index health
# ═══════════════════════════════════════════════════
# CHECK: Contradiction detection
# ═══════════════════════════════════════════════════
total_checks += 1
pass  # Silent header
pass  # 8. Contradiction detection

try:
    sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
    from contradictions import detect_contradictions
    contras = detect_contradictions(KB)
    # Filter out false positives:
    # 1. Keyword index pages (aggregate different repos)
    # 2. Different entities compared by shared keyword (different URLs = different things)
    # Exclude keyword AND entity pages from contradiction checks
    # (they aggregate info from multiple sources — different numbers are expected)
    exclude_names = set()
    page_urls = {}
    for f in wiki_files:
        name = os.path.splitext(os.path.basename(f))[0]
        if '/keywords/' in f or '/entities/' in f or '/topics/' in f:
            exclude_names.add(name)
        fm, _ = extract_frontmatter(f)
        url = fm.get('url', '') or ''
        if isinstance(fm.get('urls'), list):
            url = fm['urls'][0] if fm['urls'] else ''
        page_urls[name] = url

    real_contras = []
    for c in contras:
        p1, p2 = c.get('page1', ''), c.get('page2', '')
        if p1 in exclude_names or p2 in exclude_names:
            continue
        # Different URLs = different entities, not a real contradiction
        u1, u2 = page_urls.get(p1, ''), page_urls.get(p2, '')
        if u1 and u2 and u1 != u2:
            continue
        # Same page comparing to itself with different values = real
        real_contras.append(c)
    if real_contras:
        pass  # Informational only — don't show to user
    elif contras:
        pass  # Silent when clean
    else:
        pass  # Silent when clean
except Exception as e:
    print(f"  ⚠  Contradiction check failed: {e}")

# ═══════════════════════════════════════════════════
total_checks += 1
pass  # Silent header
pass  # 9. Search index health

search_db = os.path.join(KB, '.athena', 'search.db')
if not os.path.exists(search_db):
    print("  ⚠  No search index. Run: kb index")
    issues += 1
else:
    try:
        sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
        from search import index_status as _idx_status
        info = _idx_status(KB)
        idx_pages = info.get('page_count', 0)

        # Count actual wiki pages on disk
        disk_pages = 0
        for r, d, f in os.walk(os.path.join(KB, 'wiki')):
            d[:] = [x for x in d if x != 'dashboards']
            for fn in f:
                if fn.endswith('.md') and not fn.startswith('_') and fn != '.gitkeep':
                    disk_pages += 1

        if idx_pages == disk_pages:
            pass  # Silent when clean
        else:
            diff = disk_pages - idx_pages
            # Auto-fix: rebuild index
            try:
                from search import update_index as _update_idx
                _update_idx(KB)
                # Re-read count after rebuild to verify
                info2 = _idx_status(KB)
                new_count = info2.get('page_count', 0)
                if new_count == disk_pages:
                    pass  # Index synced — routine maintenance, not an issue
                elif new_count != idx_pages:
                    pass  # Partially synced — will converge
                else:
                    # update_index didn't change the count — stale entries need full rebuild
                    from search import build_index as _build_idx
                    _build_idx(KB)
                    pass  # Full rebuild done — routine maintenance
            except Exception as e:
                print(f"  ⚠  Index stale: {idx_pages} indexed vs {disk_pages} on disk ({diff:+d}). Rebuild failed: {e}")
                issues += 1

        emb = info.get('embedding_count', 0)
        provider = info.get('embedding_provider', 'none')
        if emb > 0:
            pass  # Silent when clean
        else:
            print(f"  ⚠  No embeddings — vector search disabled. Install fastembed: pip install fastembed")
            issues += 1

        # Check and auto-fix title/filename mismatches
        import sqlite3
        import re as _re
        import subprocess as _sp
        conn = sqlite3.connect(search_db)
        mismatches = []
        rows = conn.execute('SELECT rel_path, title FROM pages').fetchall()
        for rel_path, title in rows:
            fname = os.path.basename(rel_path).replace('.md', '')
            if not title or not fname:
                continue
            clean_title = _re.sub(r'[^a-z0-9 ]', '', title.lower())
            clean_fname = _re.sub(r'[^a-z0-9 ]', '', fname.lower())
            if clean_title[:25] != clean_fname[:25]:
                # Verify the title is safe for a filename
                safe_title = title.replace('/', '-').replace('\\', '-')
                safe_title = _re.sub(r'[*?"<>|]', '', safe_title).strip()
                if safe_title and safe_title != fname:
                    mismatches.append((fname, safe_title, rel_path))
        conn.close()

        if mismatches:
            pass  # Silent — auto-fix in progress
            fixed = 0
            failed = 0
            kb_bin = os.path.join(KB, 'bin', 'kb')
            for old_name, new_name, rel_path in mismatches:
                # Skip if target already exists
                target = os.path.join(KB, os.path.dirname(rel_path), new_name + '.md')
                if os.path.exists(target):
                    continue
                result = _sp.run(
                    [kb_bin, 'rename', old_name, '--to', new_name, '--yes'],
                    capture_output=True, text=True, cwd=KB, timeout=10,
                    env={**os.environ, 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:' + os.environ.get('PATH', '')}
                )
                if result.returncode == 0:
                    fixed += 1
                else:
                    failed += 1
            if fixed > 0:
                auto_fixed += fixed
            if failed > 0:
                print(f"  ⚠  {failed} mismatches could not be auto-fixed")
                issues += failed
        else:
            pass  # Silent when clean
    except Exception as e:
        print(f"  ⚠  Could not read search index: {e}")
        issues += 1

# ═══════════════════════════════════════════════════
total_checks += 1
pass  # 10. Frontmatter integrity

# Check for blank lines inside YAML frontmatter (breaks Obsidian/Dataview parsing)
fm_blank_lines = []
fm_fixed = 0
for f in wiki_files:
    try:
        with open(f, 'r', encoding='utf-8') as fh:
            content = fh.read()
        m = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not m:
            continue
        fm_text = m.group(1)
        if '\n\n' in fm_text:
            # Auto-fix: remove blank lines from frontmatter
            fixed_fm = '\n'.join(line for line in fm_text.split('\n') if line.strip() != '')
            new_content = content[:m.start(1)] + fixed_fm + content[m.end(1):]
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(new_content)
            fm_fixed += 1
            fm_blank_lines.append(os.path.basename(f))
    except (IOError, UnicodeDecodeError):
        pass

if fm_fixed > 0:
    print(f"  ✗ Blank lines in frontmatter: {fm_fixed} (auto-fixed)")
    for item in fm_blank_lines[:10]:
        print(f"    - {item}")
    if fm_fixed > 10:
        print(f"    ... and {fm_fixed - 10} more")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 11. Phantom empty files
# ─────────────────────────────────────────────────────
# Obsidian creates empty .md files when users click unresolved wikilinks.
# These phantom files shadow real wiki pages in search results and graph view.
total_checks += 1
phantom_files = []
for root_dir, dirs, fnames in os.walk(KB):
    # Skip known system directories
    skip_dirs = ['.git', '.obsidian', '.kb-trash', 'raw', 'docs', 'bin', 'server', 'shared', 'supabase', 'inbox', '.athena', '.claudian']
    dirs[:] = [d for d in dirs if d not in skip_dirs]
    for fname in fnames:
        if not fname.endswith('.md'):
            continue
        fpath = os.path.join(root_dir, fname)
        if os.path.getsize(fpath) == 0:
            rel = os.path.relpath(fpath, KB)
            # Don't flag expected empty files
            if rel in ('.gitkeep', '_TEMPLATE.md'):
                continue
            phantom_files.append(rel)

if phantom_files:
    issues += len(phantom_files)
    print(f"  ✗ Phantom empty files: {len(phantom_files)}")
    for pf in phantom_files[:20]:
        print(f"    - {pf}")
    # Auto-fix: remove phantom files
    removed = 0
    for pf in phantom_files:
        full_path = os.path.join(KB, pf)
        try:
            os.remove(full_path)
            removed += 1
            # Remove empty parent dirs
            parent = os.path.dirname(full_path)
            while parent != KB and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
                parent = os.path.dirname(parent)
        except OSError:
            pass
    if removed:
        print(f"  → Auto-fixed: removed {removed} phantom files")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 12. Search index title mismatch
# ─────────────────────────────────────────────────────
# Titles in the search index should match frontmatter titles.
# Stale titles cause broken wikilinks in search results.
total_checks += 1
try:
    import sqlite3
    db_path = os.path.join(KB, '.athena', 'search.db')
    if os.path.exists(db_path):
        sconn = sqlite3.connect(db_path)
        mismatches = []
        for fpath in wiki_files:
            fm, _ = extract_frontmatter(fpath)
            fm_title = fm.get('title', '')
            if not fm_title:
                continue
            rel = os.path.relpath(fpath, KB)
            row = sconn.execute('SELECT title FROM pages WHERE rel_path = ?', (rel,)).fetchone()
            if row and row[0] != fm_title:
                mismatches.append(f"{rel}: index='{row[0][:40]}' vs fm='{fm_title[:40]}'")
        sconn.close()
        if mismatches:
            issues += len(mismatches)
            print(f"  ✗ Title mismatches: {len(mismatches)}")
            for mm in mismatches[:10]:
                print(f"    - {mm}")
            print("  → Fix: run 'kb index' to rebuild")
        else:
            pass  # Silent when clean
    else:
        print("  ⚠ No search index found — skipping")
except Exception as e:
    print(f"  ⚠ Could not check index: {e}")

# ─────────────────────────────────────────────────────
pass  # 13. Duplicate H1 heading
# ─────────────────────────────────────────────────────
# If frontmatter has title: "X" and body starts with # X, Obsidian renders
# the title twice. The body H1 should be removed.
total_checks += 1
h1_dupes = []
for fpath in wiki_files:
    fm, content = extract_frontmatter(fpath)
    fm_title = fm.get('title', '')
    if not fm_title:
        continue
    # Check if body starts with # heading (after frontmatter)
    body_start = content.find('---', 3)
    if body_start == -1:
        continue
    body = content[body_start + 3:].strip()
    m = re.match(r'^#\s+(.+)', body)
    if m:
        h1_text = m.group(1).strip()
        # Check if H1 matches or is similar to frontmatter title
        if h1_text.lower() == fm_title.lower() or fm_title.lower().startswith(h1_text.lower()):
            rel = os.path.relpath(fpath, KB)
            h1_dupes.append(rel)

if h1_dupes:
    issues += len(h1_dupes)
    print(f"  ✗ Duplicate H1 headings: {len(h1_dupes)}")
    for d in h1_dupes[:15]:
        print(f"    - {d}")
    # Auto-fix: remove the body H1
    fixed = 0
    for d in h1_dupes:
        full_path = os.path.join(KB, d)
        try:
            with open(full_path, 'r', encoding='utf-8') as fh:
                content = fh.read()
            # Remove first H1 in body (after frontmatter)
            end_fm = content.find('---', 3) + 3
            body = content[end_fm:]
            body = re.sub(r'^\s*#\s+.+\n', '', body, count=1)
            with open(full_path, 'w', encoding='utf-8') as fh:
                fh.write(content[:end_fm] + body)
            fixed += 1
        except Exception:
            pass
    if fixed:
        print(f"  → Auto-fixed: removed {fixed} duplicate H1 headings")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 14. Missing Keywords section
# ─────────────────────────────────────────────────────
# Auto-fix: generate Keywords section from frontmatter tags
total_checks += 1
fixed_kw = []
for fpath in wiki_files:
    if not os.path.exists(fpath):
        continue  # file was moved/trashed by an earlier auto-fix in this run
    rel = os.path.relpath(fpath, KB)
    if not (rel.startswith('wiki/format/') or rel.startswith('wiki/insights/')):
        continue
    with open(fpath, 'r', encoding='utf-8') as fh:
        content = fh.read()
    # Extract tags from frontmatter
    fm_m = re.match(r'^---\r?\n(.*?)\r?\n---', content, re.DOTALL)
    tags = []
    if fm_m:
        tm = re.search(r'tags:\s*\[([^\]]*)\]', fm_m.group(1))
        if tm:
            tags = [t.strip().strip('"').strip("'") for t in tm.group(1).split(',') if t.strip()]
    if not tags:
        continue
    kw_section = "\n## Keywords\n" + " · ".join(f"[[{t}]]" for t in tags) + "\n"
    # Match ## Keywords only as a real section header (at line start, possibly
    # at top of file). A bare string search would match backtick-quoted mentions
    # of "## Keywords" inside body prose (e.g. code spans, table cells) and
    # then the split below would truncate the body at that point — confirmed
    # data-loss bug (2026-04-18 recovery).
    kw_header_re = re.compile(r'(?:\A|\n)##\s+Keywords\s*\n', re.IGNORECASE)
    kw_match = kw_header_re.search(content)
    if kw_match:
        # Check if existing Keywords section already has wikilinks — skip if so.
        kw_tail = content[kw_match.end():]
        kw_part = re.split(r'\n##\s+', kw_tail, maxsplit=1)[0]
        if '[[' in kw_part:
            continue  # Already has wikilinks — OK
        # Replace the existing Keywords section (preserve everything before it
        # and any sections after it).
        after_kw = re.split(r'\n##\s+', kw_tail, maxsplit=1)
        trailing = ('\n## ' + after_kw[1]) if len(after_kw) == 2 else ''
        content = content[:kw_match.start()].rstrip() + "\n" + kw_section + trailing
    else:
        # No Keywords section at all — add one.
        content = content.rstrip() + "\n" + kw_section
    with open(fpath, 'w', encoding='utf-8') as fh:
        fh.write(content)
    fixed_kw.append(os.path.basename(fpath))

if fixed_kw:
    auto_fixed += len(fixed_kw)
    print(f"  ✓ Missing Keywords section: {len(fixed_kw)} (auto-fixed)")
    for f in fixed_kw[:10]:
        print(f"    - {f}")
    if len(fixed_kw) > 10:
        print(f"    ... and {len(fixed_kw) - 10} more")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 15a. Missing Source link
# ─────────────────────────────────────────────────────
# Auto-fix: add [Source](url) to pages that have a URL but no source link
total_checks += 1
fixed_source = []
for fpath in wiki_files:
    rel = os.path.relpath(fpath, KB)
    if not rel.startswith('wiki/format/'):
        continue
    fm, body = extract_frontmatter(fpath)
    url = fm.get('url', '')
    if not url or '[Source](' in body:
        continue
    # Add source link after frontmatter
    with open(fpath, 'r', encoding='utf-8') as fh:
        content = fh.read()
    # Insert after closing ---
    fm_end = content.find('\n---', content.find('---') + 3)
    if fm_end > 0:
        insert_pos = fm_end + 4  # after \n---
        content = content[:insert_pos] + f'\n[Source]({url})\n' + content[insert_pos:]
        with open(fpath, 'w', encoding='utf-8') as fh:
            fh.write(content)
        fixed_source.append(os.path.basename(fpath))

check("Missing Source link (auto-fixed)", fixed_source, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 15b. Self-referencing stub pages
# ─────────────────────────────────────────────────────
# Auto-fix: detect pages whose body is just "Renamed — see [[Same Page Name]]" (circular link)
total_checks += 1
fixed_stubs = []
for fpath in wiki_files:
    if '/dashboards/' in fpath or '/topics/' in fpath:
        continue
    fm, body = extract_frontmatter(fpath)
    body_clean = body.strip()
    page_name = os.path.splitext(os.path.basename(fpath))[0]
    # Detect: body is just "Renamed — see [[Self]]" or "Stub — see [[Self]]"
    if ('Renamed' in body_clean or 'Stub' in body_clean) and f'[[{page_name}]]' in body_clean and len(body_clean) < 300:
        # This is a self-referencing stub — rebuild from raw content
        raw_path = fm.get('raw_path', '')
        url = fm.get('url', '')
        raw_file = os.path.join(KB, raw_path) if raw_path else None
        if raw_file and os.path.exists(raw_file):
            with open(raw_file, 'r', encoding='utf-8') as rfh:
                raw_content = rfh.read()
            # Extract body from raw
            parts = raw_content.split('## Content', 1)
            raw_body = parts[1].strip() if len(parts) > 1 else raw_content[200:3000]
            if len(raw_body) > 50:
                # Rebuild the page body
                with open(fpath, 'r', encoding='utf-8') as fh:
                    full = fh.read()
                fm_end = full.find('\n---', full.find('---') + 3)
                if fm_end > 0:
                    new_body = full[:fm_end + 4] + '\n'
                    if url: new_body += f'[Source]({url})\n\n'
                    new_body += raw_body[:5000] + '\n'
                    with open(fpath, 'w', encoding='utf-8') as fh:
                        fh.write(new_body)
                    fixed_stubs.append(os.path.basename(fpath))

check("Self-referencing stubs (auto-rebuilt)", fixed_stubs, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 15c. Wiki title normalization
# ─────────────────────────────────────────────────────
# Auto-fix: enforce naming convention — delegates to apply_naming_convention
# in wiki_page.py so the linter and the ingest path share one source of truth.
# Covers X, GitHub, LinkedIn, YouTube, arXiv, plus file-type prefixes (PDF,
# Word, Excel, PowerPoint, etc.) for direct file ingests.
total_checks += 1
title_normalized = []
for fpath in wiki_files:
    if '/dashboards/' in fpath or '/topics/' in fpath or '/entities/' in fpath:
        continue
    fm, _ = extract_frontmatter(fpath)
    title = fm.get('title', '')
    url = fm.get('url', '')
    source_type = fm.get('source_type', 'webpage')
    if not title or not url:
        continue

    # truncate=False so long-but-valid existing titles aren't silently shortened
    new_title = apply_naming_convention(title, url, source_type, truncate=False)

    if new_title != title:
        # Skip pages whose divergence is solely from filename-unsafe chars
        # (slash) being stripped — that's by design, not a defect. The TITLE
        # in frontmatter legitimately preserves `/` (e.g. `X: NicholasSpisak/
        # second-brain`) while the FILENAME sanitizes to `-`. Only flag when
        # the divergence is a real prefix/structure mismatch.
        title_alnum = re.sub(r'[^a-zA-Z0-9]', '', title)
        new_alnum = re.sub(r'[^a-zA-Z0-9]', '', new_title)
        if title_alnum == new_alnum:
            continue
        title_normalized.append(f"{os.path.basename(fpath)[:40]} → {new_title[:40]}")

# Title normalization is report-only — renaming 100+ files at once breaks wikilinks.
# Use `kb rename` for individual renames which updates all cross-references.
check("Wiki titles needing rename (use kb rename)", title_normalized)

# ─────────────────────────────────────────────────────
pass  # 15d. Empty insight pages (template only)
# ─────────────────────────────────────────────────────
# Flag insight pages that still have only the template — no evidence, no content
total_checks += 1
empty_insights = []
for fpath in wiki_files:
    if '/insights/' not in fpath:
        continue
    fm, body = extract_frontmatter(fpath)
    # Check if body is just template comments and empty sections
    body_no_comments = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL).strip()
    body_no_headers = re.sub(r'^##.*$', '', body_no_comments, flags=re.MULTILINE).strip()
    if len(body_no_headers) < 50:
        empty_insights.append(os.path.basename(fpath))

check("Empty insight pages (template only, needs content)", empty_insights)

# ─────────────────────────────────────────────────────
pass  # 15e. Missing raw files (local backup)
# ─────────────────────────────────────────────────────
# Auto-fix: create raw file from wiki page content for pages that have URL but no raw backup
total_checks += 1
missing_raw_created = []
for fpath in wiki_files:
    if '/topics/' in fpath or '/dashboards/' in fpath or '/entities/' in fpath or '/insights/' in fpath:
        continue
    fm, body = extract_frontmatter(fpath)
    url = fm.get('url', '')
    rp = fm.get('raw_path', '')
    if not url or len(body.strip()) < 100:
        continue
    # Check if raw file exists
    if rp and os.path.exists(os.path.join(KB, rp)):
        continue
    # Create raw file from wiki content
    slug = re.sub(r'https?://', '', url).replace('/', '-').replace('?', '-')
    slug = re.sub(r'[^a-z0-9-]', '-', slug.lower())[:60].rstrip('-')
    st = fm.get('source_type', 'webpage')
    from config import raw_dir_for_source_type
    raw_rel = raw_dir_for_source_type(st) or raw_dir_for_source_type('webpage')
    raw_dir = os.path.join(KB, raw_rel)
    os.makedirs(raw_dir, exist_ok=True)
    raw_path = os.path.join(raw_dir, slug + '.md')
    if os.path.exists(raw_path):
        continue
    title = fm.get('title', os.path.splitext(os.path.basename(fpath))[0])
    import time as _time_raw
    raw_content = f"# {title}\n\n- **URL:** {url}\n- **Captured:** {fm.get('date_added', _time_raw.strftime('%Y-%m-%d'))}\n\n## Content\n\n{body[:5000]}\n"
    with open(raw_path, 'w', encoding='utf-8') as fh:
        fh.write(raw_content)
    # Update wiki page raw_path
    rel_raw = os.path.relpath(raw_path, KB)
    with open(fpath, 'r', encoding='utf-8') as fh:
        text = fh.read()
    if 'raw_path:' in text:
        text = re.sub(r'raw_path:.*\n', f'raw_path: "{rel_raw}"\n', text, count=1)
    with open(fpath, 'w', encoding='utf-8') as fh:
        fh.write(text)
    missing_raw_created.append(os.path.basename(fpath)[:50])

check("Missing raw files (auto-created backup)", missing_raw_created, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 15f. Local Copy link (raw file accessible from wiki page)
# ─────────────────────────────────────────────────────
# Auto-fix: add "Local Copy" link to wiki pages so users can read the full raw content
# This is critical for ephemeral sources (Google AI Mode, paywalled articles, etc.)
total_checks += 1
local_copy_fixed = []
for fpath in wiki_files:
    if '/topics/' in fpath or '/dashboards/' in fpath or '/entities/' in fpath or '/insights/' in fpath:
        continue
    fm, body = extract_frontmatter(fpath)
    rp = fm.get('raw_path', '')
    if not rp or not os.path.exists(os.path.join(KB, rp)):
        continue
    # Check if Local Copy link already exists
    if 'Local Copy' in body or 'local-copy' in body or f'[[{rp}' in body:
        continue
    # Add Local Copy link after [Source](...) line
    with open(fpath, 'r', encoding='utf-8') as fh:
        text = fh.read()
    # Convert raw_path to Obsidian-compatible wikilink (strip .md extension for display)
    raw_name = os.path.splitext(os.path.basename(rp))[0]
    local_link = f" · [[{rp}|Local Copy]]"
    if '[Source](' in text:
        # Add after the Source link — find the closing ) and insert after it
        src_start = text.index('[Source](')
        # Find matching closing paren
        paren_depth = 0
        src_end = src_start
        for i in range(src_start, min(src_start + 5000, len(text))):
            if text[i] == '(': paren_depth += 1
            elif text[i] == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    src_end = i + 1
                    break
        text = text[:src_end] + local_link + text[src_end:]
    else:
        # Add after frontmatter
        fm_end = text.find('\n---', text.find('---') + 3)
        if fm_end > 0:
            text = text[:fm_end + 4] + f'\n[[{rp}|Local Copy]]\n' + text[fm_end + 4:]
    with open(fpath, 'w', encoding='utf-8') as fh:
        fh.write(text)
    local_copy_fixed.append(os.path.basename(fpath)[:50])

check("Local Copy links (auto-added)", local_copy_fixed, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 15. Corrupted filenames
# ─────────────────────────────────────────────────────
# Auto-fix: trash files with non-printable characters in filename
total_checks += 1
corrupted_trashed = []
for fpath in wiki_files:
    bn = os.path.basename(fpath)
    if any(ord(c) < 32 for c in bn):
        trash_dir = os.path.join(KB, '.kb-trash')
        os.makedirs(trash_dir, exist_ok=True)
        safe_name = re.sub(r'[^\x20-\x7E]', '_', bn)[:60] + '.md'
        dest = os.path.join(trash_dir, safe_name)
        import shutil
        shutil.move(fpath, dest)
        corrupted_trashed.append(f"{safe_name} (was corrupted filename)")
        auto_fixed += 1

if corrupted_trashed:
    print(f"  ✓ Corrupted filenames: {len(corrupted_trashed)} (auto-trashed)")
    for f in corrupted_trashed:
        print(f"    - {f}")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 16. Topic-type pages in wrong directory
# ─────────────────────────────────────────────────────
# Auto-fix: move source_type=topic pages from format/ dirs to wiki/topics/
total_checks += 1
moved_topics = []
for fpath in wiki_files:
    if '/topics/' in fpath or '/dashboards/' in fpath:
        continue
    fm, _ = extract_frontmatter(fpath)
    if fm.get('source_type') == 'topic' and '/format/' in fpath:
        bn = os.path.basename(fpath)
        dest = os.path.join(KB, 'wiki', 'topics', bn)
        if not os.path.exists(dest):
            import shutil
            shutil.move(fpath, dest)
            moved_topics.append(f"{bn} → wiki/topics/")
            auto_fixed += 1

if moved_topics:
    print(f"  ✓ Topic pages in wrong dir: {len(moved_topics)} (auto-moved)")
    for f in moved_topics:
        print(f"    - {f}")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 17. Dead/404 pages
# ─────────────────────────────────────────────────────
# Auto-fix: trash wiki pages whose body is just a 404 message
total_checks += 1
dead_trashed = []
dead_markers = ['page not found', 'uh oh, we can\'t seem to find', '404', 'this page isn\'t available']
for fpath in wiki_files:
    _, body = extract_frontmatter(fpath)
    body_lower = body.lower().strip()
    if len(body_lower) < 300 and any(marker in body_lower for marker in dead_markers):
        bn = os.path.basename(fpath)
        trash_dir = os.path.join(KB, '.kb-trash')
        os.makedirs(trash_dir, exist_ok=True)
        import shutil
        shutil.move(fpath, os.path.join(trash_dir, bn))
        dead_trashed.append(bn)
        auto_fixed += 1

if dead_trashed:
    print(f"  ✓ Dead/404 pages: {len(dead_trashed)} (auto-trashed)")
    for f in dead_trashed:
        print(f"    - {f}")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 18. Untitled Page placeholder in raw files
# ─────────────────────────────────────────────────────
# Auto-fix: raw H1 of "# Untitled Page" (and similar weak placeholders) is a
# capture-time fallback bug. Derive a real title from the owning wiki page,
# the content's own H1, or a humanized slug — in that priority order.
total_checks += 1
WEAK_TITLES = {'untitled page', 'untitled', 'page not found', 'error', '404', ''}

# Build raw-path → wiki-title map (inverse of the raw_path frontmatter link).
raw_to_wiki_title = {}
for wf in wiki_files:
    fm, _ = extract_frontmatter(wf)
    title = fm.get('title', '').strip().strip('"').strip("'")
    if not title:
        title = os.path.splitext(os.path.basename(wf))[0]
    raw_refs = []
    if 'raw_path' in fm and isinstance(fm['raw_path'], str):
        raw_refs.append(fm['raw_path'])
    if 'raw_paths' in fm and isinstance(fm['raw_paths'], list):
        raw_refs.extend(fm['raw_paths'])
    for rp in raw_refs:
        raw_to_wiki_title[rp.strip().strip('"').strip("'")] = title

def _humanize_slug(slug):
    parts = re.split(r'[-_.]+', slug)
    parts = [p for p in parts if p]
    return ' '.join(p[:1].upper() + p[1:] for p in parts)[:200]

def _derive_content_h1(body):
    # Walk body lines, skip the template preamble (Clipped/Content fetched/User
    # Suggestions/Description/## Content header), return first real H1.
    for line in body.splitlines():
        m = re.match(r'^#\s+(.+?)\s*$', line)
        if not m:
            continue
        t = m.group(1).strip()
        if t.lower() in WEAK_TITLES:
            continue
        if t.lower() in ('content', 'user suggestions'):
            continue
        return t
    return None

untitled_fixed = []
for rel in raw_files.keys():
    fpath = os.path.join(KB, rel)
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (IOError, UnicodeDecodeError):
        continue
    if not lines:
        continue
    m = re.match(r'^#\s+(.*?)\s*$', lines[0])
    if not m:
        continue
    current = m.group(1).strip().lower()
    if current not in WEAK_TITLES:
        continue
    # Derive a better title in priority order.
    new_title = raw_to_wiki_title.get(rel)
    if not new_title:
        # Skip template preamble: look past the "## Content" marker if present.
        body = ''.join(lines[1:])
        content_section = body.split('## Content', 1)
        tail = content_section[1] if len(content_section) == 2 else body
        new_title = _derive_content_h1(tail)
    if not new_title:
        slug = os.path.splitext(os.path.basename(rel))[0]
        new_title = _humanize_slug(slug)
    if not new_title or new_title.strip().lower() in WEAK_TITLES:
        continue
    lines[0] = f'# {new_title}\n'
    try:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        untitled_fixed.append(f"{rel} → \"{new_title}\"")
        auto_fixed += 1
    except IOError:
        pass

if untitled_fixed:
    print(f"  ✓ Untitled raw H1: {len(untitled_fixed)} (auto-fixed)")
    for f in untitled_fixed[:10]:
        print(f"    - {f}")
    if len(untitled_fixed) > 10:
        print(f"    ... and {len(untitled_fixed) - 10} more")
else:
    pass  # Silent when clean

# ─────────────────────────────────────────────────────
pass  # 19. Empty wiki pages (scaffold only, no content)
# ─────────────────────────────────────────────────────
# Flag wiki pages whose body is template scaffolding only — section headers,
# HTML comments, and the Keywords line, but no actual content. Report-only:
# the author must fill them (we don't fabricate content).
total_checks += 1
SCAFFOLD_SECTIONS = {
    'evidence', 'open questions', 'rules', 'suggested rules', 'connections',
    'keywords', 'user suggestions', 'content', 'finding', 'summary',
    'notes', 'details'
}

def _residual_content(text):
    """Strip frontmatter, HTML comments, headers, and Keywords lines;
    return what's left (the actual prose)."""
    t = re.sub(r'^---\s*\n.*?\n---\s*\n?', '', text, count=1, flags=re.DOTALL)
    t = re.sub(r'<!--.*?-->', '', t, flags=re.DOTALL)
    t = re.sub(r'^#+\s+.*$', '', t, flags=re.MULTILINE)
    # Keywords-style lines: only bracketed wikilinks separated by · or •
    t = re.sub(r'^\s*\[\[[^\]]+\]\](?:\s*[·•]\s*\[\[[^\]]+\]\])*\s*$',
               '', t, flags=re.MULTILINE)
    return t.strip()

empty_pages = []
for fpath in wiki_files:
    if '/dashboards/' in fpath:
        continue
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (IOError, UnicodeDecodeError):
        continue
    residual = _residual_content(content)
    if len(residual) < 40:
        rel = os.path.relpath(fpath, KB)
        empty_pages.append(rel)

check("Empty wiki pages (scaffold only — fill or trash)", empty_pages)

# ─────────────────────────────────────────────────────
pass  # 20. Stale URL/Captured/Clipped bullets in wiki bodies
# ─────────────────────────────────────────────────────
# Auto-fix: old wiki templates emitted "- **URL:** ..." / "- **Captured:** ..."
# as body bullets. The current wiki_page.py template does NOT emit these (URL
# is canonical in frontmatter, shown via the Source line). Any existing bullet
# is pre-migration residue that shadows the Source line.
total_checks += 1
stale_bullet_re = re.compile(
    r'^-\s+\*\*(?:URL|Captured|Clipped|Content fetched):\*\*[^\n]*\n',
    re.MULTILINE,
)
stale_bullets_fixed = []
for fpath in wiki_files:
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (IOError, UnicodeDecodeError):
        continue
    new = stale_bullet_re.sub('', content)
    new = re.sub(r'\n{3,}', '\n\n', new)
    if new != content:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(new)
        stale_bullets_fixed.append(os.path.relpath(fpath, KB))
        auto_fixed += 1

if stale_bullets_fixed:
    print(f"  ✓ Stale URL/Captured bullets in body: {len(stale_bullets_fixed)} (auto-fixed)")
    for f in stale_bullets_fixed[:10]:
        print(f"    - {f}")
    if len(stale_bullets_fixed) > 10:
        print(f"    ... and {len(stale_bullets_fixed) - 10} more")
else:
    pass

# ─────────────────────────────────────────────────────
pass  # 21. Image-type pages missing actual image binary
# ─────────────────────────────────────────────────────
# wiki_page.py sets source_type='image' whenever raw_path is under raw/images/.
# Historically some pages were created with only a .md description and no real
# binary — the "Karpathy Three-Layer Architecture Diagram" case. Flag these so
# they're either captured properly or converted to a topic page.
total_checks += 1
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.tiff')
image_type_no_binary = []
for fpath in wiki_files:
    fm, _ = extract_frontmatter(fpath)
    if fm.get('source_type') != 'image':
        continue
    raw_path = fm.get('raw_path', '').strip('"').strip("'")
    if not raw_path:
        continue
    # If raw_path points to a .md, look for a sibling binary with the same stem.
    raw_full = os.path.join(KB, raw_path)
    if raw_path.lower().endswith(IMAGE_EXTS):
        if not os.path.exists(raw_full):
            image_type_no_binary.append(os.path.relpath(fpath, KB))
    else:
        # Look for any sibling binary with matching stem.
        stem = os.path.splitext(raw_full)[0]
        has_binary = any(os.path.exists(stem + ext) for ext in IMAGE_EXTS)
        if not has_binary:
            image_type_no_binary.append(os.path.relpath(fpath, KB))

check("Image-type pages with no actual image (convert to topic, or capture the image)",
      image_type_no_binary)

# ─────────────────────────────────────────────────────
pass  # 22. Topic page freshness — auto-maintain member list
# ─────────────────────────────────────────────────────
# Auto-fix: every topic page gets an auto-maintained section listing all wiki
# pages whose frontmatter `related:` points at it. The section lives between
# HTML comment markers so manual curation above/below is preserved.
#
# Root cause: topic page bodies are hand-curated; new pages that declare
# `related: [[<Topic>]]` in their frontmatter don't auto-appear in the topic's
# visible body list. Readers saw stale topic pages (e.g. "AI Platform Memory
# Systems" didn't list X: Your harness, your memory despite its frontmatter
# linking there). CLAUDE.md Connections requirement is enforced for regular
# pages via check #6 (bidirectional cross-refs); topics need the reverse —
# aggregate all incoming references.
total_checks += 1
from config import marker as _marker
AUTO_START, AUTO_END = _marker('members')

def _strip_prefix(title):
    return re.sub(r'^(Web|Google|Drive|OneDrive|Dropbox|PDF|Word|Excel|PowerPoint'
                  r'|GitHub|LinkedIn|YouTube|arXiv|X|Insight):\s*', '', title)

# Build backlink map: topic-page-name → sorted list of referring pages
topic_refs = {}
topic_names = set()
for fpath in wiki_files:
    rel = os.path.relpath(fpath, KB)
    if rel.startswith('wiki/topics/'):
        topic_names.add(os.path.splitext(os.path.basename(fpath))[0])

for fpath in wiki_files:
    if '/topics/' in fpath or '/dashboards/' in fpath:
        continue
    fm, _ = extract_frontmatter(fpath)
    related = fm.get('related', [])
    if isinstance(related, str):
        related = [related]
    if not isinstance(related, list):
        continue
    page_name = os.path.splitext(os.path.basename(fpath))[0]
    page_title = fm.get('title', '').strip().strip('"')
    for ref in related:
        # Extract name from [[Name]] or [[Name|alias]]
        m = re.match(r'\s*\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]\s*', str(ref))
        target = (m.group(1) if m else str(ref)).strip().strip('"')
        if target in topic_names:
            topic_refs.setdefault(target, []).append((page_title or page_name, page_name))

topic_fixed = []
for topic in sorted(topic_names):
    topic_path = os.path.join(KB, 'wiki', 'topics', topic + '.md')
    if not os.path.exists(topic_path):
        continue
    members = topic_refs.get(topic, [])
    # Sort by prefix-stripped title so related pages cluster naturally
    members.sort(key=lambda t: _strip_prefix(t[0]).lower())
    # Build the auto section
    lines = [AUTO_START,
             f'### Related Pages ({len(members)} total, auto-maintained)',
             '']
    if not members:
        lines.append('_No pages currently reference this topic in frontmatter._')
    else:
        for title, name in members:
            lines.append(f'- [[{name}]]')
    lines.append('')
    lines.append(AUTO_END)
    auto_block = '\n'.join(lines)

    with open(topic_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace existing auto block, or append at end
    existing = re.search(
        re.escape(AUTO_START) + r'[\s\S]*?' + re.escape(AUTO_END),
        content,
    )
    if existing:
        new_content = content[:existing.start()] + auto_block + content[existing.end():]
    else:
        sep = '' if content.endswith('\n\n') else ('\n' if content.endswith('\n') else '\n\n')
        new_content = content.rstrip() + '\n\n' + auto_block + '\n'

    if new_content != content:
        with open(topic_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        topic_fixed.append((topic, len(members)))
        auto_fixed += 1

if topic_fixed:
    print(f"  ✓ Topic page member lists: {len(topic_fixed)} (auto-maintained)")
    for topic, n in topic_fixed[:10]:
        print(f"    - {topic} ({n} members)")
    if len(topic_fixed) > 10:
        print(f"    ... and {len(topic_fixed) - 10} more")
else:
    pass

# ─────────────────────────────────────────────────────
pass  # 23. _Contents.md auto-block maintenance
# ─────────────────────────────────────────────────────
# Every directory that contains user-facing pages gets a _Contents.md with
# an auto-maintained list of its members. Hand-curated preface text above
# the auto-block is preserved; only the content inside the markers changes.
total_checks += 1
from config import marker as _cmarker, raw_categories as _cats, raw_dir as _rd, wiki_format_dir as _wfd
_CSTART, _CEND = _cmarker('contents')

def _summary_of(fpath):
    """Pull the `summary:` field from a wiki page frontmatter; None if absent."""
    fm, _ = extract_frontmatter(fpath)
    s = fm.get('summary', '').strip().strip('"').strip("'")
    return s if s else None

def _contents_auto_block(dir_path, kind):
    """Build the auto-maintained member list for a directory.

    `kind` is 'raw-A' (raw: minimal one-liner per artifact) or 'wiki-B' (wiki:
    card-style with summary). For raw, we show: artifact filename + linked
    summary from wiki. For wiki, we show: [[Page]] + summary.
    """
    lines = [_CSTART]
    # Wiki member listing must be restricted to .md pages — sibling files like
    # .canvas, .png, .pdf are legitimate artifacts but Obsidian wikilinks
    # default to .md, so listing them produces phantom links that resolve to
    # nothing. For wiki dirs we hard-filter to .md; for raw dirs we keep the
    # broader artifact_exts logic (raw artifacts can be .pdf, .png, etc.).
    _excluded_names = ('.gitkeep', '_TEMPLATE.md', '_Contents.md', '_DO_NOT_WRITE_DIRECTLY.md')
    def _is_redirect_stub(fp):
        """Redirect stubs (`redirect: true` in frontmatter) are forwarders, not
        content pages — exclude them from auto-listing so they don't pollute
        Members or generate broken-looking wikilinks."""
        try:
            fm, _ = extract_frontmatter(str(fp))
            v = fm.get('redirect')
            return isinstance(v, str) and v.strip().strip('"').strip("'").lower() == 'true'
        except Exception:
            return False
    if kind == 'wiki-B':
        members = sorted([p for p in dir_path.iterdir()
                          if p.is_file() and p.suffix == '.md'
                          and p.name not in _excluded_names
                          and not _is_redirect_stub(p)])
    else:
        members = sorted([p for p in dir_path.iterdir()
                          if p.is_file() and p.name not in _excluded_names])
    # Include artifacts/ subdir contents for raw dirs
    artifacts = dir_path / 'artifacts'
    if artifacts.is_dir():
        members = sorted([p for p in artifacts.iterdir() if p.is_file() and p.name not in
                          ('.gitkeep', '_TEMPLATE.md')])
    lines.append(f'### Members ({len(members)} total, auto-maintained)')
    lines.append('')
    if not members:
        lines.append('_Empty._')
    elif kind == 'raw-A':
        # raw one-liner: [[artifact]] — summary from owning wiki (if any)
        # Build raw → wiki lookup
        raw_to_wiki_map = {}
        for wfp in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
            try:
                fmw, _ = extract_frontmatter(wfp)
            except Exception:
                continue
            for rp in ([fmw.get('raw_path')] if isinstance(fmw.get('raw_path'), str) else []) + \
                      (fmw.get('raw_paths', []) if isinstance(fmw.get('raw_paths'), list) else []):
                if rp:
                    raw_to_wiki_map[rp.strip().strip('"').strip("'")] = (fmw.get('title', '').strip().strip('"'), wfp)
        for m in members:
            rel = os.path.relpath(str(m), KB)
            wtitle_entry = raw_to_wiki_map.get(rel)
            if wtitle_entry:
                wtitle, wfp = wtitle_entry
                summary = _summary_of(wfp) or ''
                summary_short = (summary[:120] + '…') if len(summary) > 120 else summary
                lines.append(f'- [[{rel}|{m.name}]] — [[{wtitle}|wiki]]' + (f' · {summary_short}' if summary_short else ''))
            else:
                lines.append(f'- [[{rel}|{m.name}]] — (no wiki)')
    else:  # wiki-B card-style
        for m in members:
            name = os.path.splitext(m.name)[0]
            summary = _summary_of(str(m)) or ''
            summary_short = (summary[:200] + '…') if len(summary) > 200 else summary
            lines.append(f'- [[{name}]]')
            if summary_short:
                lines.append(f'  {summary_short}')
            lines.append('')
    # Trim trailing blank lines so we never produce \n{3,} (the stale-bullet
    # lint's blank-line collapser would otherwise rewrite the file each run).
    while lines and lines[-1] == '':
        lines.pop()
    lines.append(_CEND)
    return '\n'.join(lines)

contents_fixed = []
# Raw category directories (kind A)
for cat_name, cat_cfg in _cats().items():
    # Contents lives at raw/<cat>/_Contents.md; artifacts live under raw/<cat>/artifacts/
    top_dir = Path(os.path.join(KB, cat_cfg['dir']))
    if not top_dir.is_dir():
        continue
    cfile = top_dir / '_Contents.md'
    if not cfile.exists():
        continue
    block = _contents_auto_block(top_dir, 'raw-A')
    content = cfile.read_text(encoding='utf-8')
    existing = re.search(re.escape(_CSTART) + r'[\s\S]*?' + re.escape(_CEND), content)
    if existing:
        new_content = content[:existing.start()] + block + content[existing.end():]
    else:
        new_content = content.rstrip() + '\n\n' + block + '\n'
    if new_content != content:
        cfile.write_text(new_content, encoding='utf-8')
        contents_fixed.append(str(cfile.relative_to(KB)))
        auto_fixed += 1

# Wiki subdirectories (kind B). For each directory in the list, ensure
# a _Contents.md exists with the auto-block markers — auto-creating a
# minimal seed when missing (was: silently skipped when not present, but
# the user has to discover every ingest by searching otherwise; #140).
# Dashboards/feedback/comparisons are intentionally excluded — those
# folders contain navigation surfaces or are used as transient buckets.
_WIKI_TOC_DIRS = [
    'wiki/format/papers', 'wiki/format/repos', 'wiki/format/webpages',
    'wiki/format/videos', 'wiki/format/images', 'wiki/format/entities',
    'wiki/insights', 'wiki/topics', 'wiki/journal',
    'wiki/keywords', 'wiki/exports', 'wiki/profile', 'wiki/sessions',
]
def _seed_toc_template(dir_basename: str) -> str:
    pretty = dir_basename.replace('-', ' ').title()
    return (
        f'# Wiki {pretty} — Table of Contents\n\n'
        f'*Auto-maintained list of every page in this directory. Hand-curated notes\n'
        f'(chapters, highlights, ordering) can live ABOVE the auto-block below and\n'
        f'will not be overwritten by `kb lint`.*\n\n'
        f'{_CSTART}\n{_CEND}\n'
    )
for sd in _WIKI_TOC_DIRS:
    d = Path(os.path.join(KB, sd))
    if not d.is_dir():
        continue
    cfile = d / '_Contents.md'
    if not cfile.exists():
        cfile.write_text(_seed_toc_template(d.name), encoding='utf-8')
        contents_fixed.append(f"{cfile.relative_to(KB)}  (created)")
        auto_fixed += 1
    block = _contents_auto_block(d, 'wiki-B')
    content = cfile.read_text(encoding='utf-8')
    existing = re.search(re.escape(_CSTART) + r'[\s\S]*?' + re.escape(_CEND), content)
    if existing:
        new_content = content[:existing.start()] + block + content[existing.end():]
    else:
        new_content = content.rstrip() + '\n\n' + block + '\n'
    if new_content != content:
        cfile.write_text(new_content, encoding='utf-8')
        contents_fixed.append(str(cfile.relative_to(KB)))
        auto_fixed += 1

if contents_fixed:
    print(f"  ✓ _Contents.md auto-blocks: {len(contents_fixed)} (auto-maintained)")
    for f in contents_fixed[:10]:
        print(f"    - {f}")
    if len(contents_fixed) > 10:
        print(f"    ... and {len(contents_fixed) - 10} more")
else:
    pass

# ─────────────────────────────────────────────────────
pass  # 24. _Contents.md phantom wikilink detection
# ─────────────────────────────────────────────────────
# Defense-in-depth: scan every _Contents.md for wikilinks whose target page
# does not exist as an .md file in the same directory. Phantoms can creep in
# from (a) the auto-block including non-.md siblings (fixed at source in #23),
# (b) hand-curated preface text above the auto-block referencing renamed
# pages, or (c) future code paths that touch _Contents.md directly. Reports
# remaining phantoms; #23 already auto-fixes the auto-block portion.
#
# Whitespace normalization: Obsidian treats `[[foo]]` and `[[foo ]]` as the
# same page, so we strip both sides before comparing. Trailing-whitespace
# *filenames* are a separate problem flagged by check #25.
total_checks += 1
phantom_links = []
def _norm(s): return s.strip()
for sd in ['wiki/format/papers', 'wiki/format/repos', 'wiki/format/webpages',
           'wiki/format/videos', 'wiki/format/images', 'wiki/format/entities',
           'wiki/insights', 'wiki/topics', 'wiki/journal']:
    d = Path(os.path.join(KB, sd))
    cfile = d / '_Contents.md'
    if not cfile.exists():
        continue
    try:
        ctext = cfile.read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    present = {_norm(p.stem) for p in d.iterdir()
               if p.is_file() and p.suffix == '.md'
               and p.name not in ('_TEMPLATE.md', '_Contents.md', '_DO_NOT_WRITE_DIRECTLY.md')}
    for m in re.finditer(r'\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]', ctext):
        target = _norm(m.group(1))
        if '/' in target or target.startswith('http'):
            continue
        if target not in present:
            phantom_links.append(f"{cfile.relative_to(KB)} → [[{target}]] (no .md file)")
check("Phantom wikilinks in _Contents.md (target page missing — fix manually or rerun #23)",
      sorted(set(phantom_links)))

# ─────────────────────────────────────────────────────
pass  # 25. Wiki page filenames with quirky trailing characters
# ─────────────────────────────────────────────────────
# Filenames like `Foo .md` (trailing space) or `Foo..md` (trailing period
# before extension) silently break wikilink resolution, _Contents matching,
# and shell tooling. Trailing periods in particular cause non-deterministic
# extract_frontmatter failures inside the lint pipeline (the stem ends in
# `.` so some path-normalization layers strip it), which made
# _Contents.md regeneration flap between two states across consecutive
# lint runs. Detect both classes and rename to the cleaned form; if a
# sibling already exists with the cleaned name, report instead.
total_checks += 1
fn_renamed = []
fn_conflict = []
for wfp in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    p = Path(wfp)
    stem = p.stem
    cleaned = stem.strip().rstrip('.')
    if cleaned == stem or not cleaned:
        continue
    new_name = cleaned + p.suffix
    new_path = p.parent / new_name
    if new_path.exists():
        fn_conflict.append(f"{p.relative_to(KB)} (target {new_name} already exists)")
        continue
    try:
        p.rename(new_path)
        fn_renamed.append(f"{p.relative_to(KB)} → {new_name}")
    except OSError as e:
        fn_conflict.append(f"{p.relative_to(KB)} (rename failed: {e})")
check("Wiki filenames with trailing whitespace or period (auto-renamed)", fn_renamed, fixed=True)
check("Wiki filenames with quirky trailing chars — rename blocked (manual fix needed)", fn_conflict)

# Section 25b: broken-wikilink titles (#143). When the LLM-driven
# title extraction produced '[[]]' or similar wikilink fragments as
# the page title, the resulting filename is structurally broken (an
# empty wikilink target embedded in the filename). Auto-trash them
# rather than try to repair — they're always duplicates of a properly-
# titled sibling page (same URL, different title).
total_checks += 1
broken_title_trashed = []
_BROKEN_TITLE_RE = re.compile(r'\[\[\s*\]\]|\[\[\]\]')
import shutil as _shutil_bt, datetime as _dt_bt
_bt_trash = None
for wfp in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    name = os.path.basename(wfp)
    if name in ('_TEMPLATE.md', '_Contents.md'):
        continue
    if not _BROKEN_TITLE_RE.search(name):
        continue
    if _bt_trash is None:
        ts = _dt_bt.datetime.now().strftime('%Y%m%d_%H%M%S')
        _bt_trash = os.path.join(KB, '.kb-trash', f'{ts}_broken-wikilink-titles')
        os.makedirs(_bt_trash, exist_ok=True)
    rel = os.path.relpath(wfp, KB)
    dst_dir = os.path.join(_bt_trash, os.path.dirname(rel))
    os.makedirs(dst_dir, exist_ok=True)
    try:
        _shutil_bt.move(wfp, os.path.join(dst_dir, name))
        broken_title_trashed.append(rel)
    except OSError:
        pass
check("Broken-wikilink title pages trashed (#143 — '[[]]' in filename)", broken_title_trashed, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 26. Asset directory health (raw/assets/<slug>/)
# ─────────────────────────────────────────────────────
# Each asset dir corresponds to a captured raw page. Detect:
#  (a) orphan asset dirs whose owning raw page no longer exists
#  (b) raw pages whose markdown still hot-links http(s) images that
#      could have been downloaded — surface as "missed locals".
# Auto-fix is intentionally NOT applied; downloads can be slow/network-
# dependent and may surprise the user mid-lint. Use `kb backfill-assets`
# to fix, `kb retry-assets` to retry transient failures.
total_checks += 1
assets_root = Path(os.path.join(KB, 'raw', 'assets'))
orphan_asset_dirs = []
hotlinked_pages = []
if assets_root.is_dir():
    raw_pages = {Path(p).stem for p in glob.glob(os.path.join(KB, 'raw/webpages/artifacts/*.md'))}
    for d in assets_root.iterdir():
        if not d.is_dir() or d.name.startswith('.'):
            continue
        if d.name not in raw_pages:
            orphan_asset_dirs.append(f"raw/assets/{d.name}/  (no matching raw page)")
# Detect remaining hot-linked images in raw webpages (excluding favicons + assets)
_img_pat = re.compile(r'!\[[^\]]*\]\((https?://[^)\s]+)')
for raw_path in glob.glob(os.path.join(KB, 'raw/webpages/artifacts/*.md')):
    try:
        txt = Path(raw_path).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    hits = _img_pat.findall(txt)
    if hits:
        hotlinked_pages.append(f"{Path(raw_path).relative_to(KB)}  ({len(hits)} remote image(s))")
check("Orphan asset directories (raw page deleted, assets remain — kb purge or kb undo to recover)", orphan_asset_dirs)
check("Raw pages with hot-linked images (run `kb backfill-assets` to localize)", hotlinked_pages, show_limit=5)

# ─────────────────────────────────────────────────────
pass  # 27. Raw clippings missing H1 heading (auto-fix from frontmatter title)
# ─────────────────────────────────────────────────────
# Web Clipper output has YAML frontmatter with `title:` but no `# Heading`
# in the body. Obsidian's tab title and page title fall back to the
# slug-style filename when no H1 is present, so users see e.g.
# "the-vercel-breach-oauth-supply-chain..." instead of "The Vercel Breach:
# OAuth Supply Chain Attack...". Auto-fix: insert `# <title>` immediately
# after the closing `---` of the frontmatter block.
total_checks += 1
h1_added = []
h1_skipped = []
for raw_path in glob.glob(os.path.join(KB, 'raw/webpages/artifacts/*.md')):
    try:
        text = Path(raw_path).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---\n') and not text.startswith('---\r\n'):
        continue
    end_match = re.search(r'\n---[\s]*\n', text[3:])
    if not end_match:
        continue
    fm_end = end_match.end() + 3
    fm_block = text[3:end_match.start() + 3]
    body = text[fm_end:]
    # Already has H1 anywhere in the first 5 lines of body?
    body_head = body.lstrip('\n').split('\n', 5)[:5]
    if any(line.startswith('# ') for line in body_head):
        continue
    # Extract title from frontmatter
    title_match = re.search(r'^title\s*:\s*["\']?(.+?)["\']?\s*$', fm_block, re.MULTILINE)
    if not title_match:
        h1_skipped.append(f"{Path(raw_path).relative_to(KB)} (no title in frontmatter)")
        continue
    # Unescape YAML \" so the H1 reads `Trivy Compromised by "TeamPCP"`
    # rather than `Trivy Compromised by \"TeamPCP\"`.
    sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
    from wiki_schema import unescape_yaml_string  # type: ignore
    title = unescape_yaml_string(title_match.group(1).strip())
    new_text = text[:fm_end] + f"\n# {title}\n" + body
    Path(raw_path).write_text(new_text, encoding='utf-8')
    h1_added.append(str(Path(raw_path).relative_to(KB)))
check("Raw clippings missing H1 heading (auto-fixed from frontmatter title)", h1_added, fixed=True)
check("Raw clippings missing H1 — no title in frontmatter (manual fix needed)", h1_skipped)

# ─────────────────────────────────────────────────────
pass  # 28. Redirect stub health (target page must exist)
# ─────────────────────────────────────────────────────
# Stubs are written by `kb rename` so out-of-vault references to the old
# title don't trigger Obsidian's silent phantom-creation. If a stub's
# target was later renamed again or deleted, the stub becomes its own kind
# of broken pointer — detect and report.
total_checks += 1
broken_stubs = []
all_wiki_stems = set()
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    name = os.path.basename(f)
    if name in ('_TEMPLATE.md', '_Contents.md'):
        continue
    all_wiki_stems.add(os.path.splitext(name)[0])
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    fm, _ = extract_frontmatter(f)
    redir = fm.get('redirect')
    if not (isinstance(redir, str) and redir.strip().strip('"').strip("'").lower() == 'true'):
        continue
    target = fm.get('redirect_to', '').strip().strip('"').strip("'")
    if not target:
        broken_stubs.append(f"{os.path.relpath(f, KB)} (no redirect_to)")
    elif target not in all_wiki_stems:
        broken_stubs.append(f"{os.path.relpath(f, KB)} → [[{target}]] (target missing)")
check("Redirect stubs pointing to missing pages (manual fix: kb remove or update redirect_to)", broken_stubs)

# ─────────────────────────────────────────────────────
pass  # 29. Title quality (issue #125: conference IDs, acronyms, truncation)
# ─────────────────────────────────────────────────────
# Four heuristics catching the symptoms of the URL→title pipeline failures
# fixed in #125. Detected on every kb lint; heuristic 4 (acronym lost) is
# auto-fixed in-place. Heuristics 1-3 are reported with suggested kb rename
# commands — auto-renaming would update wikilinks across the vault, which
# is heavyweight enough to leave to the user via explicit kb rename.
#
# Skips redirect stubs (frontmatter redirect: true) since their bad titles
# are by-design; the user has already renamed to the correct page.
total_checks += 1
acronym_fixed = []
mid_word_trunc = []
conference_unexpanded = []
generic_prefix = []
try:
    sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
    from config import naming as _lint_naming  # type: ignore
    _ln = _lint_naming() or {}
except Exception:  # noqa: BLE001
    _ln = {}
_lint_acronyms = _ln.get('acronyms') or []
_lint_acronym_lc_to_canonical = {a.lower(): a for a in _lint_acronyms}
_lint_conf_patterns = _ln.get('conference_url_patterns') or []
# Common-word prefixes used to flag mid-word truncation: word's whole form
# is a strict prefix of a longer common word that's plausibly the original.
# Conservative — only triggers on titles whose length sits at a known
# truncation boundary (65, 75, 100), reducing false positives on natural
# titles that happen to end in a short word.
_KNOWN_TRUNC_BOUNDARIES = {65, 75, 100}
_PREFIX_HINTS = {
    'director': 'directory', 'machin': 'machine', 'comput': 'computing',
    'algori': 'algorithm', 'attac': 'attack', 'analys': 'analysis',
    'researc': 'research', 'devel': 'development', 'engineer': 'engineering',
    'imple': 'implementation', 'archi': 'architecture',
}

def _restore_title_acronyms(text):
    """In-place acronym restoration for an existing title string."""
    out_tokens = []
    for tok in text.split(' '):
        m = re.match(r'^(\W*)(\w+)(\W*)$', tok, re.UNICODE)
        if not m:
            out_tokens.append(tok)
            continue
        lead, word, trail = m.groups()
        canonical = _lint_acronym_lc_to_canonical.get(word.lower())
        # Only rewrite if the existing token differs from the canonical form
        # AND wasn't already all-uppercase (preserving things like "TLS").
        if canonical and word != canonical and word != word.upper():
            out_tokens.append(f'{lead}{canonical}{trail}')
        else:
            out_tokens.append(tok)
    return ' '.join(out_tokens)

for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    name = os.path.basename(f)
    if name in ('_TEMPLATE.md', '_Contents.md'):
        continue
    fm, body = extract_frontmatter(f)
    if not fm:
        continue
    redir = fm.get('redirect')
    if isinstance(redir, str) and redir.strip().strip('"').strip("'").lower() == 'true':
        continue
    title_raw = fm.get('title', '')
    if isinstance(title_raw, list):
        title = (title_raw[0] if title_raw else '').strip().strip('"').strip("'")
    else:
        title = title_raw.strip().strip('"').strip("'")
    if not title:
        continue
    url_raw = fm.get('url') or fm.get('source') or ''
    if isinstance(url_raw, list):
        url = (url_raw[0] if url_raw else '').strip().strip('"').strip("'")
    else:
        url = url_raw.strip().strip('"').strip("'")
    rel = os.path.relpath(f, KB)

    # Heuristic 4: acronym capitalization lost (auto-fix in-place).
    new_title = _restore_title_acronyms(title)
    if new_title != title:
        # Update YAML title and the H1 in body. Both must stay in sync —
        # the wiki filename stays as-is (renaming for casing-only change is
        # noise; Obsidian wikilinks are case-insensitive on macOS).
        try:
            full = Path(f).read_text(encoding='utf-8')
            # Replace title in YAML (first occurrence after `---`).
            full2 = re.sub(
                r'^(title\s*:\s*["\']?)' + re.escape(title) + r'(["\']?\s*)$',
                lambda m: m.group(1) + new_title + m.group(2),
                full, count=1, flags=re.MULTILINE,
            )
            # Replace H1 in body.
            full2 = re.sub(
                r'^(#\s+)' + re.escape(title) + r'\s*$',
                r'\g<1>' + new_title.replace('\\', r'\\'),
                full2, count=1, flags=re.MULTILINE,
            )
            if full2 != full:
                Path(f).write_text(full2, encoding='utf-8')
                acronym_fixed.append(f"{rel}: \"{title}\" → \"{new_title}\"")
        except (IOError, OSError):
            pass
        # Use the new title for subsequent heuristics.
        title = new_title

    # Heuristic 1: mid-word truncation at known cap.
    if len(title) in _KNOWN_TRUNC_BOUNDARIES:
        last_word = title.rsplit(' ', 1)[-1].lower()
        if last_word in _PREFIX_HINTS:
            mid_word_trunc.append(
                f"{rel}: title len {len(title)}, ends in \"{last_word}\" "
                f"(likely truncated from \"{_PREFIX_HINTS[last_word]}\")"
            )

    # Heuristic 3: conference identifier not expanded. We only flag when
    # the title has NO recognizable conference attribution at all — if the
    # user manually crafted "BlackHat 2017: ..." (no USA/Asia distinction),
    # we accept that as already-attributed and don't push them toward the
    # exact canonical "BlackHat USA 2017: ..." form.
    if url:
        for entry in _lint_conf_patterns:
            host_pat = entry.get('host_pattern')
            path_pat = entry.get('path_pattern')
            if not (host_pat and path_pat):
                continue
            if not re.search(host_pat, url, re.IGNORECASE):
                continue
            if not re.search(path_pat, url, re.IGNORECASE):
                continue
            template = entry.get('prefix_template') or ''
            # Conference shortname is the first word of the template
            # (e.g. 'BlackHat' from 'BlackHat USA 20$1').
            shortname = template.split(' ', 1)[0].strip()
            if not shortname:
                break
            # Already attributed if title starts with the shortname.
            if re.match(rf'^{re.escape(shortname)}\b', title, re.IGNORECASE):
                break
            conference_unexpanded.append(
                f"{rel}: URL matches {shortname} pattern, title has no "
                f"conference attribution (suggested: kb rename \"{os.path.splitext(name)[0]}\" "
                f"--to \"<rerun apply_naming_convention>\")"
            )
            break

    # Heuristic 2: generic prefix without colon attribution. Two subcases:
    #
    # 2a — colon-strip drift (#131, auto-fix): frontmatter title has the
    #      canonical colon form (`Web: Foo`) but filename has no colon
    #      (`Web Foo.md`). The wiki_schema sanitizer was stripping `:`;
    #      this lint catches captures created before that fix landed.
    #      Auto-rename to add the colon (filename + wikilinks across the
    #      vault).
    #
    # 2b — true legacy without-colon (report-only): both frontmatter
    #      title AND filename lack the colon. Either pre-#125 capture or
    #      a manually-crafted title that genuinely doesn't follow the
    #      convention. Reported for user-judgment kb rename.
    m_generic_title = re.match(r'^(PDF|Web|Drive|OneDrive|Dropbox)\s+[A-Z]', title)
    has_colon_in_title = re.match(r'^[A-Za-z][A-Za-z\s]*:\s', title)
    stem = os.path.splitext(name)[0]
    m_generic_stem = re.match(r'^(PDF|Web|Drive|OneDrive|Dropbox)\s+[A-Z]', stem)
    if has_colon_in_title and m_generic_stem:
        # 2a — colon was stripped at filename-sanitize time. Auto-rename
        # the file AND update wikilinks (using the same regex shape kb
        # rename uses for body-rewrite).
        new_stem = stem.replace(m_generic_stem.group(1) + ' ',
                                m_generic_stem.group(1) + ': ', 1)
        new_path = os.path.join(os.path.dirname(f), new_stem + '.md')
        if not os.path.exists(new_path):
            try:
                os.rename(f, new_path)
                # Update wikilinks across the vault (frontmatter `related:`
                # entries and body wikilinks). Pattern is `[[<old_stem>]]`
                # or `[[<old_stem>|alias]]` — restore as `[[<new_stem>]]`.
                refs_updated = 0
                for wfp in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
                    try:
                        wcontent = Path(wfp).read_text(encoding='utf-8')
                    except (IOError, UnicodeDecodeError):
                        continue
                    new_w = wcontent.replace(f'[[{stem}]]', f'[[{new_stem}]]')
                    new_w = new_w.replace(f'[[{stem}|', f'[[{new_stem}|')
                    if new_w != wcontent:
                        Path(wfp).write_text(new_w, encoding='utf-8')
                        refs_updated += 1
                acronym_fixed.append(  # reuse same auto-fix bucket for reporting
                    f"colon-strip: {rel} → {new_stem}.md "
                    f"(updated {refs_updated} wikilink ref(s))"
                )
            except OSError:
                generic_prefix.append(
                    f"{rel}: colon-strip detected but rename failed "
                    f"(target may exist or fs error)"
                )
        else:
            generic_prefix.append(
                f"{rel}: colon-strip detected but {new_stem}.md already "
                f"exists — manual merge needed"
            )
    elif m_generic_title and not has_colon_in_title:
        # 2b — both title and filename lack colon, report-only.
        generic_prefix.append(
            f"{rel}: title starts with \"{m_generic_title.group(1)} \" "
            f"(missing colon — pre-#125 capture; rerun ingest or kb rename)"
        )

check("Title acronyms restored + colon-strip renames (auto-fixed)", acronym_fixed, fixed=True)
check("Title length at known truncation cap with mid-word ending (re-capture or kb rename)", mid_word_trunc)
check("Conference URL detected but title not using expanded prefix (kb rename)", conference_unexpanded)
check("Title has generic format prefix without colon — manual fix (kb rename)", generic_prefix)

# ─────────────────────────────────────────────────────
pass  # 30. HTML entities in filenames or frontmatter titles (issue #124)
# ─────────────────────────────────────────────────────
# Filenames or YAML title fields containing literal HTML entities like
# '&amp;', '&#8211;', '&quot;' — the source's <title> element wasn't
# HTML-decoded before write. Source fix is in wiki_schema.write_*_page;
# this lint cleans up legacy data and catches any future regression.
#
# Auto-fix:
#   - Frontmatter `title:` and `# H1` lines: html.unescape in place.
#   - Filename: rename to the html-unescaped form. If the target filename
#     is filesystem-unsafe after unescape (e.g. would contain `/`), skip
#     the rename and report — sanitization of the new form is the user's
#     decision via kb rename.
import html as _html
total_checks += 1
_HTML_ENTITY_RE = re.compile(r'&(?:[a-z]+|#\d+);', re.IGNORECASE)
title_unescaped = []
filename_unescaped = []
filename_skipped = []
_html_lint_paths = (
    glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True) +
    glob.glob(os.path.join(KB, 'raw/**/*.md'), recursive=True)
)
for f in _html_lint_paths:
    name = os.path.basename(f)
    if name in ('_TEMPLATE.md', '_Contents.md'):
        continue
    rel = os.path.relpath(f, KB)
    # Step 1: unescape entities in frontmatter title and body H1 if present.
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    new_text = text
    # Frontmatter title:
    fm_title_m = re.search(r'^(title\s*:\s*["\']?)(.+?)(["\']?\s*)$', text, re.MULTILINE)
    if fm_title_m and _HTML_ENTITY_RE.search(fm_title_m.group(2)):
        decoded = _html.unescape(fm_title_m.group(2))
        new_text = new_text.replace(fm_title_m.group(0),
                                     fm_title_m.group(1) + decoded + fm_title_m.group(3), 1)
    # Body H1 (first one):
    h1_m = re.search(r'^#\s+(.+?)\s*$', text, re.MULTILINE)
    if h1_m and _HTML_ENTITY_RE.search(h1_m.group(1)):
        decoded_h1 = _html.unescape(h1_m.group(1))
        new_text = re.sub(r'^(#\s+)' + re.escape(h1_m.group(1)) + r'\s*$',
                          r'\1' + decoded_h1.replace('\\', r'\\'),
                          new_text, count=1, flags=re.MULTILINE)
    if new_text != text:
        Path(f).write_text(new_text, encoding='utf-8')
        title_unescaped.append(rel)

    # Step 2: filename itself contains entities — rename.
    stem = os.path.splitext(name)[0]
    if not _HTML_ENTITY_RE.search(stem):
        continue
    new_stem = _html.unescape(stem)
    # Sanity-check the new stem: if it now contains filesystem-unsafe chars,
    # don't auto-rename — that's a deeper sanitization decision.
    if any(ch in new_stem for ch in ('/', '\\', '\x00')):
        filename_skipped.append(f"{rel} → \"{new_stem}\" (unsafe chars after decode; manual fix)")
        continue
    new_path = os.path.join(os.path.dirname(f), new_stem + os.path.splitext(name)[1])
    if os.path.exists(new_path):
        filename_skipped.append(f"{rel} → {new_stem}{os.path.splitext(name)[1]} (target exists; manual merge)")
        continue
    try:
        os.rename(f, new_path)
        filename_unescaped.append(f"{rel} → {os.path.basename(new_path)}")
    except OSError as e:
        filename_skipped.append(f"{rel} (rename failed: {e})")

check("Frontmatter/H1 HTML entities decoded (auto-fixed: &amp; → &)", title_unescaped, fixed=True)
check("Filenames with HTML entities renamed (auto-fixed)", filename_unescaped, fixed=True)
check("Filenames with HTML entities — rename blocked (manual fix needed)", filename_skipped)

# Section 30c: wiki page names containing Obsidian-forbidden chars (#^[]).
# Obsidian parses `#`/`^` inside a wikilink as heading/block anchors and
# treats `[`/`]` as link delimiters (the first `]` closes `[[…]]` early), so
# any page whose name carries one of these is silently UNCLICKABLE — the
# graph view and every [[wikilink]] to it resolve to the wrong (or empty)
# target. apply_naming_convention now strips these at write time
# (wiki_page.py), so fresh ingests are clean; this catches pages written
# before that fix. Detection-only on purpose: the right replacement is a
# judgment call (e.g. "[un]prompted" → "unprompted" loses brand styling;
# an image-URL-garbage title needs a real headline, not just stripping),
# so surface for `kb rename` rather than auto-mangling. Witnessed
# 2026-05-28: a LinkedIn hashtag-row title produced an unclickable page.
total_checks += 1
_OBSIDIAN_FORBIDDEN_RE = re.compile(r'[#^\[\]]')
forbidden_name_pages = []
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    name = os.path.basename(f)
    if name in ('_TEMPLATE.md', '_Contents.md'):
        continue
    stem = os.path.splitext(name)[0]
    if _OBSIDIAN_FORBIDDEN_RE.search(stem):
        bad = ''.join(sorted({c for c in stem if c in '#^[]'}))
        suggested = _OBSIDIAN_FORBIDDEN_RE.sub('', stem)
        suggested = re.sub(r'\s{2,}', ' ', suggested).strip()
        forbidden_name_pages.append(
            f"{os.path.relpath(f, KB)} (has '{bad}') → kb rename --to \"{suggested}\"")
check("Wiki page names with Obsidian-forbidden chars #^[] — unclickable; "
      "rename suggested (manual: kb rename)", forbidden_name_pages)

# Section 30d: two-or-more wiki pages pointing at the same raw_path.
# Slug-collision bug class. Detection-only: the right resolution is
# judgment-driven (re-add the URL of the older page so the new slug logic
# regenerates a unique filename, or rename one page out of the way).
# Detection logic lives in bin/lib/raw_path_collisions.py so the regression
# test can exercise the same code path without spawning kb. Witnessed
# 2026-05-31 (YouTube watch URLs all collapsed to youtube-com-watch.md).
total_checks += 1
from raw_path_collisions import find_raw_path_collisions
raw_path_collisions = []
for rp, wikis in sorted(find_raw_path_collisions(KB).items()):
    raw_path_collisions.append(
        f"{rp} ← " + ", ".join(wikis) +
        " (re-add the URL of the older page to regenerate with a unique slug)")
check("Wiki pages sharing one raw_path — the older page silently points at "
      "the newer page's content (slug-collision class)", raw_path_collisions)

# Section 30b: auto-trash files whose names contain U+FFFD replacement
# chars or other binary-bytes-as-text contamination (#138). These were
# created when a binary URL (image, PDF) got routed through the webpage
# capture path before #138's Content-Type guard landed. Filename has
# bytes that decoded as the Unicode replacement char — file is garbage.
total_checks += 1
binary_garbled = []
import shutil as _shutil_garbled, datetime as _dt_garbled
def _is_binary_garbled(name):
    if name in ('_TEMPLATE.md', '_Contents.md'):
        return False
    if '�' in name:
        return True
    return any(ord(c) < 32 for c in name)
_garbled_trash = None
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True) + \
         glob.glob(os.path.join(KB, 'raw/**/*.md'), recursive=True):
    if not _is_binary_garbled(os.path.basename(f)):
        continue
    if _garbled_trash is None:
        ts = _dt_garbled.datetime.now().strftime('%Y%m%d_%H%M%S')
        _garbled_trash = os.path.join(KB, '.kb-trash', f'{ts}_binary-garbled-lint')
        os.makedirs(_garbled_trash, exist_ok=True)
    rel = os.path.relpath(f, KB)
    dst_dir = os.path.join(_garbled_trash, os.path.dirname(rel))
    os.makedirs(dst_dir, exist_ok=True)
    try:
        _shutil_garbled.move(f, os.path.join(dst_dir, os.path.basename(f)))
        binary_garbled.append(rel)
    except OSError:
        pass
check("Files with binary-byte names trashed (#138 — pre-Content-Type-guard residue)", binary_garbled, fixed=True)

# Section 30c (1.0.0): auto-trash raw files with NO YAML frontmatter.
# A raw is supposed to start with `---\n...\n---\n` carrying at least a
# `source:` or `url:` field. Files missing it slipped in via legacy
# capture paths or hand-edits gone wrong (concrete instance: a Cisco
# GitHub README clipped under a `linkedin-com-posts-ugcpost-*` slug —
# wrong content in wrong-categorized file). Because there's no URL we
# can recover from, the file is moved to .kb-trash/<ts>_no-frontmatter-lint/
# and removed from raw_files so subsequent lint sections don't fail on
# the missing file. The user can `kb undo` to restore for forensic review
# or recapture from the original URL if they remember it.
total_checks += 1
no_frontmatter = []
import shutil as _shutil_nofm, datetime as _dt_nofm
_nofm_trash = None
for raw_rel in list(raw_files.keys()):  # snapshot — we mutate raw_files
    raw_abs = os.path.join(KB, raw_rel)
    if not os.path.exists(raw_abs):
        continue
    if os.path.basename(raw_abs) in ('_TEMPLATE.md', '.gitkeep'):
        continue
    try:
        with open(raw_abs, 'r', encoding='utf-8', errors='replace') as fh:
            head = fh.read(4)
    except (OSError, IOError):
        continue
    if head.startswith('---'):
        continue  # has frontmatter, ok
    if _nofm_trash is None:
        ts = _dt_nofm.datetime.now().strftime('%Y%m%d_%H%M%S')
        _nofm_trash = os.path.join(KB, '.kb-trash', f'{ts}_no-frontmatter-lint')
        os.makedirs(_nofm_trash, exist_ok=True)
    dst_dir = os.path.join(_nofm_trash, os.path.dirname(raw_rel))
    os.makedirs(dst_dir, exist_ok=True)
    try:
        _shutil_nofm.move(raw_abs, os.path.join(dst_dir, os.path.basename(raw_abs)))
        no_frontmatter.append(raw_rel)
        del raw_files[raw_rel]
    except OSError:
        pass
check("Raw files with no frontmatter trashed (content-in-wrong-file class)", no_frontmatter, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 31. Raw-frontmatter readers using direct fm.get('url') (issue #115)
# ─────────────────────────────────────────────────────
# Detects raw-frontmatter URL readers that use `fm.get('url')` directly
# instead of `get_raw_source_url(fm)`. The risk: canonical raws emit
# `source:` not `url:`; a reader that only checks `url:` silently misses
# every canonical raw and produces false orphans / failed dedup.
#
# Heuristic: flag a `fm.get('url')` line only when fm was extracted from
# a clearly-raw path within ±5 lines. The strict variant reduces false
# positives (wiki readers near raw context, e.g. wiki page frontmatter
# that has `raw_path:` field). Add `# kb-lint:ok-raw-url` to silence.
total_checks += 1
risky_url_access = []
# Scan the legacy bin/kb oracle plus EVERY live bin/lib module. Issue #115
# originally scoped this to just bin/kb + kb_commands.py, so a new raw-frontmatter
# reader added to any other module (search.py, contradictions.py, a fresh
# _*_body.py, etc.) could silently reintroduce the source:/url: divergence
# uncaught. The two-gate heuristic below (_DIRECT_FM_URL_RE AND a raw-context
# extract_frontmatter within ±5 lines) keeps wiki-frontmatter readers — which
# legitimately use fm.get('url') — from false-positiving, so widening the file
# set is safe; verified to add zero flags on the current tree.
_LINT_FILES = [os.path.join(KB, 'bin', 'kb')] + sorted(
    glob.glob(os.path.join(KB, 'bin', 'lib', '*.py'))
)
# Strict raw-context: extract_frontmatter() called with a variable whose
# name signals a raw file (raw_, rf_, rfile, etc.) — wiki readers use
# names like fpath/wiki_path/f, which do not match.
_RAW_FM_EXTRACT_RE = re.compile(
    r'extract_frontmatter\s*\(\s*(?:raw_\w+|rf_\w*|rfile|orphan_\w+|abs_raw)\s*[,)]'
)
_DIRECT_FM_URL_RE = re.compile(r"\bfm\.get\(\s*['\"]url['\"]")
# Skip the lint section itself — its regex strings would match _DIRECT_FM_URL_RE.
_LINT_SECTION_MARKERS = ('# 31. Raw-frontmatter readers', '# SUMMARY')
for src_path in _LINT_FILES:
    if not os.path.isfile(src_path):
        continue
    try:
        src_lines = Path(src_path).read_text(encoding='utf-8').splitlines()
    except (IOError, UnicodeDecodeError):
        continue
    # Find lint-section line range to exclude self-references.
    skip_from = skip_to = None
    for i, line in enumerate(src_lines):
        if _LINT_SECTION_MARKERS[0] in line:
            skip_from = i
        elif skip_from is not None and _LINT_SECTION_MARKERS[1] in line:
            skip_to = i
            break
    for i, line in enumerate(src_lines):
        if skip_from is not None and skip_to is not None and skip_from <= i <= skip_to:
            continue
        if not _DIRECT_FM_URL_RE.search(line):
            continue
        if 'kb-lint:ok-raw-url' in line:
            continue
        window_start = max(0, i - 5)
        window = '\n'.join(src_lines[window_start:i + 5])
        if not _RAW_FM_EXTRACT_RE.search(window):
            continue
        rel_src = os.path.relpath(src_path, KB)
        risky_url_access.append(f"{rel_src}:{i+1}: fm.get('url') with raw-file fm — use get_raw_source_url(fm)")
check("Raw-frontmatter readers using direct fm.get('url') (use get_raw_source_url helper)", risky_url_access)

# ─────────────────────────────────────────────────────
pass  # 32. Alias-merged summary corruption (post-bulk-regen, 2026-05-08)
# ─────────────────────────────────────────────────────
# Detects frontmatter where an alias entry has a `summary:` key concatenated
# onto the same line, e.g.
#   aliases:
#     - "X"summary: "..."
#   summary: "..."
# Both summary lines render — YAML last-wins picks the standalone one but
# the merged-in version corrupts the alias. Auto-fix: split the line; if
# there's a standalone `summary:` line elsewhere in the frontmatter, drop
# the merged-in summary. Otherwise promote it to its own line.
total_checks += 1
alias_summary_fixed = []
_ALIAS_SUMMARY_RE = re.compile(
    r'^(\s*-\s*"(?:[^"\\]|\\.)*")(summary:\s*"(?:[^"\\]|\\.)*")\s*$',
    re.MULTILINE,
)
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    m = _ALIAS_SUMMARY_RE.search(text)
    if not m:
        continue
    # Inspect frontmatter only — ignore matches in body.
    if not text.startswith('---'):
        continue
    fm_end_match = re.search(r'\n---[\s]*\n', text[3:])
    if not fm_end_match or m.start() > fm_end_match.end() + 3:
        continue
    fm_block = text[: fm_end_match.end() + 3]
    has_standalone = re.search(r'^summary:\s', fm_block[m.end():], re.MULTILINE)
    if has_standalone:
        # Drop the merged-in summary; keep the alias clean.
        new_text = text[:m.start()] + m.group(1) + text[m.end():]
    else:
        # Promote the merged-in summary to its own line below the alias.
        new_text = text[:m.start()] + m.group(1) + '\n' + m.group(2) + text[m.end():]
    try:
        Path(f).write_text(new_text, encoding='utf-8')
        alias_summary_fixed.append(os.path.relpath(f, KB))
    except OSError:
        pass
check("Alias-merged-summary corruption split (auto-fixed)", alias_summary_fixed, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 33. Web Clipper template metadata bleed (post-bulk-regen, 2026-05-08)
# ─────────────────────────────────────────────────────
# Detects pages where Web Clipper template keys (source:, author:,
# published:, created:, description:, plus a duplicate `tags:` block
# containing only `clippings`) leaked into the canonical Athena
# frontmatter. Symptom in Obsidian: tags overwritten to `[clippings]`,
# summary may render incorrectly. Reports for manual fix — auto-removal
# is too risky given small sample size and the chance of false positives
# on legitimate `source:` keys.
total_checks += 1
clipper_bleed = []
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---'):
        continue
    fm_end_match = re.search(r'\n---[\s]*\n', text[3:])
    if not fm_end_match:
        continue
    fm_block = text[3:fm_end_match.end() + 3]
    # Two `tags:` blocks AND a `- clippings` entry — high-confidence bleed.
    tags_count = len(re.findall(r'^tags:', fm_block, re.MULTILINE))
    has_clippings = re.search(r'^\s*-\s*clippings\s*$', fm_block, re.MULTILINE)
    if tags_count >= 2 and has_clippings:
        clipper_bleed.append(os.path.relpath(f, KB))
check("Web Clipper template metadata bleed (manual fix needed — see frontmatter)", clipper_bleed)

# ─────────────────────────────────────────────────────
pass  # 34. Mixed date_added/last_updated formats break Dataview sort
# ─────────────────────────────────────────────────────
# Some pages were ingested with datetime-iso (`2026-04-13T16:54:22`)
# while most use date-only (`2026-04-13`). Dataview's `SORT date_added
# DESC` infers field type from the first row and silently misbehaves on
# mixed-type comparisons — symptom: the dashboard's Last 20 query loses
# rows or randomizes order. Auto-fix: strip the time component on both
# date_added and last_updated.
total_checks += 1
date_normalized = []
_DATE_DT_RE = re.compile(
    # Match both quoted ("2026-04-13T...") and unquoted (2026-04-13T...)
    # forms. Capture: 1=key 2=leading-ws 3=optional-quote 4=date 5=trailing-quote
    r'^(date_added|last_updated):(\s*)(\"?)(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}[^\n\"]*(\"?)\s*$',
    re.MULTILINE,
)
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not _DATE_DT_RE.search(text):
        continue
    new_text = _DATE_DT_RE.sub(r'\1:\2\3\4\5', text)
    if new_text == text:
        continue
    try:
        Path(f).write_text(new_text, encoding='utf-8')
        date_normalized.append(os.path.relpath(f, KB))
    except OSError:
        pass
check("date_added/last_updated normalized to YYYY-MM-DD (auto-fixed — Dataview sort)", date_normalized, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 35. Binary-content raw .md files — PDFs/images saved as markdown
# ─────────────────────────────────────────────────────
# Detects raw .md files whose content is actually a PDF (or image binary)
# rather than markdown. Symptom: kb add tries to synth them, producing
# wiki pages with garbled binary-byte titles (caught by section 30b but
# regenerated on next kb add). Root cause: the kb-capture Content-Type
# guard (#138) is bypassed in some paths — e.g. when a redirect chain
# changes content-type mid-fetch, or fetch-page wraps binary content in
# a fake YAML header. Auto-fix: move the raw .md to .kb-trash/ so kb add
# stops loop-recreating wiki pages from it.
total_checks += 1
binary_raw_trashed = []
import shutil as _shutil_binraw, datetime as _dt_binraw
_binraw_trash = None
def _is_binary_raw(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(8192)
    except OSError:
        return False
    # Strong signals of PDF content
    if b'%PDF-' in head:
        return True
    if b'\nendobj\n' in head and b'\nstream\n' in head:
        return True
    # High non-printable byte ratio (>25% in first 8KB excluding common
    # UTF-8 continuation bytes)
    nonprint = sum(1 for b in head if b < 9 or (b > 13 and b < 32))
    if nonprint > 200 and len(head) > 1000:
        return True
    return False
for f in glob.glob(os.path.join(KB, 'raw/**/*.md'), recursive=True):
    if not _is_binary_raw(f):
        continue
    if _binraw_trash is None:
        ts = _dt_binraw.datetime.now().strftime('%Y%m%d_%H%M%S')
        _binraw_trash = os.path.join(KB, '.kb-trash', f'{ts}_binary-raw-content-lint')
        os.makedirs(_binraw_trash, exist_ok=True)
    rel = os.path.relpath(f, KB)
    dst_dir = os.path.join(_binraw_trash, os.path.dirname(rel))
    os.makedirs(dst_dir, exist_ok=True)
    try:
        _shutil_binraw.move(f, os.path.join(dst_dir, os.path.basename(f)))
        binary_raw_trashed.append(rel)
    except OSError:
        pass
check("Binary-content raw .md files trashed (PDFs/images saved as .md — breaks kb add)", binary_raw_trashed, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 36. Blockquote-as-summary — comment text leaking into summary slot
# ─────────────────────────────────────────────────────
# X-post pages whose original content is sparse (a tweet linking out + a
# one-line reply) sometimes ended up with the *reply quote* as their
# summary, e.g.
#   summary: "> **MonkeySax @saxboatsec** · [2026-05-03] >  > So great 👍"
# That's the comments section bleeding into the metadata slot — useless
# for dashboards. Heuristic: a summary that starts with a markdown
# blockquote marker `> ` OR matches the X-reply pattern (`> **@handle`)
# almost always means the LLM regen latched onto the wrong block.
# Flag only — fixing requires regenerating the summary via
# bulk-llm-regen-summaries.py, which costs an LLM call. We list the
# affected pages so the user (or the auto-ingest chain) can target
# them with --only-paths.
total_checks += 1
blockquote_summaries = []
_BLOCKQUOTE_SUMMARY_RE = re.compile(r'^[\s"\']*>\s')
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---'):
        continue
    fm_end = re.search(r'\n---[\s]*\n', text[3:])
    if not fm_end:
        continue
    fm_block = text[3:fm_end.end() + 3]
    sm_match = re.search(r'^summary:\s*(.+?)(?=\n[a-z_]+:|\n---)', fm_block, re.MULTILINE | re.DOTALL)
    if not sm_match:
        continue
    summary_val = sm_match.group(1).strip()
    if _BLOCKQUOTE_SUMMARY_RE.match(summary_val):
        blockquote_summaries.append(os.path.relpath(f, KB))
check("Summary is a blockquote (X-comment leaked into summary slot — re-run bulk-llm-regen-summaries.py --only-paths)", blockquote_summaries)

# ─────────────────────────────────────────────────────
pass  # 37. Tag-relevance sniff — over-eager canonical-tag dumping
# ─────────────────────────────────────────────────────
# bulk-llm-regen-tags.py occasionally appends 4-5 canonical tags
# (`ai-agents`, `claude-code`, `llm`, `memory`, `security`) to pages
# where none of those concepts appear anywhere in the title, summary,
# or body — e.g. a deep-learning textbook tagged with `claude-code`.
# Heuristic: lowercase the tag (with hyphen→space variant), search
# title + summary + first 4KB of body. If 3+ tags don't match
# anywhere, the tag set is likely contaminated. Auto-fix: clear
# `tags:` to `[]` so the next bulk-llm-regen-tags run regenerates
# from scratch (the script already filters on the literal `tags: []`
# pattern, so no scheduling hook is needed).
total_checks += 1
tag_drift_cleared = []
_TAGS_LINE_RE = re.compile(r'^tags:\s*\[([^\]]*)\]\s*$', re.MULTILINE)
def _tag_appears_in(tag_norm, haystack):
    # `claude-code` matches both `claude-code` and `claude code` in prose.
    return tag_norm in haystack or tag_norm.replace('-', ' ') in haystack or tag_norm.replace('-', '') in haystack
for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---'):
        continue
    fm_end = re.search(r'\n---[\s]*\n', text[3:])
    if not fm_end:
        continue
    fm_block = text[3:fm_end.end() + 3]
    body = text[fm_end.end() + 3:][:4000].lower()
    title_match = re.search(r'^title:\s*"?([^"\n]+)"?', fm_block, re.MULTILINE)
    summary_match = re.search(r'^summary:\s*"?([^"\n]+)"?', fm_block, re.MULTILINE)
    title_text = (title_match.group(1) if title_match else '').lower()
    summary_text = (summary_match.group(1) if summary_match else '').lower()
    haystack = title_text + ' ' + summary_text + ' ' + body
    tags_match = _TAGS_LINE_RE.search(fm_block)
    if not tags_match:
        continue
    tags_raw = tags_match.group(1).strip()
    if not tags_raw:
        continue
    tags = [t.strip().strip('"').strip("'") for t in tags_raw.split(',') if t.strip()]
    # Skip pages with very few tags — false-positive risk too high.
    if len(tags) < 4:
        continue
    # `paper`, `webpage`, `repo`, `video` etc. are source-type tags, not
    # topic tags — exempt them from the relevance check so they don't
    # count toward the drift threshold either way.
    _SOURCE_TYPE_TAGS = {'paper', 'webpage', 'repo', 'video', 'book', 'image', 'curated-list'}
    topic_tags = [t for t in tags if t not in _SOURCE_TYPE_TAGS]
    drifted = [t for t in topic_tags if not _tag_appears_in(t.lower(), haystack)]
    if len(drifted) >= 3:
        # Auto-fix: clear tags to []. The next bulk-llm-regen-tags pass
        # picks the page up via its `tags: []` filter and regenerates
        # from the actual content.
        new_text = _TAGS_LINE_RE.sub('tags: []', text, count=1)
        if new_text != text:
            try:
                Path(f).write_text(new_text, encoding='utf-8')
                tag_drift_cleared.append(os.path.relpath(f, KB) + f"  ({len(drifted)} unrelated: {', '.join(drifted[:5])})")
            except OSError:
                pass
check("Tag drift cleared (canonical-tag dumping detected — bulk-llm-regen-tags will regenerate)", tag_drift_cleared, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 38. Duplicate X-com raw artifacts (same status-id, different slug)
# ─────────────────────────────────────────────────────
# Legacy state from before bin/lib/wiki_schema.py:_url_to_slug_input
# stripped query params: the same X.com tweet URL with vs without
# `?s=12&t=trackingparam` produced two raw artifact files with different
# filename slugs. The canonicalization fix is in place for new captures,
# but old duplicates persist — surface them so they can be merged
# manually (auto-fix is risky because picking which copy is "canonical"
# requires content inspection).
total_checks += 1
xcom_dup_groups = []
_xcom_groups = {}
_X_PREFIX_RE = re.compile(r'^(x-com-[a-z0-9_]+-status-\d+)', re.IGNORECASE)
for f in glob.glob(os.path.join(KB, 'raw/webpages/artifacts/x-com-*-status-*.md')):
    name = os.path.basename(f)
    m = _X_PREFIX_RE.match(name)
    if not m:
        continue
    prefix = m.group(1).lower()
    _xcom_groups.setdefault(prefix, []).append(name)
for prefix, files in sorted(_xcom_groups.items()):
    if len(files) > 1:
        xcom_dup_groups.append(f"{prefix}: {len(files)} files ({', '.join(sorted(files))})")
check("Duplicate X-com raw artifacts (same status-id, different tracking-param slugs — review and merge manually)", xcom_dup_groups)

# ─────────────────────────────────────────────────────
pass  # 39. Collision-bait slug → auto-rename to URL-derived
# ─────────────────────────────────────────────────────
# Generic title-derived raw artifact slugs (e.g. `post-linkedin.md`,
# `untitled.md`, `post-by-<user>-on-x.md`) collide when a different
# URL with the same generic title-pattern comes in — the second
# capture overwrites the first or wires a wiki page to the wrong
# raw. Auto-fix: rename the raw to its URL-derived slug AND update
# every wiki page that references it. Snapshot before mutation so
# `kb undo` rolls back cleanly if anything looks wrong.
#
# Conservative whitelist of patterns we'll auto-rename — these are
# the ones we've actually seen collide. Long descriptive slugs are
# left alone (they may be deliberate manual renames; renaming them
# would be churn). Slugs that already contain a unique status ID,
# arxiv id, or hash are also safe.
total_checks += 1
slug_renamed = []
slug_skipped = []
_AUTO_RENAME_PATTERNS = [
    re.compile(r'^post-linkedin$'),
    re.compile(r'^untitled$'),
    re.compile(r'^post(?:-page)?$'),
    re.compile(r'^tweet$'),
    re.compile(r'^article$'),
    # X-post slug derived from "Post by USER on X" (no status id) — same
    # user posting multiple times will collide on this slug
    re.compile(r'^post-by-[a-z0-9_]+-on-x$'),
]
try:
    sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
    from wiki_schema import make_slug, _url_to_slug_input  # type: ignore
    from snapshot import snapshot_files  # type: ignore
    _slug_lib_ok = True
except Exception:
    _slug_lib_ok = False
if _slug_lib_ok:
    _rename_batch = []  # list of (old_path, new_path, refs)
    for f in glob.glob(os.path.join(KB, 'raw/**/artifacts/*.md'), recursive=True):
        try:
            text = Path(f).read_text(encoding='utf-8')
        except (IOError, UnicodeDecodeError):
            continue
        if not text.startswith('---'):
            continue
        m = re.search(r'^source:\s*"?(https?://[^"\n]+)', text, re.MULTILINE)
        if not m:
            continue
        url = m.group(1).strip().rstrip('"').rstrip("'")
        try:
            expected = make_slug(_url_to_slug_input(url))
        except Exception:
            continue
        if not expected:
            continue
        actual = os.path.splitext(os.path.basename(f))[0]
        if actual == expected:
            continue
        # Only auto-rename whitelisted collision-bait patterns
        if not any(p.match(actual) for p in _AUTO_RENAME_PATTERNS):
            continue
        new_path = os.path.join(os.path.dirname(f), expected + '.md')
        if os.path.exists(new_path):
            # Target already exists — would clobber. Skip and record.
            slug_skipped.append(f"{os.path.relpath(f, KB)}  (target {expected}.md already exists)")
            continue
        # Find every wiki page that references the old slug or path
        old_rel = os.path.relpath(f, KB)
        old_basename = os.path.basename(f)
        refs_to_update = []
        for wf in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
            try:
                wt = Path(wf).read_text(encoding='utf-8')
            except (IOError, UnicodeDecodeError):
                continue
            if old_rel in wt or old_basename in wt:
                refs_to_update.append(wf)
        _rename_batch.append((f, new_path, refs_to_update, actual, expected, old_rel))
    # Snapshot then mutate
    if _rename_batch:
        files_to_modify = []
        files_to_delete = []
        for old, new, refs, *_rest in _rename_batch:
            files_to_delete.append(old)        # tracked as "deleted" since path changes
            files_to_modify.extend(refs)
        snapshot_files(KB, files_to_delete, files_to_modify,
                       operation='lint-auto-rename',
                       description='kb lint #39: auto-rename collision-bait raw slugs')
        for old, new, refs, actual, expected, old_rel in _rename_batch:
            try:
                os.rename(old, new)
            except OSError as e:
                slug_skipped.append(f"{old_rel}  (rename failed: {e})")
                continue
            new_rel = os.path.relpath(new, KB)
            for ref in refs:
                try:
                    rt = Path(ref).read_text(encoding='utf-8')
                    rt = rt.replace(old_rel, new_rel)
                    rt = rt.replace(actual + '.md', expected + '.md')  # bare basename refs
                    Path(ref).write_text(rt, encoding='utf-8')
                except (IOError, UnicodeDecodeError):
                    pass
            slug_renamed.append(f"{actual}.md → {expected[:40]}.md  ({len(refs)} wiki ref(s) updated)")
check("Collision-bait raw slugs auto-renamed to URL-derived form", slug_renamed, fixed=True)
if slug_skipped:
    check("Collision-bait raw slugs that couldn't be auto-renamed (review manually)", slug_skipped)

# ─────────────────────────────────────────────────────
pass  # 40. X+Web auto-follow pairs → auto-merge X into Web
# ─────────────────────────────────────────────────────
# When an X-post links out to an article and Athena's auto-follow
# captures both, you get `X: <title>` AND `Web: <title>` for the
# same content. The Web page has the actual article body; the
# X-page is just a discovery wrapper. Auto-fix: shell out to
# `kb merge` (which already handles snapshotting + wikilink updates +
# raw_paths/urls deduplication) to collapse the X-page into the
# Web-page, keeping the Web title. The merged page lists both URLs
# in `urls:` so the discovery thread stays linkable.
#
# Safety: skip if either page has been merged already (filename
# already exists with neither X: nor Web: prefix matching the body),
# or if the X-page has substantial original commentary (body > 1500
# chars after stripping the source line — likely a long thread, not
# a thin retweet wrapper).
total_checks += 1
xweb_merged = []
xweb_skipped = []
_xweb_pages = {}
_PREFIX_RE = re.compile(r'^(X:|Web:|X —|Web —)\s*', re.IGNORECASE)
for f in glob.glob(os.path.join(KB, 'wiki/format/webpages/*.md')):
    name = os.path.basename(f)
    if name in ('_TEMPLATE.md', '_Contents.md'):
        continue
    stem = os.path.splitext(name)[0]
    pre_m = _PREFIX_RE.match(stem)
    if not pre_m:
        continue
    prefix = pre_m.group(1).rstrip(' —').rstrip(':').upper()
    body_title = _PREFIX_RE.sub('', stem).lower()
    body_norm = re.sub(r'[^a-z0-9]+', '', body_title)[:60]
    if not body_norm:
        continue
    _xweb_pages.setdefault(body_norm, []).append((prefix, stem, f))
import subprocess as _subproc40
for body_norm, entries in sorted(_xweb_pages.items()):
    by_prefix = {p: (s, fp) for p, s, fp in entries}
    if 'X' not in by_prefix or 'WEB' not in by_prefix:
        continue
    x_stem, x_path = by_prefix['X']
    w_stem, w_path = by_prefix['WEB']
    # Skip if X-page has substantial original commentary (long thread)
    try:
        x_text = Path(x_path).read_text(encoding='utf-8')
        # Approximate body size: strip frontmatter + the first source line
        x_body = re.sub(r'^---\n.+?\n---\n', '', x_text, count=1, flags=re.DOTALL)
        x_body = re.sub(r'^!\[\[raw/favicons/.+?\n+', '', x_body, count=1)
        if len(x_body) > 1500:
            xweb_skipped.append(f"{x_stem}  (body {len(x_body)} chars — likely a long thread, leaving alone)")
            continue
    except (IOError, UnicodeDecodeError):
        continue
    # Pick a sensible merged-page name: the Web body title (already free of
    # the prefix). Fall back to the X body title if Web's looks worse.
    merged_name = re.sub(r'^(Web:|Web —)\s*', '', w_stem, count=1).strip()
    if not merged_name:
        merged_name = re.sub(r'^(X:|X —)\s*', '', x_stem, count=1).strip()
    # Shell out to kb merge — uses the same code path as user-driven merges
    # so snapshotting / dedup / wikilink updates all work uniformly. The
    # Web page goes first so source_type and out_dir derive from it.
    cmd = [os.path.join(KB, 'bin', 'kb'), 'merge', w_stem, x_stem,
           '--into', merged_name, '--yes']
    try:
        result = _subproc40.run(cmd, capture_output=True, text=True, timeout=60, cwd=KB)
        if result.returncode == 0:
            xweb_merged.append(f"{x_stem} + {w_stem} → {merged_name}")
        else:
            err = (result.stderr or result.stdout or '').strip()[:200]
            xweb_skipped.append(f"{x_stem} + {w_stem}  (kb merge failed: {err})")
    except (_subproc40.TimeoutExpired, OSError) as e:
        xweb_skipped.append(f"{x_stem} + {w_stem}  (merge spawn failed: {e})")
check("X+Web auto-follow pairs auto-merged (kb merge into Web title)", xweb_merged, fixed=True)
if xweb_skipped:
    check("X+Web pairs that couldn't be auto-merged (review manually)", xweb_skipped)

# ─────────────────────────────────────────────────────
pass  # 41. Wiki pages whose raw_path file is empty or missing
# ─────────────────────────────────────────────────────
# A wiki page that references a 0-byte raw_path file (or a path that
# doesn't exist on disk) means the capture failed to write the raw
# artifact properly — Local Copy in Obsidian shows nothing. The wiki
# body usually still has good LLM-synthesized content, but the
# round-trip from URL to disk-of-record is broken.
#
# Flag-only: fixing requires re-capturing the URL or promoting the
# wiki body to a properly-slugged raw artifact, both of which require
# judgment. Surfacing the broken pages here lets the user (or a
# follow-up auto-fix lint) act on them.
total_checks += 1
empty_raw_pages = []
for f in glob.glob(os.path.join(KB, 'wiki/format/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---'):
        continue
    fm_end = re.search(r'\n---[\s]*\n', text[3:])
    if not fm_end:
        continue
    fm_block = text[3:fm_end.end() + 3]
    rp_match = re.search(r'^raw_path:\s*"?([^"\n]+)', fm_block, re.MULTILINE)
    raw_paths = []
    if rp_match:
        raw_paths.append(rp_match.group(1).strip().rstrip('"').rstrip("'"))
    rp_list_match = re.search(r'^raw_paths:\s*\n((?:\s+-\s+.+\n)+)', fm_block, re.MULTILINE)
    if rp_list_match:
        for line in rp_list_match.group(1).split('\n'):
            m = re.match(r'\s+-\s+"?([^"]+)', line)
            if m:
                raw_paths.append(m.group(1).strip().rstrip('"').rstrip("'"))
    page = os.path.relpath(f, KB)
    for rp in raw_paths:
        abs_rp = os.path.join(KB, rp)
        if not os.path.isfile(abs_rp):
            empty_raw_pages.append(f"{page}  → {rp} (missing on disk)")
        elif os.path.getsize(abs_rp) == 0:
            empty_raw_pages.append(f"{page}  → {rp} (0 bytes — capture failed)")
check("Wiki pages with empty/missing raw_path (capture broken — re-capture or promote wiki body to raw)", empty_raw_pages)

# ─────────────────────────────────────────────────────
pass  # 42. Generic-titled wiki pages (\"Post LinkedIn\", etc.)
# ─────────────────────────────────────────────────────
# When raw artifacts are saved with title-derived slugs from generic
# page titles ("Post | LinkedIn", "Untitled"), the wiki page that
# wraps them inherits an equally generic title like "LinkedIn: Post
# LinkedIn". These add no information and conflict with future
# captures of the same page-title pattern.
#
# Detect titles that match generic patterns; flag for rename.
# Auto-fix is risky (renaming requires a meaningful title which
# requires reading the body content), so flag-only.
total_checks += 1
generic_titled = []
_GENERIC_TITLE_RE = re.compile(
    r'^(LinkedIn|X|Web|Reddit|Threads):\s*'
    r'(Post|Untitled|Page|Home|Index|Article|Story|Feed)'
    r'(?:\s+\1)?\s*$',  # optional repetition like "LinkedIn: Post LinkedIn"
    re.IGNORECASE,
)
for f in glob.glob(os.path.join(KB, 'wiki/format/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    title = os.path.splitext(os.path.basename(f))[0]
    if _GENERIC_TITLE_RE.match(title):
        generic_titled.append(os.path.relpath(f, KB))
check("Wiki pages with generic uninformative titles (rename via kb rename)", generic_titled)

# ─────────────────────────────────────────────────────
pass  # 43. Suspiciously short raw artifacts (truncated capture)
# ─────────────────────────────────────────────────────
# A raw artifact under ~1KB is almost always an incomplete capture —
# tweets are short but social-clip pipelines append "## Links Found"
# sections that push them well past 1KB; LinkedIn/blog posts are
# multi-paragraph and easily 5-10KB; papers/repos are much larger.
# A 200-byte raw with a substantive `source:` URL is a smoking gun
# for a truncated capture (the "I got the post-linkedin.md size 0"
# bug from this morning, and the "wiki body promoted to raw without
# the original's full content" trap from the same session).
#
# Flag-only: fixing requires re-capturing or finding the original
# clip. Surfacing the broken pages here lets the user act, and the
# auto-ingest pipeline can re-trigger captures during the next sync.
total_checks += 1
truncated_raws = []
_TRUNCATED_THRESHOLD_BYTES = 800
for f in glob.glob(os.path.join(KB, 'raw/**/artifacts/*.md'), recursive=True):
    try:
        size = os.path.getsize(f)
    except OSError:
        continue
    if size >= _TRUNCATED_THRESHOLD_BYTES:
        continue
    if size == 0:
        continue  # already covered by Section 41
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---'):
        continue
    # Must have a source URL — otherwise this is metadata not a captured page
    if not re.search(r'^source:\s*"?https?://', text, re.MULTILINE):
        continue
    rel = os.path.relpath(f, KB)
    truncated_raws.append(f"{rel}  ({size} bytes — likely truncated capture)")
check("Suspiciously short raw artifacts (truncated capture — re-run kb add or check Web Clipper output)", truncated_raws)

# ─────────────────────────────────────────────────────
pass  # 44. Wall-of-text raw bodies (no paragraph breaks)
# ─────────────────────────────────────────────────────
# When LinkedIn/X posts get clipped, their HTML renderers omit
# paragraph breaks and the entire post body lands as one giant line.
# Hard to read, hard to skim. Detect raws whose longest single line
# in the body exceeds 1500 chars — that's a clear wall-of-text signal.
#
# Flag-only: fixing requires editorial paragraph breaks (where to
# split, which markers to use), which lint shouldn't decide silently.
total_checks += 1
wall_of_text_raws = []
for f in glob.glob(os.path.join(KB, 'raw/**/artifacts/*.md'), recursive=True):
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if not text.startswith('---'):
        continue
    # Skip frontmatter; check body
    fm_end = re.search(r'\n---[\s]*\n', text[3:])
    if not fm_end:
        continue
    body = text[3 + fm_end.end():]
    longest_line = max((len(line) for line in body.split('\n')), default=0)
    if longest_line > 1500:
        rel = os.path.relpath(f, KB)
        wall_of_text_raws.append(f"{rel}  (longest line: {longest_line} chars — needs paragraph breaks)")
check("Wall-of-text raw bodies (longest line > 1500 chars — readability fix)", wall_of_text_raws)

# ─────────────────────────────────────────────────────
pass  # 45. Social posts missing canonical-source cross-link → auto-fix
# ─────────────────────────────────────────────────────
# When a social post (X/LinkedIn) references a paper, repo, or article
# but its wiki page doesn't link to the canonical source, auto-add the
# cross-link if both pages exist locally. Uses canonical_source.py
# Tier 1 (regex over raw body) — no LLM calls needed. Falls through
# to flagging when the canonical source was found but isn't yet a
# wiki page (the user / auto-ingest needs to capture it first).
total_checks += 1
xlink_added = []
xlink_pending_capture = []
try:
    sys.path.insert(0, os.path.join(KB, 'bin', 'lib'))
    from canonical_source import extract_canonical_urls  # type: ignore
    _canonical_lib_ok = True
except Exception:
    _canonical_lib_ok = False
if _canonical_lib_ok:
    # Build URL → wiki page name index so we can look up cross-links.
    _url_to_wiki: dict[str, str] = {}
    for wf in glob.glob(os.path.join(KB, 'wiki/format/**/*.md'), recursive=True):
        if os.path.basename(wf) in ('_TEMPLATE.md', '_Contents.md'):
            continue
        try:
            wt = Path(wf).read_text(encoding='utf-8')
        except (IOError, UnicodeDecodeError):
            continue
        # Index every URL this wiki page claims
        for m in re.finditer(r'^url:\s*"?(https?://[^"\n]+)', wt, re.MULTILINE):
            _url_to_wiki[m.group(1).rstrip('"').rstrip("'")] = os.path.splitext(os.path.basename(wf))[0]
        for m in re.finditer(r'^\s+-\s+"?(https?://[^"\n]+)', wt, re.MULTILINE):
            _url_to_wiki[m.group(1).rstrip('"').rstrip("'")] = os.path.splitext(os.path.basename(wf))[0]

    # Index raw → wiki for the social raws we'll process
    for f in glob.glob(os.path.join(KB, 'raw/webpages/artifacts/*.md')):
        name = os.path.basename(f)
        if not name.startswith(('x-com-', 'post-by', 'linkedin-com-', 'www-linkedin-com-', 'post-linkedin')):
            continue
        try:
            raw_text = Path(f).read_text(encoding='utf-8')
        except (IOError, UnicodeDecodeError):
            continue
        canonical_urls = extract_canonical_urls(raw_text)
        if not canonical_urls:
            continue
        # Find the wiki page(s) referencing this raw
        raw_rel = os.path.relpath(f, KB)
        for wf in glob.glob(os.path.join(KB, 'wiki/format/webpages/*.md')):
            try:
                wt = Path(wf).read_text(encoding='utf-8')
            except (IOError, UnicodeDecodeError):
                continue
            if raw_rel not in wt and name not in wt:
                continue
            wiki_stem = os.path.splitext(os.path.basename(wf))[0]
            for canon_url in canonical_urls:
                # Skip if wiki already references this URL or a [[wikilink]] to its page
                if canon_url in wt:
                    continue
                target_page = _url_to_wiki.get(canon_url)
                if not target_page:
                    # Canonical URL referenced but not yet ingested. Actively
                    # SURFACE it to inbox/url-new.txt (discover-and-surface)
                    # instead of only flagging — the old path printed "queued"
                    # but never wrote, so nothing was ever pulled in. (C3)
                    # queue_canonical_urls dedups against url-new.txt AND
                    # url-resolved.tsv, so removed/already-captured URLs are
                    # not re-queued, and re-running lint is idempotent.
                    try:
                        from canonical_source import queue_canonical_urls  # type: ignore
                        queue_canonical_urls(KB, '', [canon_url])
                    except Exception:
                        pass
                    xlink_pending_capture.append(
                        f"{wiki_stem}  → {canon_url} (surfaced to inbox, awaiting ingest)"
                    )
                    continue
                if f'[[{target_page}]]' in wt:
                    continue
                # Auto-fix: append to `related:` list and Connections section.
                # Add to frontmatter `related:` list
                new_link = f'  - "[[{target_page}]]"'
                if re.search(r'^related:\s*\n(?:\s+-\s+.+\n)*', wt, re.MULTILINE):
                    wt = re.sub(
                        r'(^related:\s*\n(?:\s+-\s+.+\n)*)',
                        lambda m: m.group(1) + new_link + '\n',
                        wt, count=1, flags=re.MULTILINE,
                    )
                else:
                    wt = re.sub(
                        r'(^last_updated:.+\n)',
                        lambda m: m.group(1) + 'related:\n' + new_link + '\n',
                        wt, count=1, flags=re.MULTILINE,
                    )
                # Add to body Connections section
                if '## Connections' in wt:
                    annotation = "canonical source (auto-detected from social post body)"
                    wt = wt.replace(
                        '## Connections',
                        f'## Connections\n\n- [[{target_page}]] — {annotation}',
                        1,
                    )
                    # Collapse duplicate Connections marker
                    wt = re.sub(r'## Connections\n\n- \[\[[^\n]+\n\n## Connections',
                                lambda m: m.group(0).rsplit('## Connections', 1)[0].rstrip() + '\n\n## Connections',
                                wt, count=1)
                try:
                    Path(wf).write_text(wt, encoding='utf-8')
                    xlink_added.append(f"{wiki_stem} ↔ {target_page}")
                except OSError:
                    pass
                # Also stamp `discovered_via:` on the CANONICAL source page
                # so the dashboard knows it was auto-pulled (vs user-clipped).
                # Inherit the social post's date_added so the canonical
                # source sorts with the post that triggered it, not with
                # the day auto-ingest happened to run.
                target_files = [tf for tf in glob.glob(os.path.join(KB, 'wiki/format/**/*.md'), recursive=True)
                                if os.path.splitext(os.path.basename(tf))[0] == target_page]
                if target_files:
                    target_path = target_files[0]
                    try:
                        tt = Path(target_path).read_text(encoding='utf-8')
                    except (IOError, UnicodeDecodeError):
                        continue
                    if 'discovered_via:' in tt:
                        continue  # already stamped
                    new_field = f'discovered_via: "[[{wiki_stem}]]"\n'
                    # Insert after `last_updated:` or after frontmatter close
                    if re.search(r'^last_updated:.+\n', tt, re.MULTILINE):
                        tt = re.sub(
                            r'(^last_updated:.+\n)',
                            lambda m: m.group(1) + new_field,
                            tt, count=1, flags=re.MULTILINE,
                        )
                    # Inherit social post's date_added
                    src_da_m = re.search(r'^date_added:\s*(.+)$', wt, re.MULTILINE)
                    if src_da_m:
                        src_da = src_da_m.group(1).strip()
                        tt = re.sub(
                            r'^date_added:\s*.+$',
                            f'date_added: {src_da}',
                            tt, count=1, flags=re.MULTILINE,
                        )
                    try:
                        Path(target_path).write_text(tt, encoding='utf-8')
                    except OSError:
                        pass
check("Social-post canonical-source cross-links auto-added", xlink_added, fixed=True)
if xlink_pending_capture:
    check("Canonical sources queued but not yet ingested (next sync will pull them)", xlink_pending_capture)

# ─────────────────────────────────────────────────────
pass  # 46. Wiki pages with invalid YAML frontmatter (auto-fix)
# ─────────────────────────────────────────────────────
# Obsidian shows "invalid properties" on the page header when YAML
# parse fails. Common causes: title/summary contains backslashes
# (LaTeX `\SOLtrue`, embedded `\"` quotes that got mangled into `\Y`)
# violating double-quoted YAML escape rules; binary garbage from PDF
# extraction leaking into [[wikilinks]]. Auto-fix: switch problem
# fields to single-quoted YAML (which accepts backslashes and double-
# quotes verbatim) and strip control-char garbage from list entries.
total_checks += 1
yaml_fixed = []
yaml_unfixed = []
try:
    import yaml as _yaml_lib
    _yaml_ok = True
except ImportError:
    _yaml_ok = False
if _yaml_ok:
    for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
        if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
            continue
        try:
            text = Path(f).read_text(encoding='utf-8')
        except (IOError, UnicodeDecodeError):
            continue
        m = re.match(r'^(---\n)(.*?)(\n---\n)(.*)', text, re.DOTALL)
        if not m:
            continue
        pre, fm, post, body = m.groups()
        try:
            _yaml_lib.safe_load(fm)
            continue  # YAML parses fine
        except _yaml_lib.YAMLError:
            pass
        # Strip control chars, requote problem fields
        new_fm = ''.join(c for c in fm if ord(c) >= 32 or c in '\n\t')
        new_fm = re.sub(
            r'^\s+-\s+"\[\[[^\]]*[^\x20-\x7E -￿][^\]]*\]\]"\n?',
            '',
            new_fm,
            flags=re.MULTILINE,
        )
        new_lines = []
        for line in new_fm.split('\n'):
            kv_m = re.match(r'^(title|summary|description):\s*"(.+)"\s*$', line)
            if kv_m:
                key, val = kv_m.group(1), kv_m.group(2)
                try:
                    _yaml_lib.safe_load(f'{key}: "{val}"')
                    new_lines.append(line)
                    continue
                except _yaml_lib.YAMLError:
                    val_s = val.replace("'", "''")
                    new_lines.append(f"{key}: '{val_s}'")
                    continue
            new_lines.append(line)
        new_fm_joined = '\n'.join(new_lines)
        try:
            _yaml_lib.safe_load(new_fm_joined)
        except _yaml_lib.YAMLError as e:
            yaml_unfixed.append(f"{os.path.relpath(f, KB)}  ({str(e)[:80]})")
            continue
        try:
            Path(f).write_text(pre + new_fm_joined + post + body, encoding='utf-8')
            yaml_fixed.append(os.path.relpath(f, KB))
        except OSError:
            pass
check("Invalid YAML frontmatter auto-fixed (control chars stripped, bad-escape fields requoted)", yaml_fixed, fixed=True)
if yaml_unfixed:
    check("Invalid YAML frontmatter that auto-fix couldn't repair (manual review)", yaml_unfixed)

# ─────────────────────────────────────────────────────
pass  # 46b. Frontmatter scalar spanning multiple physical lines (auto-fix)
# ─────────────────────────────────────────────────────
# A double-quoted YAML scalar that spans physical lines (e.g. an X.com
# <title> that packed the whole multi-line tweet into the title) is valid
# YAML via line folding — so #46's parse check passes it — but Obsidian's
# Properties parser rejects a value split across physical lines as "Invalid
# properties". Auto-fix: collapse any multi-physical-line double-quoted
# scalar onto one line, turning interior newlines into \n escapes. Scans raw
# AND wiki (raw frontmatter is user-visible in Obsidian too). Root cause is
# fixed in raw_writer._yaml_escape (now escapes newlines); this repairs files
# written before that fix. Witnessed 2026-05-26 (X post local copy).
total_checks += 1
ml_scalar_fixed = []
def _collapse_ml_fm_scalars(fm_text):
    # `: "` opens a quoted value; value chars are non-quote/backslash, an
    # escaped pair (\"), or a newline; non-greedy to the first closing `"`.
    def _repl(mm):
        return mm.group(1) + mm.group(2).replace('\r', '').replace('\n', '\\n') + mm.group(3)
    return re.sub(r'(: ")((?:[^"\\\n]|\\.|\n)*?)(")', _repl, fm_text)
for f in (glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True)
          + glob.glob(os.path.join(KB, 'raw/**/*.md'), recursive=True)):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    mm_fm = re.match(r'^(---\n)(.*?)(\n---\n)(.*)', text, re.DOTALL)
    if not mm_fm:
        continue
    pre_fm, fm_blk, post_fm, body_fm = mm_fm.groups()
    new_fm_blk = _collapse_ml_fm_scalars(fm_blk)
    if new_fm_blk == fm_blk:
        continue
    try:
        Path(f).write_text(pre_fm + new_fm_blk + post_fm + body_fm, encoding='utf-8')
        ml_scalar_fixed.append(os.path.relpath(f, KB))
    except OSError:
        pass
check("Multi-line frontmatter scalars collapsed (Obsidian 'Invalid properties')", ml_scalar_fixed, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 47. Hot-linked images / <video> tags dominating page top → relocate
# ─────────────────────────────────────────────────────
# When an X post is captured, X's HTML often places a full-width
# header image plus an embedded <video> tag right at the top of the
# tweet body. Obsidian renders the image at full pane width (covering
# the prose underneath) and won't render the <video> tag at all (just
# shows the raw HTML). Auto-fix:
#   1. Strip <video>...</video> blocks (unrenderable in Obsidian)
#   2. Strip orphan timestamps like "0:03" left behind by stripped video tags
#   3. Move all top-positioned `![Image](https://pbs.twimg.com/...)` to a
#      `## Media` section at the bottom, with width-constrained `|400` form
# Idempotent: pages already containing `## Media` are skipped.
total_checks += 1
media_relocated = []
for f in glob.glob(os.path.join(KB, 'wiki/format/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    if '## Media' in text:
        continue  # already fixed
    # Detect the pattern: hot-linked Twitter image OR <video> tag in first ~15 lines after frontmatter
    fm_match = re.match(r'^---\n.*?\n---\n', text, re.DOTALL)
    if not fm_match:
        continue
    head_block = text[fm_match.end():fm_match.end()+2000]
    has_image = bool(re.search(r'^!\[[^]]*\]\(https://pbs\.twimg\.com/[^)]+\)', head_block, re.MULTILINE))
    has_video = '<video' in head_block
    if not (has_image or has_video):
        continue
    # Strip <video>...</video> blocks
    new_text = re.sub(r'<video[^>]*>.*?</video>\s*', '', text, flags=re.DOTALL)
    # Strip orphan time stamps left behind
    new_text = re.sub(r'\n\s*\d+:\d+\s*\n', '\n', new_text)
    # Collect Twitter media image URLs (preserve order)
    media_urls = re.findall(r'!\[[^]]*\]\((https://pbs\.twimg\.com/[^)]+)\)', new_text)
    # Remove inline image lines that match the Twitter pattern
    new_text = re.sub(
        r'^!\[[^]]*\]\(https://pbs\.twimg\.com/[^)]+\)\s*\n',
        '',
        new_text,
        flags=re.MULTILINE,
    )
    if not media_urls and new_text == text:
        # Only had <video> tags, no images — still worth committing strip
        if new_text != text:
            try:
                Path(f).write_text(new_text, encoding='utf-8')
                media_relocated.append(os.path.relpath(f, KB) + ' (video tag stripped)')
            except OSError:
                pass
        continue
    # Build ## Media section at bottom
    if media_urls:
        media_block = '\n\n## Media\n\n' + '\n\n'.join(
            f'![Image|400]({u})' for u in media_urls
        ) + '\n'
        # Insert before ## Connections / ## Keywords if they exist, else append
        if '## Connections' in new_text:
            new_text = new_text.replace('## Connections', media_block + '\n## Connections', 1)
        elif '## Keywords' in new_text:
            new_text = new_text.replace('## Keywords', media_block + '\n## Keywords', 1)
        else:
            new_text = new_text.rstrip() + media_block
    if new_text != text:
        try:
            Path(f).write_text(new_text, encoding='utf-8')
            media_relocated.append(os.path.relpath(f, KB))
        except OSError:
            pass
check("Hot-linked images / video tags relocated to bottom ## Media section", media_relocated, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 48. Leaked raw frontmatter in wiki body → strip
# ─────────────────────────────────────────────────────
# When auto-ingest creates a wiki page from a raw artifact, the raw's
# own frontmatter (title/source/captured_at/clipped_via/...) sometimes
# gets pasted into the wiki body verbatim — a literal `---\n...\n---`
# block right after the Source line. Obsidian renders the `---` as a
# horizontal rule and the YAML keys as plain text, making the page
# look like it has duplicate metadata. The bug is in the wiki-page
# generator (didn't strip the raw's frontmatter before embedding the
# content); this lint cleans up existing victims.
total_checks += 1
leaked_fm_fixed = []
for f in glob.glob(os.path.join(KB, 'wiki/format/**/*.md'), recursive=True):
    if os.path.basename(f) in ('_TEMPLATE.md', '_Contents.md'):
        continue
    try:
        text = Path(f).read_text(encoding='utf-8')
    except (IOError, UnicodeDecodeError):
        continue
    # Locate wiki's own frontmatter; skip if not present
    fm_m = re.match(r'^---\n.*?\n---\n', text, re.DOTALL)
    if not fm_m:
        continue
    body = text[fm_m.end():]
    # Detect a leaked-frontmatter block: starts with `---`, contains
    # 2+ key:value lines, ends with `---`. Strip the whole block plus
    # any leading blank lines around it.
    leak_m = re.search(
        r'\n---\n(?:\w[\w_]*:.+\n){2,}---\n',
        body,
    )
    if not leak_m:
        continue
    new_body = body[:leak_m.start()] + body[leak_m.end():]
    new_text = text[:fm_m.end()] + new_body
    # Collapse any double-blank-line residue
    new_text = re.sub(r'\n{3,}', '\n\n', new_text)
    if new_text != text:
        try:
            Path(f).write_text(new_text, encoding='utf-8')
            leaked_fm_fixed.append(os.path.relpath(f, KB))
        except OSError:
            pass
check("Leaked raw frontmatter stripped from wiki body (auto-fix)", leaked_fm_fixed, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 49. Raw .md files outside the artifacts/ subdir → auto-relocate
# ─────────────────────────────────────────────────────
# Every raw category (papers/repos/webpages/videos/images) stores its
# real artifacts under `raw/<cat>/artifacts/`. The orphan walker, the
# wiki-page generator, the canonical-source discovery, and every
# cross-reference query all assume that path. A `.md` sitting at
# `raw/<cat>/<file>.md` (one level above artifacts/) is invisible to
# the system — it never becomes a wiki page, never gets a summary,
# never appears on the Recently Added dashboard. The brijpandeyji
# LinkedIn post sat in this hole for hours before discovery (root
# cause: bin/kb's clipper-processing loop wrote to the legacy path
# `raw/webpages/<slug>.md` instead of `raw/webpages/artifacts/<slug>.md`).
# That source-side bug is now patched, but this lint is the permanent
# safety net — auto-moves any future drift into artifacts/.
total_checks += 1
relocated_raws = []
_RAW_TOPLEVEL_KEEP = {'_Contents.md', '_TEMPLATE.md', 'README.md'}
for cat_dir_name in ('papers', 'repos', 'webpages', 'videos', 'images'):
    cat_dir = os.path.join(KB, 'raw', cat_dir_name)
    if not os.path.isdir(cat_dir):
        continue
    artifacts_dir = os.path.join(cat_dir, 'artifacts')
    for f in glob.glob(os.path.join(cat_dir, '*.md')):
        base = os.path.basename(f)
        if base in _RAW_TOPLEVEL_KEEP:
            continue
        target = os.path.join(artifacts_dir, base)
        if os.path.exists(target):
            # Same-named raw already canonical; the parent-level copy is
            # a stale duplicate. Don't auto-trash here (data loss risk);
            # surface as an issue so the user can compare and decide.
            issues_list = [(os.path.relpath(f, KB),
                            f'duplicate of {os.path.relpath(target, KB)}')]
            check(f"Raw .md outside artifacts/ — duplicate exists in artifacts/",
                  issues_list, fixed=False)
            continue
        try:
            os.makedirs(artifacts_dir, exist_ok=True)
            os.rename(f, target)
            relocated_raws.append(
                f'{os.path.relpath(f, KB)} → {os.path.relpath(target, KB)}'
            )
        except OSError:
            pass
check("Raw .md files relocated into artifacts/ (auto-fix)",
      relocated_raws, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 50. URL-shaped wiki titles → auto-rename from page summary
# ─────────────────────────────────────────────────────
# Symptom: wiki page titles like 'LinkedIn: — https:media.licdn.com
# dmsimagev2D5622AQFS3mmaNDdvvQfeeds' — what's left after apply_naming_
# convention strips slashes and colons from a URL that leaked into the
# title slot via the body-scan fallback (build_fallback_data picked a
# line like '[**#tag**](url)![View image](https://media.licdn.com/...)'
# because the line starts with `[**` and slipped every prefix-skip
# check, then the embedded image URL survived through to the filename
# sanitizer). The writer side is now defended (wiki_page.py 0.12.2 +
# tests in test_writers.TestBuildFallbackDataImageURLRejection), but
# existing pages from before the fix still carry garbage titles.
#
# Auto-fix: derive a clean title from the page itself — first the
# bold-quoted phrase inside the first `## Key Findings` bullet (most
# Athena-LLM bodies put the thesis there in bold), then a Person-Name
# parenthesis from the summary, then a 70-char truncation of the
# summary as last resort. Apply naming convention so the platform
# prefix matches fresh ingest, rename in place, update wikilinks,
# write a redirect stub at the old name so existing references resolve.
total_checks += 1
_URL_TITLE_SYMPTOM_RE = re.compile(
    r'^(LinkedIn|X|Web|GitHub|Drive|PDF|YouTube|arXiv|OneDrive|Dropbox|'
    r'Markdown|Word|Excel|PowerPoint):\s*(?:[—\-]\s*)?https?:',
    re.IGNORECASE,
)
_THESIS_BOLD_RE = re.compile(
    r'^##\s+Key Findings\s*\n+\s*-\s+[^\n]*?\*\*"?([^*"\n]{6,80}?)"?\*\*',
    re.MULTILINE,
)
_PERSON_PAREN_RE = re.compile(
    r'\(([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z\.]+){1,3})\)'
)
_SUMMARY_RE = re.compile(r'^summary:\s*["\']?(.+?)["\']?\s*$', re.MULTILINE)
_URL_FM_RE  = re.compile(r'^url:\s*["\']?(\S+?)["\']?\s*$', re.MULTILINE)
_STYPE_FM_RE = re.compile(r'^source_type:\s*["\']?(\w+)["\']?\s*$', re.MULTILINE)

def _derive_title_for_url_shaped_page(text):
    """Pick the best fallback title from a wiki page that lost its
    real title to an embedded image URL. Returns None if nothing
    extractable — caller surfaces those for manual `kb rename`."""
    m = _THESIS_BOLD_RE.search(text)
    if m:
        cand = m.group(1).strip().strip('"')
        if 6 <= len(cand) <= 80:
            return cand
    sm = _SUMMARY_RE.search(text)
    if sm:
        summary = sm.group(1).strip()
        pm = _PERSON_PAREN_RE.search(summary)
        if pm:
            return pm.group(1)
        if len(summary) >= 30:
            cand = summary[:80].rsplit(' ', 1)[0].rstrip(' ,;:—-.')
            if len(cand) >= 20:
                return cand
    return None

url_titled_fixed = []
url_titled_skipped = []
for wfp in sorted(glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True)):
    base = os.path.basename(wfp)
    if base in ('_TEMPLATE.md', '_Contents.md'):
        continue
    stem = os.path.splitext(base)[0]
    if not _URL_TITLE_SYMPTOM_RE.match(stem):
        continue
    try:
        with open(wfp, 'r', encoding='utf-8') as fh:
            text = fh.read()
    except (IOError, UnicodeDecodeError):
        continue
    # Skip redirect stubs — they're forwarders, not content. Renaming
    # a stub would create stub-of-stub chains. The other auto-fix paths
    # will eventually trash stale stubs anyway (Section 28).
    if 'redirect: true' in text or 'redirect_to:' in text:
        continue
    url_m = _URL_FM_RE.search(text)
    page_url = url_m.group(1) if url_m else ''
    st_m = _STYPE_FM_RE.search(text)
    src_type = st_m.group(1) if st_m else 'webpage'
    derived = _derive_title_for_url_shaped_page(text)
    if not derived:
        url_titled_skipped.append(f"{stem}  (no derivable title — manual `kb rename`)")
        continue
    new_name = apply_naming_convention(derived, page_url, src_type)
    # Guard against re-deriving back into a URL-shaped name (defense
    # against bugs in the derivation regexes).
    if new_name == stem or _URL_TITLE_SYMPTOM_RE.match(new_name):
        url_titled_skipped.append(f"{stem}  (re-derived to same/garbage name)")
        continue
    new_path = os.path.join(os.path.dirname(wfp), f'{new_name}.md')
    if os.path.exists(new_path):
        url_titled_skipped.append(
            f"{stem}  (target {new_name!r} already exists — manual merge)")
        continue
    # In-place rename: write new title to frontmatter, move file, update
    # wikilinks across vault, write redirect stub at the old path.
    new_text = re.sub(
        r'^(title:\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{new_name}"',
        text, count=1, flags=re.MULTILINE,
    )
    try:
        os.rename(wfp, new_path)
        with open(new_path, 'w', encoding='utf-8') as fh:
            fh.write(new_text)
        # Bump mtime so Dataview re-indexes — same reason as the
        # explicit utime in the main `kb rename` command. Without
        # this, lint-auto-fixed pages stay invisible in Dataview
        # views until Obsidian restarts.
        os.utime(new_path, None)
    except OSError as exc:
        url_titled_skipped.append(f"{stem}  (rename failed: {exc})")
        continue
    # Update wikilinks across the vault — same patterns kb rename uses.
    for f in glob.glob(os.path.join(KB, 'wiki/**/*.md'), recursive=True):
        if os.path.basename(f) in ('.gitkeep', '_TEMPLATE.md'):
            continue
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                txt = fh.read()
        except (IOError, UnicodeDecodeError):
            continue
        new_txt = txt.replace(f'[[{stem}]]', f'[[{new_name}]]')
        new_txt = new_txt.replace(f'"[[{stem}]]"', f'"[[{new_name}]]"')
        new_txt = re.sub(
            r'\[\[' + re.escape(stem) + r'\|([^\]]+)\]\]',
            f'[[{new_name}|\\1]]',
            new_txt,
        )
        if new_txt != txt:
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(new_txt)
    # Write redirect stub at the old path so external [[old]] references
    # still resolve. Uses the canonical wiki_schema.write_wiki_stub —
    # same forwarder shape kb rename produces.
    try:
        from wiki_schema import write_wiki_stub  # type: ignore
        write_wiki_stub(
            vault=Path(KB),
            source_type=src_type if src_type in (
                'paper', 'repo', 'webpage', 'video', 'image', 'entity'
            ) else 'webpage',
            old_name=stem,
            new_name=new_name,
        )
    except Exception as exc:  # noqa: BLE001
        # Stub write is best-effort — the rename itself succeeded.
        print(f"  (URL-title autofix: stub write failed for {stem}: {exc})",
              file=sys.stderr)
    url_titled_fixed.append(f"{stem}  →  {new_name}")

check("URL-shaped wiki titles auto-renamed (image URL leaked into title slot)",
      url_titled_fixed, fixed=True)
check("URL-shaped wiki titles needing manual `kb rename` (no derivable title)",
      url_titled_skipped)

# ═══════════════════════════════════════════════════
# AUTO-EMBED DOCUMENT SLIDES (capture-deep carousels)
# ═══════════════════════════════════════════════════
# A LinkedIn (or similar) document/carousel post captured via capture-deep stores
# its slide images in the RAW as `![View image](../../assets/<post>/<hash>.<ext>)`
# ("View image" is capture-deep's carousel-scrape alt — a precise signal that
# avoids regular article images). The wiki synthesis never embeds them, so the
# deck is invisible on the page the user actually reads. Ensure any page whose
# backing raw holds >= _SLIDE_MIN such slides carries a matching `## Slides`
# section, in document order. Idempotent: once the correct section is present,
# re-runs are no-ops (so a manual section, or a prior pass, is left untouched).
_SLIDE_RE = re.compile(
    r'!\[View image\]\(\.\./\.\./(assets/[^)\s]+\.(?:jpg|jpeg|png|webp|gif))\)',
    re.IGNORECASE,
)
_SLIDE_MIN = 3
slides_embedded = []
for f in wiki_files:
    fm, content = extract_frontmatter(f)
    rp = fm.get('raw_path')
    if isinstance(rp, list):
        rp = rp[0] if rp else None
    if not rp or not isinstance(rp, str):
        continue
    raw_abs = os.path.join(KB, rp)
    if not os.path.isfile(raw_abs):
        continue
    try:
        with open(raw_abs, 'r', encoding='utf-8') as _rf:
            raw_text = _rf.read()
    except (IOError, UnicodeDecodeError):
        continue
    ordered, seen = [], set()
    for p in _SLIDE_RE.findall(raw_text):
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    if len(ordered) < _SLIDE_MIN:
        continue
    desired = "## Slides\n\n" + "\n".join("![[raw/%s]]" % p for p in ordered) + "\n"
    stem = os.path.splitext(os.path.basename(f))[0]
    existing = re.search(r'(?ms)^## Slides\b.*?(?=^## |\Z)', content)
    if existing and all(("![[raw/%s]]" % p) in existing.group(0) for p in ordered):
        continue  # correct section already present — idempotent no-op
    if existing:
        new_content = content[:existing.start()] + desired + "\n" + content[existing.end():]
    else:
        anchor = -1
        for cand in ('\n## Connections', '\n## Keywords'):
            anchor = content.find(cand)
            if anchor != -1:
                break
        if anchor != -1:
            new_content = content[:anchor] + "\n" + desired + "\n" + content[anchor + 1:]
        else:
            new_content = content.rstrip() + "\n\n" + desired
    try:
        with open(f, 'w', encoding='utf-8') as _wf:
            _wf.write(new_content)
        slides_embedded.append("%s  (%d slides)" % (stem, len(ordered)))
    except (IOError, OSError) as _e:
        print("  WARNING: could not embed slides into %s: %s" % (stem, _e), file=sys.stderr)

# Conditional so this Python-only check stays invisible (and doesn't bump
# total_checks) on vaults without capture-deep carousels — keeps lint output
# byte-identical to the frozen Bash oracle for the parity test, which seeds no
# document slides.
if slides_embedded:
    check("Document slides auto-embedded into wiki pages (capture-deep carousels)",
          slides_embedded, fixed=True)

# ─────────────────────────────────────────────────────
pass  # 51. Resurrected dead URLs — stale url-dead.txt record for a URL that
#         was later captured (issue #129 follow-up)
# ─────────────────────────────────────────────────────
# A URL can fail capture (landing in inbox/url-dead.txt), then succeed on a
# later attempt (landing in inbox/url-resolved.tsv with status=captured) while
# its dead record is never cleared. The stale dead row is a data inconsistency:
# the same URL is recorded as both "dead" and "captured", and a stale dead row
# can wrongly suppress a future re-attempt. Auto-fix: drop the dead/failed row(s)
# whose source_url now appears as captured. Both files are header + TSV columns
# (status, description, source_url, resolved_url, type).
#
# Python-only check (the frozen Bash oracle has none); the check() call is
# CONDITIONAL so total_checks — and thus the summary line — stays byte-identical
# to the oracle on vaults with no resurrected URLs (the parity fixture seeds none).
_dead_path = os.path.join(KB, 'inbox', 'url-dead.txt')
_resolved_path = os.path.join(KB, 'inbox', 'url-resolved.tsv')
_resurrected = []

# Match on the CANONICAL url so tracking-suffix variants collapse — a tweet that
# failed with ?s=46&t=… then was captured with ?s=12&t=… (the dominant X/Twitter
# case) still matches. canonicalize() strips only host-specific tracking params,
# so genuinely distinct resources (e.g. ?id=1 vs ?id=2 on a non-tracking host)
# are NOT over-collapsed into a false match. Falls back to a plain strip if the
# helper is somehow unavailable.
try:
    from url_canonical import canonicalize as _canon_129
except Exception:
    _canon_129 = None


def _norm_url_129(u):
    u = (u or '').strip()
    if not u:
        return ''
    if _canon_129 is not None:
        try:
            return _canon_129(u).url.rstrip('/')
        except Exception:
            pass
    return u.rstrip('/')


if os.path.isfile(_dead_path) and os.path.isfile(_resolved_path):
    _captured_urls = set()
    try:
        with open(_resolved_path, 'r', encoding='utf-8') as _rf:
            for _ln in _rf:
                _cols = _ln.rstrip('\n').split('\t')
                if len(_cols) >= 3 and _cols[0] == 'captured':
                    _captured_urls.add(_norm_url_129(_cols[2]))
    except (IOError, UnicodeDecodeError):
        _captured_urls = set()
    if _captured_urls:
        try:
            with open(_dead_path, 'r', encoding='utf-8') as _df:
                _dead_lines = _df.readlines()
        except (IOError, UnicodeDecodeError):
            _dead_lines = []
        _kept = []
        for _ln in _dead_lines:
            _cols = _ln.rstrip('\n').split('\t')
            # Drop only a real dead/failed data row whose source_url is now
            # captured. Header ('status'), blanks, and malformed rows are kept.
            if (len(_cols) >= 3 and _cols[0] in ('dead', 'failed')
                    and _norm_url_129(_cols[2]) in _captured_urls):
                _resurrected.append(_cols[2].strip())
                continue
            _kept.append(_ln)
        if _resurrected:
            # Atomic rewrite (tmp + os.replace) — never truncate url-dead.txt in
            # place. A crash mid-write of a plain 'w' open would lose the ENTIRE
            # dead-URL ledger (an audit trail with no other source of truth);
            # os.replace leaves either the old or the new complete file, never a
            # truncated stub. Matches the tmp-then-rename convention used by every
            # other writer in bin/lib.
            _dead_tmp = _dead_path + '.tmp'
            try:
                with open(_dead_tmp, 'w', encoding='utf-8') as _wf:
                    _wf.writelines(_kept)
                os.replace(_dead_tmp, _dead_path)
            except OSError as _e:
                _resurrected = []  # write failed → report nothing this pass
                print(f"  WARNING: could not rewrite url-dead.txt: {_e}", file=sys.stderr)
                try:
                    os.unlink(_dead_tmp)
                except OSError:
                    pass
if _resurrected:
    check("Stale dead-URL records cleared — URL later captured (issue #129)",
          _resurrected, fixed=True)

# ═══════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════
total_remaining = issues
print(f"\n{'═' * 50}")
if auto_fixed > 0 and total_remaining > 0:
    print(f"  {total_checks} checks · {auto_fixed} auto-fixed · {total_remaining} need attention")
elif auto_fixed > 0 and total_remaining == 0:
    print(f"  {total_checks} checks · {auto_fixed} auto-fixed")
    print("  All issues were auto-fixed!")
elif total_remaining > 0:
    print(f"  {total_checks} checks · {total_remaining} need attention")
else:
    print(f"  {total_checks} checks · all clear!")
if total_remaining > 0:
    print(f"\n  To fix remaining issues, ask Athena:")
    print(f"    \"please fix all lint issues\"")
print(f"{'═' * 50}")
