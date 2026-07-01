"""raw_writer — the single typed write API for raw artifacts.

Phase 2 of the schema refactor. This is the function every raw-write
caller must funnel through (Phase 3 migrates each one). It enforces:

  * Schema validation (RawFrontmatter) — bad data rejected at write time
  * Slug derivation via slug.derive_slug — collision-bait rejected
  * URL canonicalization via url_canonical.canonicalize — tracking params
    stripped, /blob/→/resolve/, DOI alias collapsed, before storage
  * Destination is always raw/<category>/artifacts/<slug>.md
    (lint #49 made this impossible to drift, but enforcing here is
    cheaper than letting bad writes happen and fixing them later)
  * Atomic write via temp + rename (no half-written files)
  * Frontmatter is YAML, never the legacy header-bullet form

When this is the only writer in the vault (Phase 4), the following lint
sections become unnecessary because their bug class is impossible:
  - #38 Duplicate X status-id from tracking-param dups (canonicalize strips)
  - #39 Collision-bait raw slug (slug.derive_slug rejects)
  - #41 Empty raw_path (RawFrontmatter requires non-empty title)
  - #46 Invalid YAML frontmatter (Pydantic→YAML serialization is correct)
  - #49 Raw outside artifacts/ (path is constructed, not user-controlled)

Pure on inputs except for the final atomic write. Safe to call from
anywhere as long as `vault_root` is the vault directory.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from schemas import RawFrontmatter
from slug import SlugDerivationError, derive_slug
from url_canonical import canonicalize


class RawWriterError(ValueError):
    """Raised when a raw write fails any precondition. Catch this in
    callers to either retry with corrected data or surface to the user."""


class DegradedContentError(RawWriterError):
    """Subclass for write refusals where the INCOMING content itself is the
    problem (degraded-fetch markers, thin overwrite of a substantive raw,
    LinkedIn interstitial).

    Callers (process_clip) treat this differently from generic RawWriterError:
    retrying with the same input is guaranteed to fail again because the data
    on disk is deterministic. The right action is to quarantine the source
    artifact (e.g., move the Web Clipper clip to clippings/_failed/) rather
    than leaving it in the watchdog's retry queue.

    Refusals NOT caught by this subclass (e.g., transient OSError, slug
    derivation failure) stay as RawWriterError so callers can still retry
    them when conditions change.
    """


# Map source_type → on-disk category directory under raw/
# Mirrors config.raw_dir() but kept local so this module has no
# dependency on shell-config helpers (test-friendliness).
_CATEGORY_DIR = {
    "paper": "papers",
    "repo": "repos",
    "webpage": "webpages",
    "video": "videos",
    "image": "images",
    "book": "books",
}

_RESERVED_KEYS = {"title", "source", "captured_at"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yaml_escape(s: str) -> str:
    """Escape for a double-quoted YAML scalar kept on ONE physical line.

    Besides backslash/quote, newlines/CR/tab become escape sequences so a
    multi-line value can't split the frontmatter across physical lines.
    Witnessed 2026-05-26: an X.com <title> that stuffs the whole tweet in
    ('elvis on X: "…\\n\\n…" / X') rendered a multi-line `title:` value, which
    Obsidian's Properties parser flags as "Invalid properties".
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _format_fm_value(key: str, value: Any) -> str:
    """Render one frontmatter line. Lists become YAML-list form."""
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        lines = [f"{key}:"]
        for item in value:
            lines.append(f'  - "{_yaml_escape(str(item))}"')
        return "\n".join(lines)
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    return f'{key}: "{_yaml_escape(str(value))}"'


def write_raw(
    *,
    vault_root: str | Path,
    source_type: str,
    url: str | None,
    title: str,
    body: str,
    extra: dict[str, Any] | None = None,
    canonicalize_url: bool = True,
    slug_override: str | None = None,
    source_url_override: str | None = None,
) -> Path:
    """Write a raw artifact with full schema validation.

    Args:
        vault_root: Absolute path to the Athena vault.
        source_type: 'paper' | 'repo' | 'webpage' | 'video' | 'image' | 'book'.
            Determines storage subdirectory and slug-derivation policy.
        url: Source URL. Required for most categories (URL_DERIVED slug
            policy); optional for books (TITLE_DERIVED).
        title: Page title. Will be HTML-unescaped at the boundary so
            '&amp;' from source <title> doesn't reach the H1.
        body: The raw content (markdown). Frontmatter is added automatically;
            if `body` already starts with `---\\n...\\n---`, that block is
            stripped first to avoid double-frontmatter (was lint #48).
        extra: Optional category-specific fields (stars, language, authors).
            Reserved keys (title, source, captured_at) are silently skipped.
        canonicalize_url: If True (default), canonicalize the URL before
            storage. Set False only when the caller has already canonicalized.
        source_url_override: If set, written to the `source:` frontmatter in
            place of the (canonical) URL, WITHOUT affecting slug derivation or
            the storage path. Use when the canonical form is a dedup key the
            origin host does not serve (LinkedIn synthetic /posts/ugcpost-<id>)
            so the clickable `source:` stays resolvable while the slug/dedup
            identity remains canonical. See url_canonical.is_unservable_canonical.

    Returns:
        Absolute path of the written file.

    Raises:
        RawWriterError: If schema validation, slug derivation, or filesystem
            ops fail. Always wraps the underlying error for clarity.
    """
    vault = Path(vault_root)
    if not vault.is_dir():
        raise RawWriterError(f"vault_root must exist: {vault!r}")

    cat_dir = _CATEGORY_DIR.get(source_type)
    if cat_dir is None:
        raise RawWriterError(
            f"source_type={source_type!r} not recognized; expected one of "
            f"{sorted(_CATEGORY_DIR)}"
        )

    if not title or not title.strip():
        raise RawWriterError("title is required and must be non-empty")
    if not body or not body.strip():
        raise RawWriterError("body is required and must be non-empty")

    title = html.unescape(title.strip())

    canonical_url: str | None = None
    if url and url.strip():
        if canonicalize_url:
            canonical_url = canonicalize(url).url
        else:
            canonical_url = url.strip()

    # Slug derivation enforces per-category policy. Map source_type to
    # the plural-form category that slug.policy_for() uses.
    if slug_override:
        # Caller has a stable slug already (file-ingest from a known
        # filename, or a re-write where the existing slug must be kept).
        # We still normalize through slug.py's _normalize to enforce the
        # filesystem-safe character set.
        from slug import _normalize as _norm_slug
        slug = _norm_slug(slug_override)
        if not slug:
            raise RawWriterError(
                f"slug_override={slug_override!r} normalized to empty"
            )
    else:
        try:
            slug = derive_slug(cat_dir, canonical_url, title)
        except SlugDerivationError as exc:
            raise RawWriterError(f"slug derivation failed: {exc}") from exc

    # Validate the assembled frontmatter against the schema BEFORE any
    # filesystem writes — surfaces malformed extras immediately.
    fm_dict: dict[str, Any] = {
        "title": title,
        "captured_at": _now_iso(),
    }
    if canonical_url:
        # The slug/dedup identity is always `canonical_url` (below); the
        # `source:` link, however, must stay resolvable. When the caller passes
        # a source_url_override (canonical is an unservable dedup key, e.g. a
        # LinkedIn synthetic /posts/ugcpost-<id>), record that instead.
        override = (source_url_override or "").strip()
        fm_dict["source"] = override or canonical_url
    if extra:
        for k, v in extra.items():
            if k in _RESERVED_KEYS:
                continue
            fm_dict[k] = v
    try:
        RawFrontmatter(**fm_dict)
    except ValidationError as exc:
        raise RawWriterError(
            f"frontmatter validation failed: {exc.errors()[0]['msg']}"
        ) from exc

    # Strip any leaked frontmatter from the body — preempts lint #48.
    cleaned_body = _strip_leading_frontmatter(body).rstrip()

    # Render YAML frontmatter
    fm_lines = ["---"]
    for key, val in fm_dict.items():
        fm_lines.append(_format_fm_value(key, val))
    fm_lines.append("---")
    h1 = f"# {title}"
    content = "\n".join(fm_lines) + "\n\n" + h1 + "\n\n" + cleaned_body + "\n"

    # Compose destination path
    artifacts = vault / "raw" / cat_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    out = artifacts / f"{slug}.md"

    # Thin-overwrite guard. Refuse to clobber an existing larger raw with
    # significantly smaller new content. The "auth-wall capture overwrites
    # the good raw with 'Sign Up | LinkedIn'" failure mode (witnessed
    # during 0.10.0 capture-deep development) cost us the Praneeta raw
    # before being caught by git restore. This guard prevents the bug
    # class regardless of which capture path produced the thin content
    # (kb-capture timeout, Web Clipper auth-wall, capture-deep pre-login,
    # any future writer). Threshold: new content < 1KB AND existing > 5x
    # the new content. Bypass via CAPTCHA: if the URL has no canonical
    # form (just a hash slug), skip the check (synthetic captures).
    # Added 0.10.0 per the data-protection-is-#1-priority operating principle.
    # ALWAYS refuse to overwrite an existing raw with content that matches
    # known degraded-fetch patterns (auth wall, post-not-found, generic
    # error pages). These are size-independent — even a 5KB auth wall
    # is still garbage that shouldn't replace a 5KB real post. Pattern-
    # match against the actual incoming content.
    # 0.10.13: added after the Kumo case where capture-deep + auto-recapture
    # silently overwrote a 1.5KB Web Clipper raw with a 0.6KB "Post not
    # found" page (size ratio only 2.4x — old guard's 5x rule didn't fire).
    _DEGRADED_FETCH_MARKERS = (
        "Post not found",
        "This post was deleted or removed",
        "We couldn't find a post at this URL",
        "Sign Up | LinkedIn",
        "Join LinkedIn now",
        "Page not found",
        "404 Not Found",
    )
    if out.exists():
        for marker in _DEGRADED_FETCH_MARKERS:
            if marker in content:
                raise DegradedContentError(
                    f"refusing to overwrite existing raw at {out.name} with "
                    f"degraded-fetch content (matched marker: {marker!r}). "
                    f"This usually means the source URL no longer resolves "
                    f"(deleted post, login required, or transient error). "
                    f"To force overwrite, delete {out.name} first."
                )

    # ALWAYS refuse to write (new OR overwrite) when content matches the
    # LinkedIn external-link interstitial page. These pages have a fixed
    # shape — title is "LinkedIn", body warns about an external link with
    # phrases like "we're unable to verify it for safety" and "this link
    # will take you to a page that's not on LinkedIn". Writing them
    # produces sparse wiki pages with garbage titles and no real content.
    # The destination URL is embedded in the body — extract it into the
    # error message so the caller can re-queue the right URL.
    # Bug class surfaced 0.10.16 (Discord/decodingtrust/arxiv sparse pages).
    _INTERSTITIAL_MARKERS = (
        "we're unable to verify it for safety",
        "we are unable to verify it for safety",
        "will take you to a page that's not on LinkedIn",
        "will take you to a page that is not on LinkedIn",
        "Because this is an external link",
    )
    interstitial_hit = next((m for m in _INTERSTITIAL_MARKERS if m in content), None)
    if interstitial_hit:
        # Try to extract the destination URL from the interstitial body so
        # the caller can re-queue it. LinkedIn renders the destination as
        # a markdown-style link `[https://x](https://x)` near the bottom.
        dest_match = re.search(
            r"\[(https?://[^\]\s]+)\]\(https?://[^)\s]+\)",
            content,
        )
        dest_hint = (
            f" The destination URL appears to be {dest_match.group(1)} — "
            f"queue THAT URL instead." if dest_match
            else " No destination URL recoverable from the interstitial body."
        )
        raise DegradedContentError(
            f"refusing to write LinkedIn external-link interstitial as a "
            f"raw page (matched: {interstitial_hit!r}). The interstitial "
            f"is LinkedIn's safety warning, not real content.{dest_hint}"
        )

    # Size-based thin-overwrite guard. Two thresholds — both must agree
    # this is fishy enough to block. Tightened in 0.10.13 from 5x → 2x
    # ratio after a 1548 → 641 (2.4x) auth-wall overwrite slipped past.
    # Threshold: new content < 1KB AND existing > 2x the new content.
    # 1KB is the rough floor below which an article-style raw is
    # unlikely to be substantive; existing-bigger-by-2x is suspicious
    # enough to refuse without being so aggressive it blocks legitimate
    # Web Clipper updates that genuinely shrunk a page.
    # Added 0.10.0 per the data-protection-is-#1-priority operating principle.
    if out.exists() and len(content) < 1024:
        try:
            existing_size = out.stat().st_size
            if existing_size > len(content) * 2:
                raise DegradedContentError(
                    f"refusing to overwrite existing {existing_size}-byte raw "
                    f"with thin {len(content)}-byte content at {out.name} "
                    f"(likely auth-wall capture / failed fetch). To force "
                    f"overwrite, delete {out.name} first or use a larger capture."
                )
        except OSError:
            pass  # stat failed; let the write proceed and fail naturally

    # Atomic write — temp + rename ensures no half-written file ever lands
    tmp = out.with_suffix(out.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, out)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RawWriterError(f"filesystem write failed: {exc}") from exc

    return out


_LEADING_FM_RE = re.compile(r"^\s*---\n.*?\n---\s*\n", re.DOTALL)


def _strip_leading_frontmatter(body: str) -> str:
    """Remove a leading `---\\n...\\n---` block from `body` if present.

    Some content sources (Web Clipper drops, manual pastes from another
    Athena vault) come with their own frontmatter. If we don't strip it
    before writing, the result has DOUBLE frontmatter — Obsidian shows
    the second as a horizontal rule + plaintext keys. That was the lint
    #48 bug class. Strip at the boundary."""
    return _LEADING_FM_RE.sub("", body, count=1)


# ─── Self-test ───────────────────────────────────────────────────────


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        # Standard case
        path = write_raw(
            vault_root=td,
            source_type="repo",
            url="https://github.com/anthropics/skills?s=12",
            title="anthropics/skills",
            body="# Repo content\n\nSome README text.",
            extra={"stars": "131080", "language": "Python"},
        )
        assert path.exists()
        assert path.parent.name == "artifacts"
        text = path.read_text()
        assert 'source: "https://github.com/anthropics/skills"' in text, "URL should be canonicalized (no ?s=12)"
        assert 'stars: "131080"' in text
        # Slug should be URL-derived
        assert "github-com-anthropics-skills" in str(path)

        # Body with leaked frontmatter — should be stripped
        path2 = write_raw(
            vault_root=td,
            source_type="webpage",
            url="https://example.com/article",
            title="Some Article",
            body="""---
title: "leaked"
foo: bar
---

# Real content

Body text.""",
        )
        text2 = path2.read_text()
        assert text2.count("---") == 2, f"should have exactly 2 --- delimiters, got: {text2[:200]}"

        # Negative: collision-bait title for category that requires URL
        try:
            write_raw(
                vault_root=td,
                source_type="webpage",
                url=None,
                title="Untitled",
                body="hi",
            )
        except RawWriterError as e:
            assert "URL_DERIVED" in str(e) or "URL" in str(e)
        else:
            raise AssertionError("expected URL requirement to reject")

        # Negative: bad source_type
        try:
            write_raw(
                vault_root=td,
                source_type="bogus",
                url="https://example.com",
                title="Foo",
                body="bar",
            )
        except RawWriterError as e:
            assert "source_type" in str(e)
        else:
            raise AssertionError("expected bad source_type to reject")

        # Book — TITLE_DERIVED, no URL needed
        path3 = write_raw(
            vault_root=td,
            source_type="book",
            url=None,
            title="Introduction to Measure Theory",
            body="Book content here.",
        )
        assert "introduction-to-measure-theory" in str(path3)

    print("All raw_writer self-tests passed.")
