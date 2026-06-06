"""Detect canonical sources (arXiv, DOI, GitHub, Substack, Medium) referenced
in a captured page body, so the ingest pipeline can pull them in without
manual intervention.

Three tiers, used in order of escalating cost:
  1. `extract_canonical_urls(text)` — pure regex; handles 80%+ of academic
     posts because LinkedIn/X include OCR'd alt-text in their HTML.
  2. `extract_via_ocr(image_paths)` — tesseract over localized carousel
     images. Used when tier 1 finds nothing in the captured text. Skipped
     gracefully if tesseract isn't installed.
  3. `extract_via_vision(image_paths)` — Claude Vision over the first
     image. Used when 1+2 both miss. Most accurate, costs an LLM call.

All three return canonical URLs in normalized form so dedup is string equality.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# ── Tier 1: regex over captured text ───────────────────────────────────

# Direct arxiv.org URLs (canonical form).
_ARXIV_URL_RE = re.compile(
    r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?',
    re.IGNORECASE,
)
# `arXiv:2509.22040` form embedded in paper headers.
_ARXIV_LABEL_RE = re.compile(
    r'\barXiv\s*:\s*(\d{4}\.\d{4,5})\b',
    re.IGNORECASE,
)
# PDF-text-extraction artifact: letters and digits separated by spaces
# because PDF→text loses kerning. Saw this in the LinkedIn capture:
# `ar X iv :2 50 9. 22 04 0v 2 [ cs .C R ]`. The pattern matches
# `ar X iv` + any separators + 4-5 digits + `.` + 4-5 digits, where the
# digits themselves may have spaces between them.
_ARXIV_MANGLED_RE = re.compile(
    r'ar\s*X\s*iv[^a-z0-9\n]{0,10}((?:\d\s*){4,5})\s*\.\s*((?:\d\s*){4,5})',
    re.IGNORECASE,
)
# DOI — covers ACM, IEEE, Nature, etc. Not all DOIs are followable
# without auth, but the doi.org redirect is the right canonical anchor.
_DOI_RE = re.compile(
    r'\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+',
)
# Substack — author posts often share blog versions of papers.
_SUBSTACK_RE = re.compile(
    r'https?://[a-z0-9-]+\.substack\.com/p/[a-z0-9-]+',
    re.IGNORECASE,
)
# Medium — same rationale as Substack.
_MEDIUM_RE = re.compile(
    r'https?://(?:[a-z0-9-]+\.)?medium\.com/(?:@[a-z0-9-]+/)?[a-z0-9-]+(?:-[a-f0-9]+)?',
    re.IGNORECASE,
)
# X / Twitter status URLs — a link-share tweet often points to ANOTHER tweet
# that holds the real content (the Atai→Saboo case). Treat the destination
# tweet as a discoverable canonical source so the ingest pipeline + lint
# cross-link machinery surface and link it. Match only `<handle>/status/<id>`
# (handles are 1–15 chars of [A-Za-z0-9_]); `i` is a reserved x.com path
# (x.com/i/article, x.com/i/web/...), never a real handle, so exclude it.
_X_STATUS_RE = re.compile(
    r'https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)',
    re.IGNORECASE,
)
# GitHub repos at top level (skip deep links to /blob/, /tree/, /pull/, /issues/
# — those are sub-pages, not the canonical repo).
_GITHUB_REPO_RE = re.compile(
    r'https?://github\.com/([a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?/[a-z0-9_.-]+)'
    # `…` (U+2026) is X.com's truncation indicator on shortened URLs —
    # without it in the terminator class, every X post that references
    # a GitHub repo was silently missed by canonical-source discovery.
    r'(?=$|[\s/?#"\)<>…](?!blob|tree|pull|issues|wiki|releases))',
    re.IGNORECASE,
)

# Tutorial/example placeholders that look like real repos but aren't.
# Either path component (owner OR repo) matching one of these = placeholder.
# Kept as a frozenset for cheap membership testing; comparison is lowercase.
_PLACEHOLDER_TOKENS = frozenset({
    'my-org', 'my-username', 'my-user', 'my-account', 'my-repo', 'my-project',
    'your-org', 'your-username', 'your-user', 'your-account', 'your-repo', 'your-project',
    'org', 'user', 'username', 'repo', 'project',
    'orgname', 'reponame', 'projectname',
    'example', 'example-org', 'example-repo',
    'foo', 'bar', 'baz', 'foo-bar',
    'placeholder', 'sample',
})

def _is_placeholder_repo(owner: str, repo: str) -> bool:
    """True iff owner OR repo is a known tutorial/placeholder token.
    Catches the my-org/my-repo, your-username/repo, example/example
    family that tutorials use but aren't real fetchable URLs."""
    return owner in _PLACEHOLDER_TOKENS or repo in _PLACEHOLDER_TOKENS
# Direct PDF URLs hosted anywhere — the Riazi case (Terence Tao's Measure
# Theory book on terrytao.wordpress.com) is the canonical example: not
# arXiv, not a DOI, just a static PDF on a personal site. Capture the
# URL and let kb add / kb-capture route it to the paper handler via
# Content-Type detection. Stop at the next whitespace, quote, paren,
# bracket, or trailing ellipsis (`…` is U+2026 — common on truncated X
# links). Skip arxiv.org PDFs (already covered by _ARXIV_URL_RE) and
# image hosts like pbs.twimg.com that masquerade as PDFs sometimes.
_PDF_URL_RE = re.compile(
    r'https?://(?!(?:arxiv\.org|pbs\.twimg\.com|media\.licdn\.com))'
    r'[^\s"\'\)\]<>]+?\.pdf(?:[?#][^\s"\'\)\]<>…]*)?',
    re.IGNORECASE,
)


def _normalize_arxiv(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def _normalize_doi(doi: str) -> str:
    # Strip trailing punctuation that DOI capture sometimes catches.
    return f"https://doi.org/{doi.rstrip('.,;:)')}"


def _normalize_pdf_host(url: str) -> str:
    """Transform PDF URLs on hosts that serve HTML wrappers (HuggingFace
    blob views, GitHub blob views) to their raw-binary equivalents so
    the kb-capture paper handler actually receives a PDF.

    Without this, the wrapper page gets fetched as text/html and routed
    to the webpage handler — producing a junk wiki page about the file
    listing instead of the actual paper. Discovered via the DeepSeek-V4
    HuggingFace incident: `huggingface.co/.../blob/main/X.pdf` returned
    HTML, generating two wiki pages from one wrapper.
    """
    # HuggingFace: /blob/<branch>/<file> → /resolve/<branch>/<file>
    m = re.match(r'(https?://huggingface\.co/[^/]+/[^/]+)/blob/(.+)', url, re.IGNORECASE)
    if m:
        return f"{m.group(1)}/resolve/{m.group(2)}"
    # GitHub: github.com/<u>/<r>/blob/<branch>/<file> → raw.githubusercontent.com/<u>/<r>/<branch>/<file>
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/(.+)', url, re.IGNORECASE)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return url


def _reconstruct_mangled_arxiv_id(part1: str, part2: str) -> str | None:
    """Strip whitespace from PDF-mangled digits and validate the result
    matches arXiv's NNNN.NNNNN format.
    """
    a = re.sub(r'\s+', '', part1)
    b = re.sub(r'\s+', '', part2)
    candidate = f"{a}.{b}"
    if re.match(r'^\d{4}\.\d{4,5}$', candidate):
        return candidate
    return None


def extract_canonical_urls(text: str) -> list[str]:
    """Return canonical-source URLs found in the text, deduplicated.

    Order is the order of first occurrence; arXiv URLs come first so the
    auto-queue prioritizes the highest-value source.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for m in _ARXIV_URL_RE.finditer(text):
        _add(_normalize_arxiv(m.group(1)))
    for m in _ARXIV_LABEL_RE.finditer(text):
        _add(_normalize_arxiv(m.group(1)))
    for m in _ARXIV_MANGLED_RE.finditer(text):
        candidate = _reconstruct_mangled_arxiv_id(m.group(1), m.group(2))
        if candidate:
            _add(_normalize_arxiv(candidate))
    for m in _DOI_RE.finditer(text):
        doi = m.group(0)
        # arXiv has a DOI alias `10.48550/arXiv.<id>` that resolves to the
        # same paper as `arxiv.org/abs/<id>`. Recognize this and emit the
        # arXiv URL form instead of the DOI — otherwise the same paper
        # gets queued twice and ingested as two separate wiki pages
        # (the AARM/Errico paper case from this session).
        arxiv_alias = re.match(r'10\.48550/arXiv\.(\d{4}\.\d{4,5})', doi, re.IGNORECASE)
        if arxiv_alias:
            _add(_normalize_arxiv(arxiv_alias.group(1)))
        else:
            _add(_normalize_doi(doi))
    for m in _GITHUB_REPO_RE.finditer(text):
        repo = m.group(1).rstrip('/').rstrip('.')
        # Skip false positives like `github.com/in/`, `/about`, etc.
        if '/' not in repo or repo.count('/') != 1:
            continue
        # Skip tutorial/example placeholder repos — these aren't real
        # and fetching them produces "Page not found" wiki pages that
        # pollute the auto-discovery section. Owner OR repo containing
        # one of these tokens is a placeholder, drop it.
        owner, repo_name = repo.lower().split('/')
        if _is_placeholder_repo(owner, repo_name):
            continue
        _add(f"https://github.com/{repo}")
    for m in _X_STATUS_RE.finditer(text):
        handle = m.group(1)
        if handle.lower() == 'i':
            continue  # reserved x.com path, not a user handle
        _add(f"https://x.com/{handle}/status/{m.group(2)}")
    for m in _SUBSTACK_RE.finditer(text):
        _add(m.group(0))
    for m in _MEDIUM_RE.finditer(text):
        _add(m.group(0))
    for m in _PDF_URL_RE.finditer(text):
        # Strip trailing ellipsis (U+2026) that LinkedIn/X shows on
        # truncated links — common on social posts.
        url = m.group(0).rstrip('…').rstrip('.')
        # Transform HTML-wrapper hosts (HuggingFace blob, GitHub blob) to
        # their raw-PDF equivalents so the paper handler gets bytes, not HTML.
        _add(_normalize_pdf_host(url))

    return urls


def queue_canonical_urls(
    vault_root: str | Path,
    source_url: str,
    canonical_urls: list[str],
) -> list[str]:
    """Append canonical URLs to <vault>/inbox/url-new.txt for the next
    auto-ingest cycle. Returns the URLs actually queued (filters dups
    + self-references).

    Idempotent: re-queuing the same URL is a no-op.
    """
    if not canonical_urls:
        return []
    vault_root = Path(vault_root)
    inbox = vault_root / 'inbox' / 'url-new.txt'
    inbox.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if inbox.exists():
        with open(inbox, 'r', encoding='utf-8') as f:
            existing = {line.strip() for line in f if line.strip()}
    # Also dedupe against url-resolved.tsv so we don't re-queue something
    # that was already captured.
    resolved_tsv = vault_root / 'inbox' / 'url-resolved.tsv'
    if resolved_tsv.exists():
        try:
            with open(resolved_tsv, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        existing.add(parts[2].strip())
        except (IOError, UnicodeDecodeError):
            pass

    queued: list[str] = []
    with open(inbox, 'a', encoding='utf-8') as f:
        for url in canonical_urls:
            if url == source_url:
                continue
            if url in existing:
                continue
            f.write(url + '\n')
            existing.add(url)
            queued.append(url)
    return queued


# ── Tier 2: tesseract OCR fallback ────────────────────────────────────


def extract_via_ocr(image_paths: list[Path]) -> list[str]:
    """Run tesseract over the given images; pipe the OCR output through
    `extract_canonical_urls`.

    Returns [] if tesseract isn't installed (graceful skip — the caller
    can decide whether to escalate to Tier 3).
    """
    try:
        subprocess.run(['tesseract', '--version'],
                       capture_output=True, timeout=5, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []

    aggregated_text = []
    # Only try the first 3 images; carousel paper-IDs almost always live
    # on the title page. Avoid running tesseract on every page.
    for img in image_paths[:3]:
        if not img.exists():
            continue
        try:
            result = subprocess.run(
                ['tesseract', str(img), '-', '--psm', '6'],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                aggregated_text.append(result.stdout)
        except subprocess.SubprocessError:
            continue
    if not aggregated_text:
        return []
    return extract_canonical_urls('\n'.join(aggregated_text))


# ── Tier 3: Claude Vision fallback ────────────────────────────────────


_VISION_PROMPT = (
    "Look at this image of a paper or document. Extract any of the "
    "following identifiers visible in the text: arXiv ID (format "
    "NNNN.NNNNN), DOI (format 10.NNNN/...), or a clear paper title with "
    "first author. Output ONLY the identifier(s) on separate lines, "
    "no preamble, no explanation. If none visible, output 'none'."
)


def extract_via_vision(image_paths: list[Path]) -> list[str]:
    """Send the first image to `claude -p` with vision, ask for canonical
    identifiers, parse the result through `extract_canonical_urls`.

    Skipped if `claude` isn't on PATH or no images are localized.
    """
    if not image_paths:
        return []
    first = image_paths[0]
    if not first.exists():
        return []
    try:
        subprocess.run(['claude', '--version'],
                       capture_output=True, timeout=5, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    try:
        result = subprocess.run(
            [
                'claude', '-p',
                '--disable-slash-commands',
                '--model', 'claude-haiku-4-5',  # Vision-capable, cheap
                f'<image>{first}</image>\n\n{_VISION_PROMPT}',
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return []
        out = (result.stdout or '').strip()
        if not out or out.lower() == 'none':
            return []
        return extract_canonical_urls(out)
    except subprocess.SubprocessError:
        return []


# ── Combined entry point ──────────────────────────────────────────────


def discover_canonical_sources(
    body_text: str,
    image_paths: list[Path] | None = None,
    use_ocr: bool = True,
    use_vision: bool = False,
) -> tuple[list[str], str]:
    """Run all three tiers in escalation order.

    Returns (canonical_urls, tier_used) where tier_used is one of
    "regex" / "ocr" / "vision" / "none". Tier 3 (vision) defaults to
    OFF because it costs an API call per attempt — opt-in via
    use_vision=True or athena.default.json `canonical_sources.vision_enabled`.
    """
    urls = extract_canonical_urls(body_text)
    if urls:
        return urls, "regex"
    if use_ocr and image_paths:
        urls = extract_via_ocr(image_paths)
        if urls:
            return urls, "ocr"
    if use_vision and image_paths:
        urls = extract_via_vision(image_paths)
        if urls:
            return urls, "vision"
    return [], "none"
