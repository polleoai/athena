import sys, os, re, time
kb_root = sys.argv[1]
raw_path = sys.argv[2]
rel_raw = os.path.relpath(raw_path, kb_root)
try:
    with open(raw_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
except (IOError, OSError):
    sys.exit(0)
title = os.path.basename(raw_path).replace('.md', '').replace('-', ' ').title()
m = re.match(r'^#\s+(.+)$', content, re.MULTILINE)
if m: title = m.group(1).strip()
url = ''
m_url = re.search(r'\*\*URL:\*\*\s*(https?\S+)', content)
if m_url: url = m_url.group(1)
source_type = 'paper' if '/papers/' in rel_raw else 'webpage'
body = content
if '## Content' in content: body = content.split('## Content', 1)[1].strip()
body = re.sub(r'^# .+$', '', body, flags=re.MULTILINE).strip()
body = body.replace('&#8211;', '—').replace('&amp;', '&')
summary = re.sub(r'#\s+\S+', '', body[:200]).replace('\n', ' ').strip().replace('"', "'")
if len(body) > 200: summary = summary[:197] + '...'
source_link = f'[Source]({url})\n\n' if url else ''

import sys as _sys
_sys.path.insert(0, os.path.join(kb_root, 'bin', 'lib'))
from wiki_schema import write_wiki_page, SchemaError  # type: ignore
from pathlib import Path as _Path
try:
    wp = write_wiki_page(
        vault=_Path(kb_root), source_type=source_type, title=title,
        summary=summary or '(paper details pending)',
        body=source_link + body[:3000],
        tags=['security'] if source_type == 'paper' else [source_type],
        raw_path=rel_raw, url=url or None,
    )
    print(f"  Created paper page: {os.path.relpath(str(wp), kb_root)}")
except SchemaError:
    sys.exit(0)  # already exists or schema rejected — non-fatal in this batch path
