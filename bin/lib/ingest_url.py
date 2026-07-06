#!/usr/bin/env python3
"""Ingest a file-based URL (PDF, DOCX, XLSX, PPTX, EPUB, ...) into Athena.

Downloads the file to raw/<subdir>/, extracts its title + prose text,
and writes a companion .md raw file that Athena's wiki-page builder
consumes. Prints the raw .md path on success, or an error message +
exits non-zero on failure.

This script exists because `bin/kb-capture` (bash) got unwieldy trying
to handle both platform-specific flows (arxiv, github, tweets) AND
generic file ingestion via URL-suffix matching. Python is a better
fit for Content-Type detection, ZIP/XML parsing, and the per-format
text extractors — all of which live in bin/lib/file_extract.py.

Usage:
    bin/ingest-file <url>

Exit codes:
    0 — raw markdown + source file written
    1 — detection or download failed
    2 — unsupported type (callers should fall through to other flows)
"""

import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error


# This module lives at <root>/bin/lib/ingest_url.py (relocated from the
# standalone bin/ingest-file script so its logic compiles into the source-free
# binary — Phase 4). Its sibling lib modules are on the same directory.
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from url_detect import detect_type  # noqa: E402
from arcus_file import ingest_local_file, ArcusFileError  # noqa: E402


def _vault_root() -> str:
    """Vault root: KB_ROOT env (set by the dispatcher / plugin) → __file__
    (source mode: lib→bin→root, three levels up). Frozen-safe."""
    env = os.environ.get('KB_ROOT')
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.dirname(_LIB_DIR))


# source_type → raw/ subdirectory mapping. Matches the folders Athena's
# wiki-page builder expects (paper→papers, doc→docs, etc.). `webpage`
# is intentionally missing — this script doesn't handle HTML pages;
# they go through the existing bash webpage branch.
_RAW_SUBDIR = {
    'paper':       'papers',
    'doc':         'docs',
    'spreadsheet': 'spreadsheets',
    'slides':      'slides',
    'book':        'books',
    'image':       'images',
}


def _slug_max_chars():
    """Slug length cap, from naming.slug_max_chars (default 100). Mirrors
    naming.filename_max_chars so a file slug isn't clipped tighter than the
    wiki filename it feeds — issue #126, where a hardcoded 60 dropped the
    meaningful tail (`...active-director` instead of `...directory-dacl-backdoors`)."""
    try:
        from config import naming as _naming
        return int(_naming().get('slug_max_chars', 100))
    except Exception:
        return 100


def _slugify(s, max_len=None):
    """Filename-safe slug. Same style the bash script uses for
    consistency with existing raw files. Cap defaults to
    naming.slug_max_chars; when truncation is needed we cut at the last
    hyphen boundary so the tail isn't a mangled half-word (issue #126)."""
    if max_len is None:
        max_len = _slug_max_chars()
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-{2,}', '-', s).strip('-')
    if len(s) > max_len:
        cut = s[:max_len]
        dash = cut.rfind('-')
        # Prefer a clean word boundary, but only if it keeps a reasonable
        # amount of the slug (≥ half the cap) — otherwise a slug with one
        # very long leading token would collapse to almost nothing.
        if dash >= max_len // 2:
            cut = cut[:dash]
        s = cut.strip('-')
    return s or 'untitled'


DRIVE_FILE_RE = re.compile(r'drive\.google\.com/file/d/([^/]+)', re.IGNORECASE)
PDF_MAGIC = b'%PDF-'


def _effective_download_url(url):
    """Rewrite view-only URLs into their direct-download equivalents.
    Drive's `/file/d/<id>/view` serves the HTML viewer; the actual PDF
    is at `/uc?export=download&id=<id>`. Other hosts pass through."""
    m = DRIVE_FILE_RE.search(url)
    if m:
        return f'https://drive.google.com/uc?export=download&id={m.group(1)}'
    return url


def _drive_filename(url, timeout=10):
    """Drive's download endpoint returns a Content-Disposition header
    with the file's real name (e.g. "DS_Cheat_Sheet.pdf"). Fetch that
    via HEAD so we can use it for slug + title instead of the file_id.
    Returns None if HEAD fails or disposition header is absent."""
    if not DRIVE_FILE_RE.search(url):
        return None
    download_url = _effective_download_url(url)
    try:
        req = urllib.request.Request(
            download_url, method='HEAD',
            headers={'User-Agent': 'Athena/1.0 (file ingest)'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            disp = resp.headers.get('Content-Disposition') or ''
        m = re.search(r'filename\*?=(?:[^\'"]*\'\')?"?([^";]+)"?', disp)
        if m:
            return urllib.parse.unquote(m.group(1).strip())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        pass
    return None


def _slug_from_url(url, detected_filename=None):
    """Prefer the server's suggested filename (from Content-Disposition)
    over the URL basename — servers often give a nicer one.
    `filename*=UTF-8''` URL-encoded forms are already decoded by
    detect_type, so we just strip the extension here. Drive view URLs
    have a file_id as basename, which is useless as a slug — we fall
    back to the file_id in that case; the raw markdown title later
    supplies the human-readable name from PDF metadata."""
    if detected_filename:
        name = os.path.splitext(detected_filename)[0]
    else:
        path = urllib.parse.urlparse(url).path or ''
        name = os.path.splitext(os.path.basename(path))[0]
        # Drive view URLs have path like /file/d/<id>/view — basename
        # "view" is meaningless. Use file_id instead.
        if name in ('view', 'edit', '') and 'drive.google.com' in url:
            m = DRIVE_FILE_RE.search(url)
            if m:
                name = 'drive-' + m.group(1)[:16]
    return _slugify(name) if name else _slugify(url)


def _download(url, dest_path, timeout=60, require_pdf=False):
    """Backwards-compatible wrapper around _download_with_reason. Existing
    callers that only need a True/False outcome use this."""
    ok, _reason = _download_with_reason(url, dest_path, timeout=timeout, require_pdf=require_pdf)
    return ok


def _download_with_reason(url, dest_path, timeout=60, require_pdf=False):
    """Stream the URL to disk. Returns (ok, reason) where reason is a
    short tag for failures: 'http_NNN' (with status code), 'pdf_magic_mismatch',
    'timeout', 'connection_refused', 'os_error', or 'empty_body'.

    `require_pdf=True` validates PDF magic bytes at the start of the
    response and refuses to save content that isn't a real PDF (Google
    Drive returns an HTML confirmation page for large files that need
    manual virus-scan acceptance — that's what this guard catches)."""
    effective = _effective_download_url(url)
    try:
        req = urllib.request.Request(
            effective, headers={'User-Agent': 'Athena/1.0 (file ingest)'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                open(dest_path, 'wb') as f:
            if require_pdf:
                first = resp.read(8)
                if not first.startswith(PDF_MAGIC):
                    return False, 'pdf_magic_mismatch'
                f.write(first)
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        return (os.path.getsize(dest_path) > 0, '' if os.path.getsize(dest_path) > 0 else 'empty_body')
    except urllib.error.HTTPError as e:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False, f'http_{e.code}'
    except urllib.error.URLError as e:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        # urllib wraps connection refused / DNS / etc. in URLError with
        # a `reason` attribute — keep its repr in the tag for diagnostics.
        return False, f'url_error:{type(e.reason).__name__ if hasattr(e, "reason") else "unknown"}'
    except TimeoutError:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False, 'timeout'
    except OSError:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False, 'os_error'


def _append_dead_record(vault, url, reason, source_type):
    """Record a failed download in inbox/url-dead.txt so the URL doesn't
    silently vanish (#129). 5-column TSV format matches the existing
    file's header: status, description, source_url, resolved_url, type.

    status convention:
      - 'dead'   for permanent failures (4xx, pdf_magic_mismatch on direct PDF URL)
      - 'failed' for transient failures (timeout, connection refused, 5xx)
        — explicit so retry tooling can distinguish.
    """
    permanent_tags = ('http_4', 'pdf_magic_mismatch')
    is_permanent = any(reason.startswith(p) for p in permanent_tags)
    status = 'dead' if is_permanent else 'failed'
    dead_path = os.path.join(vault, 'inbox', 'url-dead.txt')
    try:
        # Ensure header exists if file is being created.
        new_file = not os.path.isfile(dead_path) or os.path.getsize(dead_path) == 0
        with open(dead_path, 'a', encoding='utf-8') as fh:
            if new_file:
                fh.write('status\tdescription\tsource_url\tresolved_url\ttype\n')
            fh.write(f'{status}\t{reason}\t{url}\t{url}\t{source_type}\n')
    except OSError as e:
        # Failure to record is non-fatal — the user already sees stderr.
        print(f'ingest-file: warning: could not record dead URL ({e})', file=sys.stderr)


def _humanize(name):
    """Turn a filename into a readable title fallback. Preserves the
    original casing when called on a server-provided filename
    (NIST.AI.100-4 → NIST AI 100-4, DS_Cheat_Sheet → DS Cheat Sheet)
    and produces a Title-Case result for lowercased slugs
    (ds-cheat-sheet → DS Cheat Sheet) with known acronyms restored to
    uppercase (ace → ACE, dacl → DACL). Only used when the PDF's own
    Title metadata is absent."""
    words = re.sub(r'[._-]+', ' ', name).strip()
    # If the input is fully lowercase (we got here via slug), title-case it
    # and restore known acronyms. Otherwise preserve the original casing —
    # the server sent it that way for a reason (uppercase acronyms,
    # camelCase proper nouns, etc.).
    if words and words == words.lower():
        titled = words.title()
        return _apply_acronyms(titled)
    return words


def _apply_acronyms(text):
    """Restore known security/tech acronyms to uppercase after .title()
    has lowercased them. Acronym list comes from naming.acronyms in
    athena.default.json — duplicated as a hardcoded fallback list here
    so this script remains usable when bin/lib/config.py isn't on the
    import path."""
    if not text:
        return text
    try:
        if _LIB_DIR not in sys.path:
            sys.path.insert(0, _LIB_DIR)
        from config import naming as _naming
        acronyms = _naming().get('acronyms') or []
    except Exception:  # noqa: BLE001 — config is optional; fall back to defaults
        acronyms = [
            'ACE', 'ACL', 'DACL', 'SACL', 'AD', 'DC', 'DNS', 'TLS', 'SSL',
            'API', 'CVE', 'OS', 'URL', 'URI', 'HTTP', 'HTTPS', 'SQL', 'XML',
            'JSON', 'YAML', 'AI', 'ML', 'LLM', 'GPU', 'CPU', 'SDK', 'MCP',
            'AWS', 'GCP', 'WMI', 'RPC', 'SMB', 'NTLM',
        ]
    acronym_map = {a.lower(): a for a in acronyms}

    def _replace(token):
        m = re.match(r'^(\W*)(\w+)(\W*)$', token, re.UNICODE)
        if not m:
            return token
        lead, word, trail = m.groups()
        canonical = acronym_map.get(word.lower())
        return f'{lead}{canonical}{trail}' if canonical else token

    return ' '.join(_replace(t) for t in text.split(' '))


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    vault = _vault_root()

    info = detect_type(url)
    st, ext = info['source_type'], info['ext']

    if st not in _RAW_SUBDIR:
        # This script doesn't handle webpage/repo/video — those have
        # their own bash flows. Exit 2 is a signal to the caller, not
        # a hard failure.
        print(f'ingest-file: type {st!r} routed elsewhere', file=sys.stderr)
        sys.exit(2)

    # Drive URLs' view endpoint returns HTML (not PDF), so detect_type's
    # HEAD-based Content-Disposition capture is skipped for them. A
    # targeted HEAD on the download endpoint grabs the real filename.
    drive_fn = _drive_filename(url)
    if drive_fn and not info.get('filename'):
        info['filename'] = drive_fn

    subdir = _RAW_SUBDIR[st]
    slug = _slug_from_url(url, info.get('filename'))
    raw_dir = os.path.join(vault, 'raw', subdir)
    os.makedirs(raw_dir, exist_ok=True)
    downloaded = os.path.join(raw_dir, f'{slug}.{ext}')
    raw_md = os.path.join(raw_dir, f'{slug}.md')

    if os.path.exists(raw_md):
        print(f'ingest-file: already exists: {raw_md}', file=sys.stderr)
        sys.exit(1)

    print(f'Downloading ({st}/{ext})...', file=sys.stderr)
    # For PDFs, validate magic bytes — catches Drive's HTML confirm
    # page when a large file needs manual virus-scan acknowledgement.
    download_ok, fail_reason = _download_with_reason(url, downloaded, require_pdf=(ext == 'pdf'))
    if not download_ok:
        # Audit trail: append to inbox/url-dead.txt so failed captures
        # don't silently vanish (#129). Format matches the canonical
        # 5-column TSV: status, description, source_url, resolved_url, type.
        _append_dead_record(vault, url, fail_reason, st)
        if DRIVE_FILE_RE.search(url):
            # Drive download commonly fails when the file isn't
            # publicly downloadable (private / shared-but-auth'd), or
            # when rate-limited. Athena doesn't authenticate with
            # Drive — that's the Web Clipper's job, since it runs in
            # the user's signed-in browser.
            print(
                'ingest-file: Drive download failed. The file may be private '
                'or rate-limited. Open the URL in your browser, then use the '
                'Obsidian Web Clipper to save it into the clippings folder — '
                "Athena's watcher will process it automatically.",
                file=sys.stderr,
            )
        else:
            print(f'ingest-file: download failed for {url} ({fail_reason}). '
                  f'Recorded in inbox/url-dead.txt.', file=sys.stderr)
        sys.exit(1)
    size_kb = os.path.getsize(downloaded) // 1024
    print(f'  saved: raw/{subdir}/{slug}.{ext} ({size_kb} KB)', file=sys.stderr)

    # Title hint: prefer the server-provided filename (preserves case like
    # "NIST AI 100-4") over the humanized slug. arcus's extractor will use
    # PDF metadata title if present; this hint is the fallback.
    original_name = info.get('filename') and os.path.splitext(info['filename'])[0]
    title_hint = _humanize(original_name) if original_name else _humanize(slug)

    # Extract + write raw via arcus_file adapter. Adapter wraps the local
    # file extraction (PdfProvider for .pdf, DocsProvider for .docx/.xlsx/
    # .pptx/.epub) and produces athena's raw .md in the legacy body layout.
    from pathlib import Path
    try:
        raw_path = ingest_local_file(
            vault=Path(vault),
            url=url,
            local_path=Path(downloaded),
            source_type=st,
            ext=ext,
            title_hint=title_hint,
        )
    except ArcusFileError as e:
        print(f'ingest-file: arcus extraction failed: {e}', file=sys.stderr)
        sys.exit(1)

    rel_raw = os.path.relpath(str(raw_path), vault)
    print(f'  wrote: {rel_raw}', file=sys.stderr)
    # stdout = the relative raw path, so the caller can chain into
    # wiki_page.py --raw-path <...>.
    print(rel_raw)


if __name__ == '__main__':
    main()
