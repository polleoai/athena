"""Pydantic schemas for raw + wiki frontmatter.

This is the data contract for the Athena vault. Once Phase 3 migration
is complete, every write to a raw or wiki page will pass through one of
these schemas — bad data is rejected at write time instead of being
caught by post-hoc lint sections.

The schemas are derived empirically from `kb schema-check` output run
against the existing vault (see athena-schema-refactor-plan.md). Field
sets, types, and required/optional status reflect what actually exists
in the corpus today, not what an idealized design would prescribe — so
that Phase 1 can be deployed without breaking anything.

Three wiki-page shapes are recognized:
  * Standard (paper/repo/webpage/video/image): single raw_path + url
  * Merged (any source_type): raw_paths + urls (lists)
  * Synthesis (entity/topic/insight/comparison): no raw_path or url
  * Redirect stub: only title + redirect + redirect_to

Phase 1 only DEFINES these schemas. The `kb schema-check` command uses
them to score the vault. No writers are migrated yet — that is Phase 3.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


# ─── Shared types ────────────────────────────────────────────────────


NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class SourceType(str, Enum):
    """Wiki page kinds. Drives storage location, expected fields, and
    rendering behavior. Match the values currently used in
    `wiki/format/<dir>/*.md` frontmatter — adding a new value here
    requires adding a new wiki/format/ subdirectory."""

    PAPER = "paper"
    REPO = "repo"
    WEBPAGE = "webpage"
    VIDEO = "video"
    IMAGE = "image"
    BOOK = "book"
    # Synthesis pages — LLM-authored, no raw source
    ENTITY = "entity"
    TOPIC = "topic"
    INSIGHT = "insight"
    COMPARISON = "comparison"
    JOURNAL = "journal"


# Source types that REQUIRE a raw_path (or raw_paths) and url (or urls).
# Synthesis types are LLM-authored and don't have a raw source.
_SOURCE_TYPES_WITH_RAW = frozenset({
    SourceType.PAPER, SourceType.REPO, SourceType.WEBPAGE,
    SourceType.VIDEO, SourceType.IMAGE, SourceType.BOOK,
})


# ─── Raw frontmatter ─────────────────────────────────────────────────


class RawFrontmatter(BaseModel):
    """Schema for the YAML frontmatter at the top of every file in
    `raw/<category>/artifacts/`. These are the post-capture immutable
    source files — created by kb-capture, Web Clipper, or kb_add.

    Required fields are universal across all categories (papers, repos,
    webpages, etc.). Category-specific fields (stars, language for
    repos; authors for papers) are accepted via `model_config.extra`.
    """

    model_config = ConfigDict(
        extra="allow",  # tolerate category-specific fields like stars/language
        str_strip_whitespace=True,
    )

    title: NonEmptyStr
    # The URL — kept as 'source' because that's what raw FM uses. Optional
    # because some legacy raws are local-PDF imports with no canonical URL
    # (e.g., uploaded lecture notes). Phase 4 strict mode will require it
    # for newly-captured raws but always tolerate legacy.
    source: Optional[NonEmptyStr] = None
    captured_at: Optional[Union[datetime, str]] = None
    clipped_via: Optional[str] = None  # 'kb-capture' | 'web-clipper' | etc.
    clipped_at: Optional[Union[date, str]] = None

    @field_validator("source")
    @classmethod
    def _source_looks_like_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("file://")):
            raise ValueError(f"source must be a URL (http/https/file), got: {v!r}")
        return v


# ─── Wiki frontmatter (discriminated by shape) ───────────────────────


class _WikiBase(BaseModel):
    """Fields common to every wiki page shape."""

    model_config = ConfigDict(
        extra="allow",  # tolerate aliases, discovered_via, etc. without listing each
        str_strip_whitespace=True,
    )

    title: NonEmptyStr
    date_added: Union[date, str]
    last_updated: Union[date, str]


class StandardWikiPage(_WikiBase):
    """A wiki page that summarizes ONE raw source.

    The most common shape — paper/repo/webpage/video/image with a single
    raw_path + url pair. ~95% of pages in the vault.
    """

    source_type: SourceType
    raw_path: NonEmptyStr
    # url is Optional because papers ingested as local PDFs (UC Davis
    # lecture notes, hand-uploaded research papers) legitimately have no
    # canonical web URL. New writes through wiki_writer always supply one
    # for hosted content; this Optional accommodates legitimate legacy data.
    url: Optional[NonEmptyStr] = None
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    summary: NonEmptyStr

    @field_validator("raw_path")
    @classmethod
    def _raw_path_under_artifacts(cls, v: str) -> str:
        # Lint #49 caught this drift; here it's enforced at the type level.
        if not re.match(r"^raw/[^/]+/artifacts/[^/]+\.md$", v):
            raise ValueError(
                f"raw_path must match raw/<cat>/artifacts/<slug>.md, got: {v!r}"
            )
        return v

    @field_validator("source_type")
    @classmethod
    def _source_type_has_raw(cls, v: SourceType) -> SourceType:
        if v not in _SOURCE_TYPES_WITH_RAW:
            raise ValueError(
                f"source_type={v.value!r} cannot use StandardWikiPage shape "
                f"(no raw source). Use SynthesisWikiPage instead."
            )
        return v


class MergedWikiPage(_WikiBase):
    """A wiki page that combines MULTIPLE raw sources into one synthesis.

    Created by `kb merge`. Uses `raw_paths` (list) and `urls` (list)
    instead of singular forms. ~5% of pages.
    """

    source_type: SourceType
    raw_paths: list[NonEmptyStr] = Field(min_length=2)
    urls: list[NonEmptyStr] = Field(min_length=2)
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    summary: NonEmptyStr

    @field_validator("raw_paths")
    @classmethod
    def _all_under_artifacts(cls, v: list[str]) -> list[str]:
        for path in v:
            if not re.match(r"^raw/[^/]+/artifacts/[^/]+\.md$", path):
                raise ValueError(
                    f"raw_paths entries must match raw/<cat>/artifacts/<slug>.md, "
                    f"got: {path!r}"
                )
        return v

    @model_validator(mode="after")
    def _lists_same_length(self) -> "MergedWikiPage":
        if len(self.raw_paths) != len(self.urls):
            raise ValueError(
                f"raw_paths ({len(self.raw_paths)}) and urls ({len(self.urls)}) "
                f"must have the same length — each raw must correspond to a url."
            )
        return self


class SynthesisWikiPage(_WikiBase):
    """A wiki page authored by the LLM with no underlying raw source.

    Includes entity pages (people/orgs/tools), topic syntheses,
    polished insights, and comparisons. These cite OTHER wiki pages
    via `related:` but have no raw artifact of their own.
    """

    source_type: Literal[
        SourceType.ENTITY, SourceType.TOPIC, SourceType.INSIGHT,
        SourceType.COMPARISON, SourceType.JOURNAL,
    ]
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    summary: NonEmptyStr


class RedirectStub(BaseModel):
    """A redirect placeholder left by `kb rename` so old wikilinks still
    resolve. Only three fields. Detected by presence of `redirect: true`.

    `extra="allow"` because some parsers leave bonus whitespace fields
    (e.g., trailing empty `tags:`) that don't break the redirect contract.
    The redirect field accepts True or "true" — read_frontmatter doesn't
    coerce YAML booleans."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    title: NonEmptyStr
    redirect: Union[bool, str]
    redirect_to: NonEmptyStr

    @field_validator("redirect")
    @classmethod
    def _redirect_must_be_truthy(cls, v: Union[bool, str]) -> bool:
        if v is True:
            return True
        if isinstance(v, str) and v.strip().lower() == "true":
            return True
        raise ValueError(f"redirect must be true (got {v!r})")


# ─── Discriminator: which shape is this frontmatter? ─────────────────


class WikiShape(str, Enum):
    """The four valid wiki frontmatter shapes."""

    STANDARD = "standard"
    MERGED = "merged"
    SYNTHESIS = "synthesis"
    REDIRECT = "redirect"


class AmbiguousShapeError(ValueError):
    """Raised when a frontmatter dict has mutually-exclusive shape markers.

    Pre-fix, detect_wiki_shape silently picked one shape and the other
    fields were absorbed by extra='allow' on the Pydantic models — drift
    could persist invisibly. Now we surface it explicitly so callers can
    decide whether to repair the data or refuse to write."""


def detect_wiki_shape(fm_dict: dict) -> WikiShape:
    """Return the shape this frontmatter dict claims to be — purely
    based on which fields are present, not on whether they validate.

    Raises AmbiguousShapeError if the dict has mutually-exclusive shape
    markers (e.g., both `raw_path` AND `raw_paths`, or `redirect: true`
    plus content fields). Callers that need a best-effort answer can
    catch the exception and fall back to a default."""
    redirect_val = fm_dict.get("redirect")
    is_redirect = redirect_val is True or (
        isinstance(redirect_val, str) and redirect_val.lower() == "true"
    )

    has_singular_raw = "raw_path" in fm_dict
    has_plural_raw = "raw_paths" in fm_dict
    has_singular_url = "url" in fm_dict
    has_plural_url = "urls" in fm_dict
    has_content_field = (
        has_singular_raw or has_plural_raw or has_singular_url or has_plural_url
        or "source_type" in fm_dict
    )

    if is_redirect and has_content_field:
        raise AmbiguousShapeError(
            "page has redirect:true alongside content fields "
            "(raw_path/url/source_type) — a redirect stub is a pointer, "
            "not a content page; one shape must be removed"
        )
    if (has_singular_raw and has_plural_raw) or (has_singular_url and has_plural_url):
        raise AmbiguousShapeError(
            "page has both singular (raw_path/url) AND list (raw_paths/urls) "
            "fields — pick one shape; merged uses lists, standard uses singulars"
        )

    if is_redirect:
        return WikiShape.REDIRECT
    if has_plural_raw or has_plural_url:
        return WikiShape.MERGED
    source_type = fm_dict.get("source_type", "")
    try:
        st = SourceType(source_type)
    except ValueError:
        # Unknown source_type — assume STANDARD so the validator complains
        # with a useful error.
        return WikiShape.STANDARD
    if st in _SOURCE_TYPES_WITH_RAW:
        return WikiShape.STANDARD
    return WikiShape.SYNTHESIS


_SHAPE_MODELS: dict[WikiShape, type[BaseModel]] = {
    WikiShape.STANDARD: StandardWikiPage,
    WikiShape.MERGED: MergedWikiPage,
    WikiShape.SYNTHESIS: SynthesisWikiPage,
    WikiShape.REDIRECT: RedirectStub,
}


def model_for_shape(shape: WikiShape) -> type[BaseModel]:
    """Lookup the Pydantic model for a given shape."""
    return _SHAPE_MODELS[shape]


# ─── Self-test ───────────────────────────────────────────────────────


if __name__ == "__main__":
    # Standard repo page (the most common shape)
    standard = {
        "title": "GitHub: skills — anthropics",
        "source_type": "repo",
        "raw_path": "raw/repos/artifacts/github-com-anthropics-skills.md",
        "url": "https://github.com/anthropics/skills",
        "date_added": "2026-05-08",
        "last_updated": "2026-05-09",
        "tags": ["ai-agents", "claude-code"],
        "related": ['[[Some Page]]'],
        "summary": "A repo of agent skills.",
        "aliases": ["skills"],  # extra field — should be tolerated
        "discovered_via": "[[Some Other Page]]",
    }
    assert detect_wiki_shape(standard) == WikiShape.STANDARD
    StandardWikiPage(**standard)

    # Merged page
    merged = {
        "title": "Stanford CS229",
        "source_type": "repo",
        "raw_paths": [
            "raw/repos/artifacts/afshinea--stanford.md",
            "raw/webpages/artifacts/drive-google.md",
        ],
        "urls": [
            "https://github.com/afshinea/stanford",
            "https://drive.google.com/file/d/abc/view",
        ],
        "date_added": "2026-04-06",
        "last_updated": "2026-04-07",
        "tags": ["ml"],
        "related": [],
        "summary": "Combined notes.",
    }
    assert detect_wiki_shape(merged) == WikiShape.MERGED
    MergedWikiPage(**merged)

    # Synthesis page
    synth = {
        "title": "AI Agents",
        "source_type": "topic",
        "date_added": "2026-04-01",
        "last_updated": "2026-04-15",
        "tags": ["ai-agents"],
        "related": ['[[Foo]]', '[[Bar]]'],
        "summary": "Topic page.",
    }
    assert detect_wiki_shape(synth) == WikiShape.SYNTHESIS
    SynthesisWikiPage(**synth)

    # Redirect stub
    redirect = {
        "title": "Old Page Name",
        "redirect": True,
        "redirect_to": "New Page Name",
    }
    assert detect_wiki_shape(redirect) == WikiShape.REDIRECT
    RedirectStub(**redirect)

    # Raw frontmatter
    raw = {
        "title": "anthropics/skills",
        "source": "https://github.com/anthropics/skills",
        "captured_at": "2026-05-09T19:08:02Z",
        "clipped_via": "kb-capture",
        "clipped_at": "2026-05-09",
        "stars": "131080",  # extra field — tolerated
        "language": "Python",
    }
    RawFrontmatter(**raw)

    # Negative: standard page with bad raw_path
    bad = dict(standard, raw_path="raw/repos/github-com-anthropics-skills.md")  # missing artifacts/
    try:
        StandardWikiPage(**bad)
    except Exception as e:
        assert "artifacts" in str(e)
    else:
        raise AssertionError("expected raw_path validator to reject")

    # Negative: synthesis page using StandardWikiPage shape
    try:
        StandardWikiPage(**dict(standard, source_type="topic"))
    except Exception as e:
        assert "topic" in str(e) or "no raw source" in str(e).lower()
    else:
        raise AssertionError("expected source_type validator to reject")

    print("All schema self-tests passed.")
