"""url_detect — athena-specific URL → source_type routing.

Used by bin/ingest-file (PDF/DOCX/XLSX/PPTX/EPUB downloads) and any future
caller that needs to ask "what athena category does this URL belong to?"

This module CANNOT move to arcus — it encodes athena's vault categories
(repo, paper, video, etc.) which arcus has no awareness of. arcus has its
own URL routing (Factory.detect()) but that only distinguishes its four
content kinds (youtube/pdf/docs/html), not athena's wider taxonomy.

Lifted verbatim from the deleted bin/lib/file_extract.py (only `detect_type`
+ its supporting constants; the extraction functions moved to arcus).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request


# ── MIME → (source_type, ext) ───────────────────────────────────
_MIME_MAP: dict[str, tuple[str, str]] = {
    'application/pdf':                                  ('paper', 'pdf'),
    'application/x-pdf':                                ('paper', 'pdf'),
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ('doc', 'docx'),
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':      ('spreadsheet', 'xlsx'),
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': ('slides', 'pptx'),
    'application/epub+zip':                             ('book', 'epub'),
    'image/png':                                        ('image', 'png'),
    'image/jpeg':                                       ('image', 'jpg'),
    'image/jpg':                                        ('image', 'jpg'),
    'image/gif':                                        ('image', 'gif'),
    'image/webp':                                       ('image', 'webp'),
}


# ── URL extension → (source_type, ext) ──────────────────────────
_EXT_MAP: dict[str, tuple[str, str]] = {
    'pdf':  ('paper', 'pdf'),
    'docx': ('doc', 'docx'),
    'xlsx': ('spreadsheet', 'xlsx'),
    'pptx': ('slides', 'pptx'),
    'epub': ('book', 'epub'),
    'png':  ('image', 'png'),
    'jpg':  ('image', 'jpg'),
    'jpeg': ('image', 'jpg'),
}


def detect_type(url: str, timeout: int = 10) -> dict:
    """Resolve a URL to {source_type, ext, mime, filename}.

    Tries HEAD first (reads Content-Type + Content-Disposition). Falls
    back to URL-suffix parsing if HEAD fails or returns a generic type.
    Never raises — returns 'webpage'/'html' as the catch-all default.
    """
    out = {
        'source_type': 'webpage',
        'ext': 'html',
        'mime': None,
        'filename': None,
    }

    # Platform-specific URL patterns short-circuit all the detection —
    # we want GitHub/arxiv/youtube to go through their bespoke flows.
    if re.search(r'github\.com/[^/]+/[^/]+', url, re.IGNORECASE):
        out.update({'source_type': 'repo', 'ext': None, 'mime': None})
        return out
    if re.search(r'(youtube\.com|youtu\.be)', url, re.IGNORECASE):
        out.update({'source_type': 'video', 'ext': None, 'mime': None})
        return out
    if re.search(r'arxiv\.org/abs/', url, re.IGNORECASE):
        out.update({'source_type': 'paper', 'ext': 'pdf', 'mime': 'application/pdf'})
        return out
    # Google Drive `/file/d/<id>/view` URLs are PDFs (or other files)
    # served through a viewer UI — HEAD on the view URL returns HTML,
    # so Content-Type detection would miss them. We assume PDF since
    # that's the realistic case in Athena's ingest workflow; a non-PDF
    # Drive file will still get caught by magic-byte validation during
    # download (ingest-file discards non-PDF content).
    if re.search(r'drive\.google\.com/file/d/[^/]+', url, re.IGNORECASE):
        out.update({'source_type': 'paper', 'ext': 'pdf', 'mime': 'application/pdf'})
        return out

    # Try HEAD request. Some servers don't support HEAD, others respond
    # with wrong headers — we accept whatever we can parse.
    try:
        req = urllib.request.Request(
            url, method='HEAD',
            headers={'User-Agent': 'Athena/1.0 (content detector)'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            mime = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            disp = resp.headers.get('Content-Disposition') or ''
            if mime:
                out['mime'] = mime
                if mime in _MIME_MAP:
                    out['source_type'], out['ext'] = _MIME_MAP[mime]
            # Parse filename from Content-Disposition if present — this
            # is the server's own suggested filename, usually more
            # meaningful than the URL path.
            fn_match = re.search(r'filename\*?=(?:[^\'"]*\'\')?"?([^";]+)"?', disp)
            if fn_match:
                out['filename'] = urllib.parse.unquote(fn_match.group(1).strip())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        pass  # HEAD failed — fall through to URL-suffix detection

    # URL-suffix fallback: runs if HEAD didn't produce a known mapping.
    # The "generic" Content-Types ("application/octet-stream", etc.) also
    # fall here — we trust the URL over a vague server response.
    if out['source_type'] == 'webpage' and out['ext'] == 'html':
        path = urllib.parse.urlparse(url).path or ''
        ext_match = re.search(r'\.([a-z0-9]+)(?:$|\?)', path, re.IGNORECASE)
        if ext_match:
            ext = ext_match.group(1).lower()
            if ext in _EXT_MAP:
                out['source_type'], out['ext'] = _EXT_MAP[ext]
                if not out['mime']:
                    # Best-guess MIME from ext — not authoritative, but
                    # useful if downstream code keys on mime.
                    rev = {v: k for k, v in _MIME_MAP.items()}
                    out['mime'] = rev.get((out['source_type'], out['ext']))

    return out
