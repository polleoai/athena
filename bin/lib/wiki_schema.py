"""
wiki_schema.py — Canonical writers for raw and wiki pages.

Why this module exists:
  Before this, ~7 separate code paths wrote raw page files and ~13 places
  derived slugs from URLs/titles. Each had its own subtle rules: some
  lowercased, some didn't; some used `re.sub(r'[^\\w\\s-]', '')`, others
  added `.replace(' ', '-')`; trailing-period stems slipped through some
  paths and not others. Result: the same URL captured via two different
  flows produced two different files (different slugs, different
  frontmatter shape, sometimes one with H1 and one without), surfacing as
  duplicate-URL findings, phantom wikilinks, and oscillating lint output.

  This module is the single canonical home for:
    * make_slug(text)              — one slug rule
    * RAW_PAGE_REQUIRED_FIELDS     — frontmatter schema
    * write_raw_page(...)          — schema-validating raw writer
    * WIKI_PAGE_REQUIRED_FIELDS    — wiki page schema
    * read_frontmatter(path)       — schema-aware reader

Design rule:
  No code outside this module should construct a slug, write a raw page,
  or hand-format raw frontmatter. If you're tempted, add a method here
  instead. New ingest paths must go through write_raw_page().
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Iterable

# ── Slug ───────────────────────────────────────────────────────────────

# A slug is the lowercase, alphanumeric-and-hyphen, length-capped form of
# a piece of text used as a filename stem. Stable across operating systems
# (no spaces, no quirky chars), Obsidian-friendly (no characters Obsidian
# treats as wikilink syntax), and round-trippable (idempotent — slugifying
# a slug yields the same slug).
_SLUG_MAX_LEN = 60


def unescape_yaml_string(s: str) -> str:
    """Reverse YAML double-quoted-string escape sequences in a captured value.

    Naive frontmatter parsers (regex-based, like ours) extract the content
    *between* the outer quotes verbatim — they don't process YAML's `\\"`
    or `\\\\` escapes. Pasting that captured value somewhere unquoted (an
    H1, a body line) leaks the escape backslashes as literal characters.

    Caller pattern: read frontmatter → unescape → use in body. Re-writing
    to YAML re-applies escapes via _escape_double_quotes.
    """
    if not s:
        return s
    return s.replace('\\"', '"').replace("\\\\", "\\")


def make_slug(text: str, max_len: int = _SLUG_MAX_LEN) -> str:
    """The one canonical slug function. All ingest paths must use this.

    Rules (in order):
      1. Lowercase
      2. Replace anything not [a-z0-9] with '-'
      3. Collapse runs of '-'
      4. Strip leading/trailing '-' and '.'   ← trailing '.' caused the
         `Code..md` bug that broke extract_frontmatter intermittently
      5. Truncate to max_len, then re-strip trailing '-'/'.'
    """
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-.")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-.")
    return s


# ── Schemas ────────────────────────────────────────────────────────────

# A raw clipping/webpage MUST have these fields after writing. Lint will
# auto-fix missing ones where possible (e.g., insert H1 from frontmatter
# title), but the writer should produce conforming pages from the start.
RAW_PAGE_REQUIRED_FIELDS = ("title", "source", "captured_at")

# A wiki page MUST have these fields. Used by lint check
# "Suspect non-Athena pages" and the post-capture validator.
WIKI_PAGE_REQUIRED_FIELDS = (
    "title",
    "source_type",
    "date_added",
    "tags",
    "summary",
)


class SchemaError(ValueError):
    """Raised when a write would produce a schema-violating page.

    Catching this at write-time prevents bad data from landing on disk —
    the entire premise of moving consistency from lint (post-hoc cleanup)
    to writers (pre-condition enforcement).
    """


def _today_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Raw page writer ────────────────────────────────────────────────────


def raw_artifacts_dir(vault: Path, source_type: str) -> Path:
    """Resolve raw/<source_type>s/artifacts/ — single source of truth.

    Reads bin/config/athena.default.json via the existing config loader so
    layout changes happen in config, not scattered across writers.
    """
    import sys
    sys.path.insert(0, str(Path(vault) / "bin" / "lib"))
    from config import raw_dir_for_source_type  # noqa: E402

    rel = raw_dir_for_source_type(source_type)
    if not rel:
        raise SchemaError(f"Unknown source_type for raw write: {source_type!r}")
    return Path(vault) / rel


def write_raw_page(
    *,
    vault: Path,
    source_type: str,
    url: str,
    title: str,
    body: str,
    slug_override: str | None = None,
    extra_frontmatter: dict | None = None,
) -> Path:
    """Write a raw page atomically with schema validation.

    Phase 3a delegates to bin/lib/raw_writer.write_raw — the typed write
    API introduced in Phase 2. This function preserves the legacy
    (vault, source_type, url, ...) signature so existing callers don't
    have to change. The behavior gains over the pre-Phase-3 implementation:

      * URL is canonicalized at write time (tracking params stripped,
        HF /blob/→/resolve/, DOI→arxiv alias collapsed). Two captures of
        the same X tweet with different ?s=12&t=... produce ONE raw
        instead of two duplicates (kills lint #38 source).
      * Slug derivation enforces per-category policy: collision-bait
        titles (Untitled, Tweet, Post-Linkedin) are rejected, never
        silently aliased onto existing files (kills lint #39 source).
      * Schema validation of the assembled frontmatter via Pydantic —
        bad data raises before any FS write.

    The legacy `url` requirement (must be non-empty) is preserved to
    keep the caller contract; raw_writer accepts None for book-style
    captures, but write_raw_page callers always supply a URL.
    """
    if not url or not url.strip():
        raise SchemaError("write_raw_page: url is required")

    # Wrap raw_writer.RawWriterError as SchemaError for caller compat.
    # Translate the source_type variations the legacy callers use:
    # "papers" / "paper" / "webpages" / "webpage" → singular form.
    st = source_type.rstrip("s") if source_type.endswith("s") else source_type
    if st not in ("paper", "repo", "webpage", "video", "image", "book"):
        raise SchemaError(
            f"write_raw_page: source_type={source_type!r} not recognized"
        )

    try:
        from raw_writer import RawWriterError, write_raw
    except ImportError as exc:
        raise SchemaError(f"raw_writer module unavailable: {exc}") from exc

    try:
        return write_raw(
            vault_root=vault,
            source_type=st,
            url=url,
            title=title,
            body=body,
            slug_override=slug_override,
            extra=extra_frontmatter,
        )
    except RawWriterError as exc:
        raise SchemaError(f"write_raw_page: {exc}") from exc


def _url_to_slug_input(url: str) -> str:
    """Strip protocol/www and use the path+host as slug input.

    Query parameters are stripped — `?s=12&t=...` was the difference that
    made the same X.com tweet captured twice produce two different slugs
    and become a duplicate pair. Stripping query params at slug time is
    one half of the dedup story (lint #4 is the other half — duplicate-URL
    detection that compares canonicalized URLs).
    """
    import urllib.parse

    p = urllib.parse.urlparse(url)
    host = (p.netloc or "").removeprefix("www.")
    path = p.path.rstrip("/")
    # Deliberately drop p.query and p.fragment — they cause same-tweet-different-slug.
    return f"{host}{path}".replace("/", "-")


def _escape_double_quotes(s: str) -> str:
    return s.replace('"', '\\"')


def _format_fm_field(key: str, value) -> str:
    """Render one frontmatter field. Lists become YAML-list form."""
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        lines = [f"{key}:"]
        for item in value:
            lines.append(f'  - "{_escape_double_quotes(str(item))}"')
        return "\n".join(lines)
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    return f'{key}: "{_escape_double_quotes(str(value))}"'


# ── Frontmatter reader ─────────────────────────────────────────────────


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_frontmatter(path: Path) -> tuple[dict, str]:
    """Return ({field: value}, body_text). Tolerates list-style fields.

    Used by lint and downstream readers; prefer this over inline parsing
    so frontmatter quirks (quoting, list forms) are handled in one place.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return {}, ""
    m = _FM_RE.match(text)
    if not m:
        return {}, text

    fm: dict = {}
    cur_key: str | None = None

    def _strip_one_quote_pair(v: str) -> str:
        """Strip exactly one matched quote pair, never more.

        `.strip('"')` is greedy and eats every trailing `"`, which corrupts
        YAML-escaped values like `"foo \"bar\""` (loses the closing quote
        of the inner-escaped pair). Strip exactly one matched pair.
        """
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            return v[1:-1]
        return v

    for line in m.group(1).split("\n"):
        if re.match(r"^\w[\w_-]*:\s*$", line):
            cur_key = line.split(":", 1)[0].strip()
            fm[cur_key] = []
            continue
        if line.startswith("  - ") and cur_key is not None:
            v = _strip_one_quote_pair(line[4:])
            fm[cur_key].append(v)
            continue
        m2 = re.match(r"^([\w_-]+)\s*:\s*(.*)$", line)
        if m2:
            cur_key = m2.group(1).strip()
            value = _strip_one_quote_pair(m2.group(2))
            # Recognize YAML inline-list form: `tags: [a, b, c]` should
            # parse as a Python list, not a string. Pre-fix, schemas.py
            # rejected these as "Input should be a valid list" — but the
            # data was correct, only the parser was wrong.
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if inner:
                    fm[cur_key] = [
                        _strip_one_quote_pair(item.strip())
                        for item in inner.split(",")
                    ]
                else:
                    fm[cur_key] = []
            else:
                fm[cur_key] = value
    return fm, text[m.end():]


def get_raw_source_url(fm: dict) -> str:
    """Get the source URL from a parsed raw frontmatter dict.

    Canonical raws (written by `write_raw_page`) emit `source: "https://..."`.
    Legacy raws on disk still use `url:`. ALL readers of raw frontmatter
    must use this helper instead of `fm.get('url')` directly — that's the
    silent regression that caused #115 (every canonical raw became
    URL-invisible to dedup, mismatch detection, and orphan-synth).

    Returns '' if neither field is present. Strips whitespace and
    surrounding quotes — frontmatter parsers vary on whether quotes are
    preserved.
    """
    v = fm.get("source") or fm.get("url") or ""
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v).strip().strip('"').strip("'")


def is_redirect_stub(fm: dict) -> bool:
    """Whether a frontmatter dict marks the page as a rename forwarder."""
    v = fm.get("redirect", "")
    if isinstance(v, bool):
        return v
    return str(v).strip().strip('"').strip("'").lower() == "true"


def is_merged_loser(fm: dict) -> bool:
    """Whether a raw page is the trashed-loser side of a kb merge.

    Orphan-raw synth must skip these — otherwise it recreates a wiki
    page for content that was just merged elsewhere, producing the
    `_Contents.md` oscillation we spent half a session debugging.
    """
    v = fm.get("merged_into", "")
    return bool(v) and str(v).strip().strip('"').strip("'") not in ("", "null", "None")


def required_fields_missing(fm: dict, schema: Iterable[str]) -> list[str]:
    """Return list of schema fields absent or empty in fm."""
    return [k for k in schema if not fm.get(k)]


# ── Wiki page writer ───────────────────────────────────────────────────


def wiki_format_dir(vault: Path, source_type: str) -> Path:
    """Resolve wiki/format/<sources> for a given source_type — single
    source of truth for "where does this kind of wiki page live?". Reads
    the same config that raw_artifacts_dir uses."""
    import sys as _sys
    _sys.path.insert(0, str(Path(vault) / "bin" / "lib"))
    from config import wiki_format_dir as _cfg_wfd  # noqa: E402

    rel = _cfg_wfd(_normalize_source_type_to_category(source_type))
    if not rel:
        raise SchemaError(f"Unknown source_type for wiki write: {source_type!r}")
    return Path(vault) / rel


def _normalize_source_type_to_category(source_type: str) -> str:
    """Source type → wiki/format subdir name (papers, repos, webpages, …).

    Single mapping so callers can pass either form.
    """
    mapping = {
        "paper": "papers", "papers": "papers",
        "repo": "repos", "repos": "repos",
        "webpage": "webpages", "webpages": "webpages",
        "video": "videos", "videos": "videos",
        "image": "images", "images": "images",
        "entity": "entities", "entities": "entities",
    }
    return mapping.get(source_type, source_type)


def _sanitize_wiki_title_for_filename(title: str) -> str:
    """Strip path-traversal/wikilink-syntax chars from a title to produce
    a filesystem-safe filename stem. Different from make_slug — wiki
    titles preserve human readability, just remove characters Obsidian
    can't handle in filenames.

    Colon is INTENTIONALLY preserved (#131) — it is the canonical source-
    prefix separator (`Web: <title>`, `GitHub: <repo>`, `PDF: <title>`,
    `X: <topic>`, etc.). Stripping it produced filename / frontmatter
    drift: title field had `Web: Foo` while the filename had `Web Foo`.
    macOS APFS/HFS+ stores colons natively (Finder substitutes `/` for
    display only); Linux filesystems accept colons; Obsidian wikilinks
    handle them fine. If Windows compat becomes a concern later, switch
    to a per-platform replacement strategy (em-dash on Windows) rather
    than unconditional stripping.
    """
    if not title:
        raise SchemaError("Wiki title cannot be empty")
    cleaned = title.replace("/", "-").replace("\\", "-").replace("|", "—")
    cleaned = re.sub(r'[*?"<>]', "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = cleaned.strip(".").strip()  # trailing dots/spaces — phantom-file source
    if not cleaned:
        raise SchemaError(f"Wiki title sanitizes to empty: {title!r}")
    return cleaned[:120]  # reasonable filesystem limit


def validate_wiki_frontmatter(text: str, *, allow_redirect_stub: bool = True) -> dict:
    """Parse and validate a wiki page's frontmatter against the schema.

    Returns the parsed frontmatter dict. Raises SchemaError if required
    fields are missing. Use this when a caller already has prebuilt
    wiki-page text (e.g., merge code that combines multiple pages) so it
    still goes through one validation gate.

    Redirect stubs (`redirect: true`) are exempt from the standard
    schema — they are deliberately minimal forwarders.
    """
    m = _FM_RE.match(text)
    if not m:
        raise SchemaError("Wiki page has no frontmatter")
    fm: dict = {}
    cur_key: str | None = None
    for line in m.group(1).split("\n"):
        if re.match(r"^\w[\w_-]*:\s*$", line):
            cur_key = line.split(":", 1)[0].strip()
            fm[cur_key] = []
            continue
        if line.startswith("  - ") and cur_key is not None:
            fm[cur_key].append(line[4:].strip().strip('"').strip("'"))
            continue
        m2 = re.match(r"^([\w_-]+)\s*:\s*(.*)$", line)
        if m2:
            cur_key = m2.group(1).strip()
            fm[cur_key] = m2.group(2).strip().strip('"').strip("'")
    if allow_redirect_stub and is_redirect_stub(fm):
        return fm
    missing = required_fields_missing(fm, WIKI_PAGE_REQUIRED_FIELDS)
    if missing:
        raise SchemaError(f"Wiki frontmatter missing required fields: {missing}")
    return fm


def write_wiki_page(
    *,
    vault: Path,
    source_type: str,
    title: str,
    summary: str,
    body: str,
    tags: list[str] | None = None,
    related: list[str] | None = None,
    raw_path: str | None = None,
    raw_paths: list[str] | None = None,
    url: str | None = None,
    urls: list[str] | None = None,
    date_added: str | None = None,
    extra_frontmatter: dict | None = None,
    overwrite: bool = False,
) -> Path:
    """Write a wiki page atomically with schema validation.

    Phase 3b delegates to bin/lib/wiki_writer.write_wiki — the typed
    write API introduced in Phase 2. Caller API unchanged. Behavior gains:
      * URL canonicalized at write time
      * Schema validation per-shape (standard/merged/synthesis) via
        Pydantic — lint #46 (invalid YAML) bug class is impossible
      * Leaked-frontmatter stripping at body boundary — lint #48 source
        on the wiki side is impossible
      * raw_path enforced under raw/<cat>/artifacts/ — lint #49 wiki-side
        bug class is impossible
    """
    if not tags:
        tags = [_normalize_source_type_to_category(source_type).rstrip("s")]

    # Decide WikiShape from inputs
    try:
        from wiki_writer import WikiShape, WikiWriterError, write_wiki  # type: ignore
    except ImportError as exc:
        raise SchemaError(f"wiki_writer module unavailable: {exc}") from exc

    # Normalize lists: caller may pass len=1 raw_paths; collapse to raw_path.
    if raw_paths and len(raw_paths) == 1 and not raw_path:
        raw_path = raw_paths[0]
        raw_paths = None
    if urls and len(urls) == 1 and not url:
        url = urls[0]
        urls = None

    if raw_paths and len(raw_paths) >= 2:
        shape = WikiShape.MERGED
    elif source_type in (
        "topic", "insight", "comparison", "journal",
        "topics", "insights", "comparisons",
    ) and not raw_path and not url:
        shape = WikiShape.SYNTHESIS
    else:
        shape = WikiShape.STANDARD

    # Singularize plural source_type for the writer
    st_singular = _normalize_source_type_to_category(source_type).rstrip("s")
    # ...except 'entity' which the singularizer doesn't handle well
    if source_type in ("entity", "entities"):
        st_singular = "entity"

    try:
        return write_wiki(
            vault_root=vault,
            shape=shape,
            title=title,
            source_type=st_singular,
            summary=summary,
            body=body,
            tags=tags,
            related=related,
            raw_path=raw_path,
            raw_paths=raw_paths,
            url=url,
            urls=urls,
            date_added=date_added,
            extra=extra_frontmatter,
            overwrite=overwrite,
        )
    except WikiWriterError as exc:
        raise SchemaError(f"write_wiki_page: {exc}") from exc


def _wiki_subdir_for_meta(vault: Path, source_type: str) -> Path:
    """Resolve the wiki subdirectory for non-format source types like
    `topic`, `insight`, `project`, `entity`, `comparison`, `journal`,
    `keyword`. Reads the same config that wiki_format_dir uses."""
    import sys as _sys
    _sys.path.insert(0, str(Path(vault) / "bin" / "lib"))
    from config import _cfg  # type: ignore  # noqa: E402

    wiki_cfg = _cfg().get("wiki", {})
    mapping = {
        "topic": wiki_cfg.get("topics", "wiki/topics"),
        "insight": wiki_cfg.get("insights", "wiki/insights"),
        "journal": wiki_cfg.get("journal", "wiki/journal"),
        "project": "wiki/projects",
        "comparison": "wiki/comparisons",
        "keyword": "wiki/keywords",
    }
    rel = mapping.get(source_type)
    if not rel:
        raise SchemaError(f"Unknown wiki source_type: {source_type!r}")
    return Path(vault) / rel


def write_wiki_stub(*, vault: Path, source_type: str, old_name: str, new_name: str) -> Path:
    """Write a redirect stub at the old wiki page path.

    Used by `kb rename` to forward [[Old Title]] references to [[New
    Title]] without Obsidian silently auto-creating phantoms. Stubs are
    minimal-by-design (no summary, no tags) — they are forwarders, not
    content. Lint's "Suspect non-Athena pages" check skips them via the
    `redirect: true` flag.
    """
    out_dir = wiki_format_dir(vault, source_type) if source_type in (
        "paper", "repo", "webpage", "video", "image", "entity",
        "papers", "repos", "webpages", "videos", "images", "entities",
    ) else _wiki_subdir_for_meta(vault, source_type)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = _sanitize_wiki_title_for_filename(old_name)
    out = out_dir / f"{safe_stem}.md"
    body = (
        f"---\n"
        f'title: "{_escape_double_quotes(old_name)}"\n'
        f"redirect: true\n"
        f'redirect_to: "{_escape_double_quotes(new_name)}"\n'
        f"---\n\n"
        f"> [!info] This page was renamed. Continue to [[{new_name}]].\n\n"
        f"→ [[{new_name}]]\n"
    )
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, out)
    return out


# ── CLI for bash callers ───────────────────────────────────────────────
#
# bin/kb-capture is bash and would otherwise have to either (a) reinvent
# slug/frontmatter/H1 logic or (b) shell out for every primitive. Instead,
# bash pipes the body to this script and gets back the absolute path of
# the written file:
#
#   echo "$body" | python3 bin/lib/wiki_schema.py write \\
#       --vault "$KB_ROOT" \\
#       --source-type webpage \\
#       --url "$URL" \\
#       --title "$TITLE" \\
#       --extra clipped_via=kb-capture \\
#       --extra clipped_at=2026-05-01
#
# Output: one line, the absolute path. Errors go to stderr, exit nonzero.
def _cli():
    import argparse
    import sys as _sys

    p = argparse.ArgumentParser(description="Canonical raw-page writer (CLI for shell callers)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("write", help="Write a raw page")
    pw.add_argument("--vault", required=True)
    pw.add_argument("--source-type", required=True)
    pw.add_argument("--url", required=True)
    pw.add_argument("--title", required=True)
    pw.add_argument("--slug", default=None,
                    help="Override the URL-derived slug (callers that already "
                         "wrote a binary sibling under a specific slug — e.g. "
                         "arxiv's <paper_id>.pdf — should pass that slug here "
                         "so .md and .pdf live next to each other for "
                         "_find_binary_sibling lookup)")
    pw.add_argument("--extra", action="append", default=[],
                    help='key=value (repeat) — added to extra_frontmatter')
    pw.add_argument("--input", default="-", help="Body input (default: stdin)")

    ps = sub.add_parser("slug", help="Print canonical slug for input text (debug)")
    ps.add_argument("text")

    args = p.parse_args()

    if args.cmd == "slug":
        print(make_slug(args.text))
        return 0

    body = _sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    extras: dict = {}
    for kv in args.extra or []:
        if "=" not in kv:
            print(f"--extra expects key=value, got {kv!r}", file=_sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        extras[k.strip()] = v.strip()

    try:
        out = write_raw_page(
            vault=Path(args.vault),
            source_type=args.source_type,
            url=args.url,
            title=args.title,
            body=body,
            slug_override=args.slug,
            extra_frontmatter=extras or None,
        )
    except SchemaError as e:
        print(f"SchemaError: {e}", file=_sys.stderr)
        return 1
    print(str(out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
