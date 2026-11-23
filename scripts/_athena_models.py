"""Model ids and cold-start budgets for Athena's Python scripts, read from the
vendored Gryphon registry (vendor/gryphon/packages/provider-config/dist).

Athena dev issue #182: the bulk regen scripts hardcoded claude-opus-4-7, and
_athena_timeout kept its own copy of Gryphon's cold-start table. Both went
stale: Gryphon 2.13.0 retired those ids. Reading the registry means the next
Gryphon bump updates the scripts with no Athena change.

The registry is JavaScript, so this asks `node` (already required: the
scripts drive `claude`, a node CLI). Failures raise RegistryUnavailable;
nothing falls back to a stale literal.
"""
from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DIST = _REPO / "vendor" / "gryphon" / "packages" / "provider-config" / "dist"

_JS = r"""
const [dist, alias] = process.argv.slice(1);
const r = require(dist + "/registry.js");
const a = require(dist + "/pricing/anthropic.js");
const base = String(alias).replace(/\[1m\]$/, "");
let id = a.resolveModel(base);
id = r.retiredModelSuccessor(id) || id;
process.stdout.write(JSON.stringify({ id, coldStartMs: r.coldStartMsFor(id) }));
"""

_ALIAS = re.compile(r"^[a-z0-9][a-z0-9.\-]*(\[1m\])?$")


class RegistryUnavailable(RuntimeError):
    pass


@functools.lru_cache(maxsize=None)
def _lookup(alias: str) -> dict:
    if not _ALIAS.match(alias or ""):
        raise ValueError(f"not a model alias or id: {alias!r}")
    node = shutil.which("node")
    if not node:
        raise RegistryUnavailable("node is not on PATH; it reads Gryphon's model registry")
    if not (_DIST / "registry.js").is_file():
        raise RegistryUnavailable(f"Gryphon's model registry is missing: {_DIST}/registry.js "
                                  "(run `git submodule update --init vendor/gryphon`)")
    try:
        out = subprocess.run([node, "-e", _JS, str(_DIST), alias], capture_output=True,
                             text=True, timeout=20, check=True).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, ValueError) as e:
        raise RegistryUnavailable(f"reading Gryphon's model registry failed: {e}") from e
    if not isinstance(data, dict) or not str(data.get("id", "")).startswith("claude-"):
        raise RegistryUnavailable(f"registry gave no Claude id for {alias!r}: {data!r}")
    return data


def claude_model(alias: str) -> str:
    """The current Claude id for an alias ("opus", "sonnet", "haiku") or a
    possibly-retired id; with Gryphon 2.13.0, opus resolves to claude-opus-5-5."""
    return _lookup(alias)["id"]


def cold_start_seconds(model: str) -> int | None:
    """Gryphon's cold-start budget for `model` in seconds, or None when the
    registry has none for it."""
    ms = _lookup(model).get("coldStartMs")
    return int(ms) // 1000 if isinstance(ms, (int, float)) and ms > 0 else None
