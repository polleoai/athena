"""arcus_video — single-video YouTube ingest via arcus, wrapped into athena's raw video format.

Athena keeps playlist orchestration (detect playlist URLs, suggest "fetch the whole
course", iterate over playlist members). This module handles the single-video case
by delegating download + transcript extraction to arcus's YouTubeProvider, then
wrapping arcus's output into athena's raw video frontmatter shape.

Called by bin/kb-capture's `video)` branch. See feedback-athena-arcus-split memory
for the architectural rule that governs this split.

Usage from bash:
    python3 bin/lib/arcus_video.py \\
        --vault "$KB_ROOT" --url "$URL" --date "$DATE" \\
        [--desc "..."] [--keywords "..."] [--category "..."]

Prints the path of the raw artifact on success. Exits non-zero on failure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from datetime import date as _date
from pathlib import Path
from typing import Any


@contextlib.contextmanager
def _silence_fds():
    """Redirect FD 1 and FD 2 to /dev/null for the duration of the block.

    Required because yt-dlp's [download] progress writes directly to FD 1
    (carriage-return on a single line) and bypasses Python's sys.stdout —
    contextlib.redirect_stdout doesn't catch it. Without this, the bash
    caller of arcus_video.py captures the progress chatter as part of the
    'path' on stdout, contaminating downstream variable state.
    """
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

# Athena's lib modules live in the same directory; ensure they import.
_LIB_DIR = Path(__file__).parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from raw_writer import write_raw  # noqa: E402
from wiki_schema import SchemaError  # noqa: E402


class ArcusVideoError(Exception):
    """Raised when arcus extraction fails for any reason recoverable by the caller.

    The caller (kb-capture's `video)` branch) treats this as a signal to fall back
    to the legacy inline yt-dlp path.
    """


def _format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    return f"{seconds // 60}m{seconds % 60}s"


def _read_arcus_outputs(out_dir: Path) -> tuple[dict[str, Any], str]:
    """Return (json_payload, md_body_text) from arcus's writer output.

    arcus writes exactly one <slug>.md + <slug>.json on success. We don't care
    which slug it picked; just glob.
    """
    json_files = list(out_dir.glob("*.json"))
    if not json_files:
        raise ArcusVideoError(f"arcus produced no .json sidecar in {out_dir}")
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))

    md_files = list(out_dir.glob("*.md"))
    if not md_files:
        raise ArcusVideoError(f"arcus produced no .md in {out_dir}")
    md_text = md_files[0].read_text(encoding="utf-8")

    # Strip arcus's YAML frontmatter — we'll build athena's own.
    body = re.sub(r"^---\n.*?\n---\n", "", md_text, count=1, flags=re.DOTALL)
    return payload, body.strip()


def _build_athena_body(
    arcus_body: str,
    *,
    channel: str,
    duration_str: str,
    date_str: str,
    video_description: str = "",
    desc_hint: str = "",
    keywords_hint: str = "",
    category_hint: str = "",
) -> str:
    """Compose athena's raw video body with the standard section layout.

    Mirrors the layout produced by kb-capture's legacy inline yt-dlp branch so
    downstream wiki layer + lints see no structural difference.

    `video_description` is the creator's own description (yt-dlp's
    `info['description']`, exposed by arcus as SourceMetadata.description
    from arcus >= the description-support release). When present it lands in
    the `## Description` section; otherwise the placeholder ships so the
    section structure stays stable across backends.
    """
    parts: list[str] = []
    parts.append(f"- **Channel/Speaker:** {channel or '(unknown)'}")
    parts.append(f"- **Duration:** {duration_str or '(unknown)'}")
    parts.append(f"- **Clipped:** {date_str}")
    parts.append("- **Content fetched:** metadata + transcript (via arcus)")
    parts.append("")
    parts.append("## Description")
    parts.append("")
    if video_description and video_description.strip():
        parts.append(video_description.strip())
    else:
        parts.append("<!-- No description available (arcus returned no "
                     "description field, or the video has none). -->")

    if desc_hint or keywords_hint or category_hint:
        parts.append("")
        parts.append("## User Suggestions")
        parts.append("")
        parts.append("<!-- These are user-provided hints. The ingest workflow will "
                     "independently analyze the content and reconcile — do not blindly "
                     "trust these. -->")
        parts.append("")
        if desc_hint:
            parts.append(f"- **Description:** {desc_hint}")
        if keywords_hint:
            parts.append(f"- **Keywords:** {keywords_hint}")
        if category_hint:
            parts.append(f"- **Category:** {category_hint}")

    parts.append("")
    parts.append("## Transcript")
    parts.append("")
    parts.append(arcus_body or "<!-- Transcript not available -->")
    return "\n".join(parts) + "\n"


def ingest_youtube_url(
    *,
    vault: Path,
    url: str,
    date_str: str | None = None,
    desc_hint: str = "",
    keywords_hint: str = "",
    category_hint: str = "",
) -> Path:
    """Extract a YouTube video via arcus + write an athena raw video artifact.

    Returns the Path of the raw artifact. Raises ArcusVideoError on any failure
    (arcus extraction failed, arcus produced no output, wiki_schema rejected
    the write, etc.). The bash caller is expected to fall back to its legacy
    inline yt-dlp path on this exception.
    """
    # Lazy-import arcus so a missing dep gives a clean error rather than
    # crashing at module load.
    try:
        from arcus.provider_runtime import (
            EXIT_CODES,
            Factory,
            ProviderRegistry,
            register_defaults,
        )
    except ImportError as e:
        raise ArcusVideoError(
            f"arcus-provider-runtime not installed (pip install -e "
            f"<arcus repo>/packages/provider-runtime): {e}"
        ) from e

    registry = ProviderRegistry()
    register_defaults(registry)
    factory = Factory(registry)

    match = factory.detect(url)
    if match is None:
        raise ArcusVideoError(f"no arcus provider matches: {url}")

    with tempfile.TemporaryDirectory(prefix="athena-arcus-") as tmp:
        out_dir = Path(tmp)
        with _silence_fds():
            exit_code = factory.run(url, out_dir=out_dir, force=False)
        if exit_code != EXIT_CODES["SUCCESS"]:
            raise ArcusVideoError(
                f"arcus extraction failed (exit {exit_code}) for {url}"
            )

        payload, arcus_body = _read_arcus_outputs(out_dir)

    metadata = payload.get("metadata") or {}
    title = metadata.get("title") or "Untitled Video"
    video_id = metadata.get("source_id") or ""
    channel = metadata.get("author") or ""
    duration_str = _format_duration(metadata.get("duration_ms", 0) // 1000 if metadata.get("duration_ms") else 0)
    posted = metadata.get("posted") or ""
    language = metadata.get("language") or ""
    # `description` is populated by arcus when the YouTubeProvider can
    # read yt-dlp's info['description']. Older arcus releases omit this
    # field (predates the 2026-05-31 description-support change); use
    # .get() so we degrade to the placeholder instead of KeyError'ing.
    video_description = metadata.get("description") or ""

    date_str = date_str or _date.today().isoformat()

    body = _build_athena_body(
        arcus_body,
        channel=channel,
        duration_str=duration_str,
        date_str=date_str,
        video_description=video_description,
        desc_hint=desc_hint,
        keywords_hint=keywords_hint,
        category_hint=category_hint,
    )

    extras: dict[str, Any] = {
        "clipped_via": "kb-capture (arcus)",
        "clipped_at": date_str,
    }
    if channel:
        extras["channel"] = channel
    if duration_str:
        extras["duration"] = duration_str
    if posted:
        extras["posted"] = posted
    if language:
        extras["language"] = language

    # Force the existing athena video-slug convention `video-<vid_id>` so
    # new arcus-extracted videos coexist with legacy raws (which use that
    # same shape) and so two captures of the same URL collapse to one
    # file. Without slug_override, athena's URL-derived slug strips the
    # `?v=...` query and every YouTube URL collides on `youtube-com-watch`.
    slug_override = f"video-{video_id}" if video_id else None

    try:
        raw_path = write_raw(
            vault_root=vault,
            source_type="video",
            url=url,
            title=title,
            body=body,
            slug_override=slug_override,
            extra=extras,
        )
    except Exception as e:
        raise ArcusVideoError(f"athena raw_writer rejected write: {e}") from e

    return raw_path


def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Ingest a YouTube URL via arcus + write athena raw video artifact"
    )
    p.add_argument("--vault", required=True, type=Path, help="Athena vault root")
    p.add_argument("--url", required=True, help="YouTube URL to extract")
    p.add_argument("--date", default=None, help="ISO date for clipped_at (default: today)")
    p.add_argument("--desc", default="", help="User-provided description hint")
    p.add_argument("--keywords", default="", help="User-provided keywords hint")
    p.add_argument("--category", default="", help="User-provided category hint")
    args = p.parse_args()

    try:
        raw_path = ingest_youtube_url(
            vault=args.vault,
            url=args.url,
            date_str=args.date,
            desc_hint=args.desc,
            keywords_hint=args.keywords,
            category_hint=args.category,
        )
    except ArcusVideoError as e:
        print(f"arcus_video: {e}", file=sys.stderr)
        return 1

    print(str(raw_path))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
