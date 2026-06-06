"""wiki_writer — the single typed write API for wiki pages.

Phase 2 of the schema refactor. Companion to raw_writer for the wiki
side. Phase 3 migrates wiki_schema.write_wiki_page to delegate here.

Enforces:
  * Schema validation per shape (StandardWikiPage / MergedWikiPage /
    SynthesisWikiPage / RedirectStub) — bad data rejected at write time
  * raw_path/raw_paths must point under raw/<cat>/artifacts/ (eliminates
    lint #49's drift class on the wiki side)
  * URL canonicalization — wiki url: field always canonical
  * Body never embeds raw frontmatter (eliminates lint #48)
  * Atomic write via temp + rename
  * Frontmatter is YAML, never the legacy header-bullet form

Lints retired when this writer is the only path:
  - #46 Invalid YAML (Pydantic→YAML serialization is correct by construction)
  - #48 Leaked raw frontmatter in wiki body (writer strips at boundary)

Pure on inputs except for the final atomic write.
"""

from __future__ import annotations

import html
import os
import re
from datetime import date as _date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from schemas import (
    MergedWikiPage,
    RedirectStub,
    SourceType,
    StandardWikiPage,
    SynthesisWikiPage,
    WikiShape,
)
from url_canonical import canonicalize


class WikiWriterError(ValueError):
    """Raised when a wiki write fails any precondition."""


# Map source_type → on-disk wiki path. Two kinds:
#   * Format types: wiki/format/<plural>/  (have raw sources)
#   * Synthesis types: wiki/<plural>/       (LLM-authored, no raw)
# This split is historical — wiki/format/ was added later for the
# raw-source-backed pages while wiki/topics/ etc. predate it. Reality
# on disk wins; the mapping must mirror it exactly.
_SOURCE_TYPE_TO_REL = {
    # Format types — under wiki/format/
    "paper": "format/papers",
    "repo": "format/repos",
    "webpage": "format/webpages",
    "video": "format/videos",
    "image": "format/images",
    "book": "format/books",
    "entity": "format/entities",
    "comparison": "format/comparisons",
    # Synthesis types — under wiki/ at root
    "topic": "topics",
    "insight": "insights",
    "journal": "journal",
}


def _today_iso() -> str:
    return _date.today().isoformat()


def _yaml_escape(s: str) -> str:
    """Escape for double-quoted YAML scalar."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _format_fm_value(key: str, value: Any) -> str:
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


_LEADING_FM_RE = re.compile(r"^\s*---\n.*?\n---\s*\n", re.DOTALL)


def _strip_leading_frontmatter(body: str) -> str:
    """Remove a leading frontmatter block from body. Lint #48 class."""
    return _LEADING_FM_RE.sub("", body, count=1)


def _wikilink(s: str) -> str:
    """Wrap `s` in [[...]] if it isn't already a wikilink or URL."""
    s = s.strip()
    if not s:
        return s
    if s.startswith("[[") and s.endswith("]]"):
        return s
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"[[{s}]]"


def _safe_filename(title: str) -> str:
    """Sanitize title for use as wiki filename — Obsidian uses the filename as
    the wikilink target, so the merge/rename/refresh-wiki path that goes through
    here must produce the same name as fresh capture for the canonical Athena
    title shape ('Prefix: plain text').

    COLON handling mirrors wiki_page._safe_filename (issue #131): on POSIX the
    colon is kept verbatim because Athena's 'Prefix: Title' convention is valid
    there (and HFS+/APFS/ext4 allow it); on Windows it maps to ' —' since NTFS
    reserves ':' for alternate data streams. Previously this function dropped the
    colon to '-' unconditionally, so a merge produced 'Web- Foo.md' while a fresh
    capture produced 'Web: Foo.md' — a divergence that broke wikilink resolution.

    OTHER reserved chars (/ \\ * ? " < > |) are replaced with '-' on both
    platforms. This is intentionally STRICTER than wiki_page._safe_filename
    (which leaves them on POSIX / deletes them on Windows); canonical titles have
    already been through apply_naming_convention and rarely contain them, so the
    two implementations are byte-identical for the shapes that matter (verified by
    the parity test in tests/test_wiki_writer_safe_filename.py).
    """
    t = html.unescape(title.strip())
    # Windows reserves ':'; map it to a readable ' —'. POSIX keeps it untouched.
    if os.name == "nt":
        t = t.replace(":", " —")
    # Forbidden on macOS/Windows/Linux: / \ * ? " < > |  (':' handled above)
    t = re.sub(r'[/\\*?"<>|]', "-", t)
    # Collapse runs of whitespace
    t = re.sub(r"\s+", " ", t).strip()
    # No trailing dots/spaces (Windows hates them)
    t = t.rstrip(". ")
    return t


def write_wiki(
    *,
    vault_root: str | Path,
    shape: WikiShape | str,
    title: str,
    source_type: str | None = None,
    summary: str | None = None,
    body: str = "",
    tags: list[str] | None = None,
    related: list[str] | None = None,
    raw_path: str | None = None,
    raw_paths: list[str] | None = None,
    url: str | None = None,
    urls: list[str] | None = None,
    date_added: str | None = None,
    redirect_to: str | None = None,
    extra: dict[str, Any] | None = None,
    overwrite: bool = False,
    canonicalize_urls: bool = True,
) -> Path:
    """Write a wiki page atomically with full schema validation.

    Caller specifies shape (or it's inferred from arguments). The right
    Pydantic model validates the assembled frontmatter; failure raises
    WikiWriterError without writing anything.

    Required arguments by shape:
      STANDARD: source_type, summary, raw_path, url, tags
      MERGED:   source_type, summary, raw_paths (≥2), urls (=len(raw_paths)), tags
      SYNTHESIS: source_type ∈ {entity, topic, insight, comparison, journal},
                 summary, tags
      REDIRECT: redirect_to (no other content fields)
    """
    vault = Path(vault_root)
    if not vault.is_dir():
        raise WikiWriterError(f"vault_root must exist: {vault!r}")

    if isinstance(shape, str):
        shape = WikiShape(shape)

    if not title or not title.strip():
        raise WikiWriterError("title is required and must be non-empty")
    title = html.unescape(title.strip())

    # Compose frontmatter dict per shape, then validate, then serialize.
    fm_dict: dict[str, Any] = {"title": title}

    if shape == WikiShape.REDIRECT:
        if not redirect_to or not redirect_to.strip():
            raise WikiWriterError("REDIRECT shape requires redirect_to")
        fm_dict["redirect"] = True
        fm_dict["redirect_to"] = redirect_to.strip()
        rel = _SOURCE_TYPE_TO_REL.get(source_type or "webpage", "format/webpages")
        out_dir = vault / "wiki" / rel
        body_text = ""  # redirect stubs have no body
    else:
        if not source_type:
            raise WikiWriterError(f"source_type is required for shape={shape.value}")
        if not summary or not summary.strip():
            raise WikiWriterError(
                "summary is required (lint #36 was the symptom of summaries "
                "being optional; the writer enforces it)"
            )
        summary = html.unescape(summary.strip())
        if shape != WikiShape.SYNTHESIS and not tags:
            tags = [source_type]

        fm_dict["source_type"] = source_type
        fm_dict["date_added"] = date_added or _today_iso()
        fm_dict["last_updated"] = _today_iso()

        if shape == WikiShape.STANDARD:
            if not raw_path:
                raise WikiWriterError("STANDARD shape requires raw_path")
            if not url:
                raise WikiWriterError("STANDARD shape requires url")
            fm_dict["raw_path"] = raw_path
            fm_dict["url"] = canonicalize(url).url if canonicalize_urls else url
        elif shape == WikiShape.MERGED:
            if not raw_paths or len(raw_paths) < 2:
                raise WikiWriterError("MERGED shape requires raw_paths with ≥2 entries")
            if not urls or len(urls) != len(raw_paths):
                raise WikiWriterError(
                    "MERGED shape requires urls list with same length as raw_paths"
                )
            fm_dict["raw_paths"] = raw_paths
            fm_dict["urls"] = (
                [canonicalize(u).url for u in urls] if canonicalize_urls else urls
            )
        elif shape == WikiShape.SYNTHESIS:
            # No raw_path or url — synthesis pages are LLM-authored.
            pass

        fm_dict["tags"] = tags or []
        fm_dict["related"] = (
            [_wikilink(r) for r in related] if related else []
        )
        fm_dict["summary"] = summary

        rel = _SOURCE_TYPE_TO_REL.get(source_type)
        if rel is None:
            raise WikiWriterError(
                f"source_type={source_type!r} has no on-disk mapping; "
                f"expected one of {sorted(_SOURCE_TYPE_TO_REL)}"
            )
        out_dir = vault / "wiki" / rel
        body_text = body

    if extra:
        reserved = set(fm_dict.keys())
        for k, v in extra.items():
            if k in reserved:
                continue
            fm_dict[k] = v

    # Validate before any FS work
    Model = {
        WikiShape.STANDARD: StandardWikiPage,
        WikiShape.MERGED: MergedWikiPage,
        WikiShape.SYNTHESIS: SynthesisWikiPage,
        WikiShape.REDIRECT: RedirectStub,
    }[shape]
    try:
        Model(**fm_dict)
    except ValidationError as exc:
        first_err = exc.errors()[0]
        raise WikiWriterError(
            f"frontmatter validation failed: "
            f"{'.'.join(str(x) for x in first_err['loc'])}: {first_err['msg']}"
        ) from exc

    # Compose YAML frontmatter
    fm_lines = ["---"]
    for key, val in fm_dict.items():
        fm_lines.append(_format_fm_value(key, val))
    fm_lines.append("---")

    # Strip leaked raw frontmatter from body — lint #48 prevention.
    cleaned_body = _strip_leading_frontmatter(body_text).rstrip()

    # Auto-generate ## Connections section if `related` is provided and the
    # body doesn't already have one. Frontmatter `related:` is hidden in
    # Obsidian preview — the body section is what users actually click.
    # Same logic as legacy wiki_schema.write_wiki_page.
    if shape != WikiShape.REDIRECT and related and "## Connections" not in cleaned_body:
        conn_lines = ["", "## Connections", ""]
        for r in related:
            link = _wikilink(r)
            conn_lines.append(f"- {link}")
        if cleaned_body:
            cleaned_body = cleaned_body + "\n" + "\n".join(conn_lines)
        else:
            cleaned_body = "\n".join(conn_lines).lstrip()

    content = "\n".join(fm_lines)
    if cleaned_body:
        content += "\n\n" + cleaned_body
    content += "\n"

    # Compose destination path
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_safe_filename(title)}.md"
    if out.exists() and not overwrite:
        raise WikiWriterError(
            f"wiki page already exists: {out.relative_to(vault)} "
            f"(pass overwrite=True if intentional)"
        )

    # Atomic write
    tmp = out.with_suffix(out.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, out)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise WikiWriterError(f"filesystem write failed: {exc}") from exc

    return out


# ─── Self-test ───────────────────────────────────────────────────────


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        vault = Path(td)

        # STANDARD
        path = write_wiki(
            vault_root=vault,
            shape=WikiShape.STANDARD,
            title="GitHub: skills — anthropics",
            source_type="repo",
            summary="A repo of agent skills.",
            tags=["ai-agents"],
            related=["Some Related Page"],
            raw_path="raw/repos/artifacts/github-com-anthropics-skills.md",
            url="https://github.com/anthropics/skills?utm_source=share",
        )
        assert path.exists()
        text = path.read_text()
        assert 'url: "https://github.com/anthropics/skills"' in text, "URL should be canonicalized"
        assert "[[Some Related Page]]" in text

        # MERGED
        path2 = write_wiki(
            vault_root=vault,
            shape=WikiShape.MERGED,
            title="Stanford CS229 — ML",
            source_type="repo",
            summary="Combined notes.",
            tags=["ml"],
            raw_paths=[
                "raw/repos/artifacts/afshinea--stanford.md",
                "raw/webpages/artifacts/drive-google.md",
            ],
            urls=[
                "https://github.com/afshinea/stanford",
                "https://drive.google.com/file/d/abc/view",
            ],
        )
        assert path2.exists()

        # SYNTHESIS
        path3 = write_wiki(
            vault_root=vault,
            shape=WikiShape.SYNTHESIS,
            title="AI Agents",
            source_type="topic",
            summary="Topic page.",
            tags=["ai-agents"],
            related=["Foo", "Bar"],
        )
        assert path3.exists()
        # No raw_path / url should be in frontmatter
        text3 = path3.read_text()
        assert "raw_path" not in text3
        assert "url:" not in text3

        # REDIRECT
        path4 = write_wiki(
            vault_root=vault,
            shape=WikiShape.REDIRECT,
            title="Old Page Name",
            redirect_to="New Page Name",
            source_type="webpage",  # determines target dir
        )
        text4 = path4.read_text()
        assert "redirect: true" in text4
        assert 'redirect_to: "New Page Name"' in text4

        # Body with leaked frontmatter should be stripped
        path5 = write_wiki(
            vault_root=vault,
            shape=WikiShape.STANDARD,
            title="Leak test",
            source_type="webpage",
            summary="ok",
            raw_path="raw/webpages/artifacts/leak.md",
            url="https://example.com",
            body="""---
source: "leaked"
foo: bar
---

# Real body

actual content""",
        )
        text5 = path5.read_text()
        assert text5.count("---") == 2, f"should have exactly 2 --- delimiters, got: {text5[:300]}"

        # Negative: STANDARD missing raw_path
        try:
            write_wiki(
                vault_root=vault,
                shape=WikiShape.STANDARD,
                title="No raw",
                source_type="webpage",
                summary="ok",
                url="https://example.com",
            )
        except WikiWriterError as e:
            assert "raw_path" in str(e)
        else:
            raise AssertionError("expected raw_path requirement")

        # Negative: MERGED with single raw_path
        try:
            write_wiki(
                vault_root=vault,
                shape=WikiShape.MERGED,
                title="Solo merge",
                source_type="webpage",
                summary="ok",
                raw_paths=["raw/webpages/artifacts/only-one.md"],
                urls=["https://example.com"],
            )
        except WikiWriterError as e:
            assert "≥2" in str(e) or ">=2" in str(e) or "2" in str(e)
        else:
            raise AssertionError("expected raw_paths≥2 requirement")

    print("All wiki_writer self-tests passed.")
