"""arcus_file — file-based ingest (PDF / DOCX / XLSX / PPTX / EPUB) via arcus.

athena downloads the file to raw/<subdir>/<slug>.<ext> using its own
download logic (Drive special-handling, dead-URL recording, PDF magic-byte
validation), then hands the local path to arcus's PdfProvider or
DocsProvider for text extraction. arcus_file wraps the result into
athena's raw .md format and writes via raw_writer.write_raw.

The download stays in athena because the download logic is vault-aware
(records failures to inbox/url-dead.txt, knows Drive's quirks, validates
PDF magic bytes). The extraction goes to arcus.

Called by bin/ingest-file (which keeps its existing CLI surface).
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import tempfile
from datetime import date as _date
from pathlib import Path
from typing import Any


# Athena's lib modules live in the same directory; ensure they import.
_LIB_DIR = Path(__file__).parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from raw_writer import write_raw  # noqa: E402


@contextlib.contextmanager
def _silence_fds():
    """Suppress stdout/stderr during arcus's extraction so the bash caller
    captures only the path on stdout. Mirrors arcus_video.py + arcus_html.py."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull)


# source_type → raw/ subdirectory. Mirrors bin/ingest-file's existing layout.
_RAW_SUBDIR = {
    'paper':       'papers',
    'doc':         'docs',
    'spreadsheet': 'spreadsheets',
    'slides':      'slides',
    'book':        'books',
    'image':       'images',
}

# source_type → write_raw's source_type vocabulary. raw_writer accepts
# {paper, repo, webpage, video, image, book}. doc/spreadsheet/slides don't
# have direct mappings — they fall back to 'paper' since raw_writer treats
# them as "non-web, file-based content" alike.
_RAW_WRITER_TYPE = {
    'paper':       'paper',
    'doc':         'paper',
    'spreadsheet': 'paper',
    'slides':      'paper',
    'book':        'book',
    'image':       'image',
}


class ArcusFileError(Exception):
    """Raised when arcus file extraction fails for a recoverable reason."""


def _extract_via_arcus(local_path: Path, ext: str) -> dict[str, str]:
    """Run the local file through arcus (PdfProvider for pdf, DocsProvider
    for docx/xlsx/pptx/epub) and return {title, authors, text}.

    Reads arcus's .json sidecar for structured metadata. The .md file is
    written but discarded — athena composes its own body in the legacy
    format that wiki_page.py expects.
    """
    try:
        from arcus.provider_runtime import (
            EXIT_CODES,
            Factory,
            ProviderRegistry,
            register_defaults,
        )
    except ImportError as e:
        raise ArcusFileError(
            f"arcus-provider-runtime not installed: {e}"
        ) from e

    registry = ProviderRegistry()
    register_defaults(registry)
    factory = Factory(registry)

    match = factory.detect(str(local_path))
    if match is None:
        raise ArcusFileError(f"no arcus provider matches: {local_path}")

    provider, _ = match
    expected = {'pdf', 'docs'}
    if provider.kind not in expected:
        raise ArcusFileError(
            f"arcus dispatched to {provider.kind}, expected one of {expected}"
        )

    with tempfile.TemporaryDirectory(prefix="athena-arcus-file-") as tmp:
        out_dir = Path(tmp)
        with _silence_fds():
            exit_code = factory.run(str(local_path), out_dir=out_dir, force=False)
        if exit_code != EXIT_CODES["SUCCESS"]:
            raise ArcusFileError(
                f"arcus extraction failed (exit {exit_code}) for {local_path}"
            )

        # Read the .json sidecar for structured fields. arcus always writes
        # exactly one .md + .json pair per call.
        import json
        json_files = list(out_dir.glob("*.json"))
        if not json_files:
            raise ArcusFileError(f"arcus produced no .json sidecar in {out_dir}")
        payload = json.loads(json_files[0].read_text(encoding="utf-8"))

    metadata = payload.get("metadata") or {}
    return {
        "title": (metadata.get("title") or "").strip(),
        "authors": (metadata.get("author") or "").strip(),
        "text": payload.get("text") or "",
    }


def _build_athena_body(
    *,
    title: str,
    source_kind: str,
    downloaded_basename: str,
    authors: str,
    text: str,
    date_str: str,
) -> str:
    """Compose the raw .md body in the legacy format bin/ingest-file produces
    (so existing wiki pages built against this format keep parsing cleanly).
    """
    has_text = bool(text and text.strip())
    parts = [
        f"- **Authors:** {authors or '(to be filled)'}",
        f"- **{source_kind.upper()} file:** ./{downloaded_basename}",
        f"- **Clipped:** {date_str}",
        f"- **Content fetched:** yes ({'full text' if has_text else 'metadata only'}, via arcus)",
        "",
        "## Content",
        "",
    ]
    if has_text:
        parts.append(text)
    else:
        parts.append("<!-- Text extraction unavailable. Paste content manually or re-run capture. -->")
    return "\n".join(parts) + "\n"


def ingest_local_file(
    *,
    vault: Path,
    url: str,
    local_path: Path,
    source_type: str,
    ext: str,
    title_hint: str = "",
    date_str: str | None = None,
) -> Path:
    """Extract text from an already-downloaded local file via arcus and write
    an athena raw .md artifact under raw/<subdir>/.

    Caller is responsible for downloading the file (athena's vault-aware
    download logic stays in bin/ingest-file). This function only handles
    the extraction + raw_writer.write_raw step.

    Returns the Path of the raw .md artifact. Raises ArcusFileError on any
    extraction or write failure.
    """
    if source_type not in _RAW_SUBDIR:
        raise ArcusFileError(f"unsupported source_type: {source_type!r}")

    if not local_path.exists():
        raise ArcusFileError(f"local file not found: {local_path}")

    extracted = _extract_via_arcus(local_path, ext)
    title = extracted["title"] or title_hint or local_path.stem
    authors = extracted["authors"]
    text = extracted["text"]

    date_str = date_str or _date.today().isoformat()

    body = _build_athena_body(
        title=title,
        source_kind=ext,
        downloaded_basename=local_path.name,
        authors=authors,
        text=text,
        date_str=date_str,
    )

    extras: dict[str, Any] = {
        "clipped_via": "ingest-file (arcus)",
        "clipped_at": date_str,
    }
    if authors:
        extras["authors"] = authors

    # bin/ingest-file historically uses the file's slug as the raw .md stem,
    # parallel to the downloaded source file. Pass slug_override so the raw
    # .md sits next to the .pdf/.docx/etc.
    slug_override = local_path.stem

    raw_writer_type = _RAW_WRITER_TYPE.get(source_type, "paper")

    try:
        raw_path = write_raw(
            vault_root=vault,
            source_type=raw_writer_type,
            url=url,
            title=title,
            body=body,
            slug_override=slug_override,
            extra=extras,
        )
    except Exception as e:
        raise ArcusFileError(f"athena raw_writer rejected write: {e}") from e

    return raw_path
