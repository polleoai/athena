"""Favicon cache for Athena wiki pages.

Each ingested URL gets a domain-keyed favicon saved under
`raw/favicons/<domain>.png`. The wiki template embeds it inline next to
the Source link. Falls back to a per-domain emoji when the favicon can't
be fetched.

Fetched via Google's s2 proxy which returns a normalized 64x64 PNG for
every domain (including ones that have no favicon of their own or serve
malformed .ico). One HTTP call, always a valid PNG.

Public API:
    normalize_domain(url)  -> str | None
    ensure_favicon(url, vault_path) -> str | None
        Returns vault-relative path "raw/favicons/<domain>.png" on
        success, None on any failure (caller should use domain_emoji).
    domain_emoji(url) -> str
        Always returns an emoji; unknown domains get the generic globe.
"""

import os
import re
import urllib.parse
import urllib.request
import urllib.error


FAVICON_DIR = 'raw/favicons'
GOOGLE_FAVICON = 'https://www.google.com/s2/favicons?domain={domain}&sz=64'
FETCH_TIMEOUT_SECS = 10
MAX_BYTES = 50 * 1024
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'

# Domain -> emoji for the fallback path. Keys match the output of
# normalize_domain (lowercase, no www, no port). Subdomains are
# looked up exactly; if unknown, we check the registrable domain
# (last two labels) before falling back to the generic globe.
_DOMAIN_EMOJI = {
    'x.com': '𝕏',
    'twitter.com': '𝕏',
    'linkedin.com': '💼',
    'github.com': '🐱',      # Octocat — the actual GitHub favicon is a cat
    'gist.github.com': '🐱',
    'youtube.com': '📺',
    'youtu.be': '📺',
    'arxiv.org': '📄',
    'medium.com': 'Ⓜ️',
    'substack.com': '📰',
    'news.ycombinator.com': '🟠',
    'stackoverflow.com': '📚',
    'reddit.com': '👽',
    'wikipedia.org': '📖',
    'en.wikipedia.org': '📖',
}
_FALLBACK_EMOJI = '🌐'

# Fallback by Athena source_type when domain_emoji is generic. Covers
# videos from non-youtube hosts, papers not on arxiv, etc. Order matters
# in the resolver: domain > source_type > generic globe.
_SOURCE_TYPE_EMOJI = {
    'paper': '📄',
    'repo': '🐱',      # best guess — most repos are github
    'webpage': '🌐',
    'video': '📺',
    'image': '🖼️',
}


def source_type_emoji(source_type):
    """Best-effort emoji for an Athena source_type. Returns generic
    globe for unknown types so callers don't need to null-check."""
    return _SOURCE_TYPE_EMOJI.get(source_type, _FALLBACK_EMOJI)


def normalize_domain(url):
    """Extract a canonical domain key from a URL.

    - Lowercase hostname (URLs are case-insensitive for host).
    - Strip leading "www." so `www.x.com` and `x.com` share a cache entry.
    - Strip port and trailing dot.
    - Return None for URLs we can't parse (missing scheme, bad input).
    """
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or '').lower()
    if not host:
        return None
    host = re.sub(r'\.$', '', host)      # trailing dot
    if host.startswith('www.'):
        host = host[4:]
    # Basic sanity check: at least one dot, no whitespace, no path chars.
    if '.' not in host or re.search(r'[\s/\\]', host):
        return None
    return host


def domain_emoji(url):
    """Return a fallback emoji for a URL's domain. Never raises."""
    domain = normalize_domain(url)
    if not domain:
        return _FALLBACK_EMOJI
    if domain in _DOMAIN_EMOJI:
        return _DOMAIN_EMOJI[domain]
    # Try registrable domain (last two labels) for subdomains we didn't list.
    # e.g. "docs.github.com" -> "github.com". Handles most common cases.
    parts = domain.split('.')
    if len(parts) > 2:
        reg = '.'.join(parts[-2:])
        if reg in _DOMAIN_EMOJI:
            return _DOMAIN_EMOJI[reg]
    return _FALLBACK_EMOJI


def _is_valid_png(data):
    """Reject anything that isn't a real PNG. Google's proxy always
    returns PNG; if we get something else, something went wrong upstream
    and we'd rather show the emoji fallback than embed garbage."""
    return isinstance(data, (bytes, bytearray)) and data[:8] == PNG_MAGIC


def _fetch_favicon_bytes(domain):
    """Fetch the favicon via Google's s2 proxy. Returns bytes on success,
    None on any failure (timeout, non-200, non-PNG, too-large, etc.).
    Intentionally silent — caller falls back to emoji and keeps going."""
    try:
        req = urllib.request.Request(
            GOOGLE_FAVICON.format(domain=urllib.parse.quote(domain)),
            headers={'User-Agent': 'Athena/1.0 (favicon cache)'},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECS) as resp:
            if resp.status != 200:
                return None
            data = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return None  # suspicious size — bail rather than embed
            if not _is_valid_png(data):
                return None
            return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def ensure_favicon(url, vault_path):
    """Return a vault-relative path to a cached favicon PNG, or None.

    - Cache key is the normalized domain; many pages from one site share
      one PNG on disk.
    - File existence = cache hit; we never refetch (favicons change
      rarely, and stale is cheap). Users can force refresh by deleting
      the cached file and re-ingesting.
    - On any fetch failure the caller falls back to domain_emoji.
    """
    domain = normalize_domain(url)
    if not domain:
        return None

    rel_path = f'{FAVICON_DIR}/{domain}.png'
    abs_path = os.path.join(vault_path, rel_path)
    if os.path.exists(abs_path) and os.path.getsize(abs_path) > 0:
        return rel_path

    data = _fetch_favicon_bytes(domain)
    if data is None:
        return None

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp_path = abs_path + '.tmp'
    try:
        with open(tmp_path, 'wb') as f:
            f.write(data)
        os.replace(tmp_path, abs_path)  # atomic
    except OSError:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return None
    return rel_path
