"""Athena config loader — single source of truth for paths, naming conventions,
markers, and platform rules.

Two-file model:

  1. `bin/config/athena.default.json`  — CANONICAL, ships with the release.
     Never edited by users in normal operation. Integrity-checked on load.
  2. `bin/config/athena.json`           — OPTIONAL user override. Missing or
     malformed → loader warns and proceeds with defaults only.

Deep-merged at startup. Override values win where present; default values
fill the rest.

Design principles:

- The config file is for runtime tweaks that do NOT invalidate data layout —
  Web Clipper watch folders, custom platform prefixes, auto-marker strings.
- Structural changes (directory layout, category additions) are data
  migrations and ship as dedicated migration scripts, not config edits.
- If `athena.default.json` is missing or unreadable, the installation is
  broken. The loader raises with a clear recovery hint rather than silently
  falling back — better to fail loud than drift quietly.

Integrity check:

A SHA256 of the released default is embedded below. On load, the loader
compares the shipped file's hash against this value. Mismatch is logged as
a warning (not a hard error — legitimate dev edits happen) but tells us
immediately when diagnosing user reports: "was their config tampered with?"
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


# SHA256 of the canonical bin/config/athena.default.json at release time.
# Release checklist step: recompute via `shasum -a 256 bin/config/athena.default.json`
# whenever the default file is intentionally updated.
_EXPECTED_DEFAULT_SHA256 = "43aac591f902d550a81c7cdae85528a2efe1bba86cd36ee06e3a4fb706850048"

# The schema version the running code was written against. Mismatches log a
# warning so users know the override they're merging may be stale.
_EXPECTED_SCHEMA_VERSION = 1

_DEFAULT_REL = "bin/config/athena.default.json"
_OVERRIDE_REL = "bin/config/athena.json"


class ConfigError(RuntimeError):
    """Raised when the canonical default config cannot be loaded."""


# ── File discovery ───────────────────────────────────────────────────────

def _find_file(rel: str) -> Path | None:
    """Locate a config file. Search order:
      1. Alongside this module's parent (bin/lib → bin/config/...)
      2. Walk up from cwd looking for `bin/config/...`
    Returns None if not found.
    """
    here = Path(__file__).resolve().parent.parent / rel.split("bin/")[1]
    if here.is_file():
        return here
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / rel
        if candidate.is_file():
            return candidate
    return None


def _default_path() -> Path:
    p = _find_file(_DEFAULT_REL)
    if p is None:
        raise ConfigError(
            f"Required config file not found: {_DEFAULT_REL}. "
            f"Your Athena installation appears broken. Restore via "
            f"`git checkout {_DEFAULT_REL}` or reinstall."
        )
    return p


def _override_path() -> Path | None:
    env = os.environ.get("ATHENA_CONFIG")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    return _find_file(_OVERRIDE_REL)


# ── Integrity + schema checks ────────────────────────────────────────────

def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_matches_git_head(path: Path) -> bool:
    """Return True iff `path` is tracked by git and matches HEAD's blob
    (no working-tree modifications). Returns False if not in a git repo,
    not tracked, or has unstaged edits.

    Used to suppress the default-config integrity warning for legitimate
    committed dev edits — if the file matches HEAD, the user's repo is
    in a deliberate state, even if its hash differs from the release
    baseline. The warning still fires when the working tree has
    uncommitted edits AND the hash doesn't match the baseline (i.e. an
    in-progress edit a release-checkout wouldn't have).
    """
    try:
        # `git diff --quiet HEAD -- path` exits 0 if no diff, 1 if diff,
        # other for repo-not-found / file-not-tracked. Suppress stderr
        # so 'fatal: not a git repository' doesn't leak to the user.
        result = subprocess.run(
            ["git", "-C", str(path.parent), "diff", "--quiet", "HEAD", "--", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # git missing, slow filesystem, or subprocess setup failure —
        # fall through and let the hash-mismatch path decide.
        return False


def _verify_default_integrity(path: Path) -> None:
    actual = _sha256_of(path)
    if actual == _EXPECTED_DEFAULT_SHA256:
        return
    # Hash differs from release baseline. Suppress warning if the file is
    # tracked-and-clean in git (i.e. matches HEAD) — that's a legitimate
    # committed dev edit, not a working-tree corruption. Only warn when
    # the working tree has uncommitted edits or git isn't usable.
    if _file_matches_git_head(path):
        return
    logger.warning(
        "athena.default.json hash mismatch: expected %s, got %s. "
        "File has been modified from the released default — often a "
        "legitimate dev edit, but can indicate corruption. To restore: "
        "`git checkout %s`",
        _EXPECTED_DEFAULT_SHA256[:12] + "...",
        actual[:12] + "...",
        path,
    )


def _verify_schema_version(cfg: dict, source: str) -> None:
    v = cfg.get("version")
    if v is None:
        logger.warning("%s is missing `version` field — expected %d", source, _EXPECTED_SCHEMA_VERSION)
    elif v > _EXPECTED_SCHEMA_VERSION:
        logger.warning(
            "%s declares version %s but this code understands up to %s. "
            "Newer fields will be ignored.",
            source, v, _EXPECTED_SCHEMA_VERSION,
        )


# ── Deep merge (override over default) ───────────────────────────────────

def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge. Lists from overlay replace base's list entirely
    (we don't want to concatenate e.g. watch-folder lists — user expresses
    full intent). Scalars from overlay replace base's scalar."""
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ── Public API ───────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load() -> dict:
    """Load the canonical config, optionally layering a user override on top.

    Raises ConfigError only if the canonical default file cannot be found or
    parsed — that's a broken install, not a recoverable condition.
    """
    default_path = _default_path()
    _verify_default_integrity(default_path)
    try:
        with open(default_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"Could not parse {default_path}: {e}. "
            f"Restore via `git checkout {_DEFAULT_REL}`."
        ) from e
    _verify_schema_version(cfg, str(default_path))

    override_path = _override_path()
    if override_path is not None:
        try:
            with open(override_path, encoding="utf-8") as f:
                overlay = json.load(f)
            _verify_schema_version(overlay, str(override_path))
            cfg = _deep_merge(cfg, overlay)
            logger.info("Merged override from %s", override_path)
        except json.JSONDecodeError as e:
            logger.warning(
                "Override file %s is malformed (%s). Using defaults only. "
                "Fix the syntax or delete the file to silence this warning.",
                override_path, e,
            )
        except OSError as e:
            logger.warning("Could not read override %s (%s). Using defaults only.",
                           override_path, e)

    return cfg


# ── Convenience views ────────────────────────────────────────────────────

def _cfg() -> dict:
    return load()


def raw_categories() -> dict:
    return _cfg()["raw"]["categories"]


def raw_dir(category: str) -> str:
    """Path (vault-relative) for a raw category, including the optional
    artifacts subdirectory. When `artifacts_subdir` is set (Phase 2+), this
    returns `raw/<cat>/artifacts` automatically."""
    cat = raw_categories()[category]
    subdir = _cfg()["raw"].get("artifacts_subdir", "")
    return os.path.join(cat["dir"], subdir) if subdir else cat["dir"]


def source_type_to_category() -> dict:
    return {c["source_type"]: name for name, c in raw_categories().items()}


def category_for_source_type(source_type: str) -> str | None:
    return source_type_to_category().get(source_type)


def raw_dir_for_source_type(source_type: str) -> str | None:
    cat = category_for_source_type(source_type)
    return raw_dir(cat) if cat else None


def wiki_format_dir(category: str) -> str:
    return os.path.join(_cfg()["wiki"]["format_root"], category)


def marker(kind: str) -> tuple[str, str]:
    m = _cfg()["auto_markers"][kind]
    return m["start"], m["end"]


def naming() -> dict:
    return _cfg()["naming"]


def system_files() -> dict:
    return _cfg()["system_files"]


def inbox() -> dict:
    return _cfg()["inbox"]


PATHS = _cfg
