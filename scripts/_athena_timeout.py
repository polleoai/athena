"""Idle-timeout helper for Athena bulk-LLM scripts.

Mirrors Gryphon's connection-timeout setting (vendor/gryphon/src/constants.js
COLD_START_BUDGET_MS + resolveConnectionTimeoutMs) so bulk-regen scripts
respect the same single knob as Obsidian chat.

Behavior summary:

* Reads `connectionTimeoutMs` from `<vault>/.obsidian/plugins/athena/data.json`.
  If present and within [5s, 600s], it overrides the model-adaptive default.
* Otherwise falls back to model-adaptive defaults that match Gryphon's table:
  haiku 30s, sonnet 60s, opus 120s, opus[1m] 180s; everything else 60s.
* `run_claude_with_idle_timeout()` runs `claude -p` with stream-json so each
  output event acts as a heartbeat — long-but-alive runs don't trip the
  watchdog. Total wallclock is unbounded; only stalls fire the timeout.

Why not use `subprocess.run(timeout=...)`: that's total wallclock. Opus on
slow networks can produce a healthy 90s response; a 60s wallclock kills
the run mid-stream and the user sees a timeout for what is actually a
working call. Idle-timeout matches Gryphon's UX.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

# Model-adaptive budgets come from Gryphon's registry (coldStartMsFor) via
# _athena_models (#182). This file used to mirror Gryphon's table by hand,
# and its ids (claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-…) went
# stale when Gryphon 2.13.0 retired them.
_DEFAULT_TIMEOUT_S = 60
_MIN_TIMEOUT_S = 5
_MAX_TIMEOUT_S = 600


def _find_vault_root(start: Path) -> Path:
    """Walk up from a starting path looking for a `.obsidian/` directory.

    Athena scripts always live at `<vault>/scripts/`, but symlinks and
    worktrees can break that assumption — searching for the marker keeps
    the helper robust to layout changes.
    """
    cur = start.resolve()
    for _ in range(8):
        if (cur / ".obsidian").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve().parent.parent


def resolve_timeout_seconds(model: str, vault: Path | None = None) -> int:
    """Return the idle-timeout budget (seconds) for a given model.

    Resolution order: Obsidian setting override → model-adaptive default →
    `_DEFAULT_TIMEOUT_S`. Out-of-range overrides are silently ignored,
    matching the JS-side `resolveConnectionTimeoutMs` semantics.
    """
    if vault is None:
        vault = _find_vault_root(Path(__file__))
    data_file = vault / ".obsidian" / "plugins" / "athena" / "data.json"
    try:
        if data_file.is_file():
            cfg = json.loads(data_file.read_text(encoding="utf-8"))
            override_ms = cfg.get("connectionTimeoutMs")
            if isinstance(override_ms, (int, float)) and override_ms > 0:
                override_s = int(round(override_ms / 1000))
                if _MIN_TIMEOUT_S <= override_s <= _MAX_TIMEOUT_S:
                    return override_s
    except (OSError, json.JSONDecodeError):
        # Missing / corrupt data.json shouldn't break a long batch run.
        # Fall through to the model-adaptive default.
        pass
    try:
        from _athena_models import cold_start_seconds, RegistryUnavailable
    except ImportError:  # a caller that put only this file on sys.path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _athena_models import cold_start_seconds, RegistryUnavailable
    try:
        budget = cold_start_seconds(model)
    except (RegistryUnavailable, ValueError) as e:
        print(f"_athena_timeout: {e}; using the {_DEFAULT_TIMEOUT_S}s default for {model!r}",
              file=sys.stderr)
        return _DEFAULT_TIMEOUT_S
    if budget is None:
        return _DEFAULT_TIMEOUT_S
    return max(_MIN_TIMEOUT_S, min(_MAX_TIMEOUT_S, budget))


def run_claude_with_idle_timeout(
    cmd: list[str],
    idle_timeout_s: int,
) -> tuple[int, str, str]:
    """Run a command (typically `claude -p ...`) with an idle watchdog.

    The watchdog resets on every byte the child writes to stdout or
    stderr. The child is killed only after `idle_timeout_s` seconds of
    silence — total wallclock is unbounded.

    Returns (returncode, raw_stdout, raw_stderr). On idle timeout, raises
    TimeoutError after killing the child; partial output is attached to
    the exception's `args[1]` and `args[2]` so callers can log it.

    The caller is responsible for adding `--output-format stream-json
    --verbose` (or similar streaming flag) to the command if they want
    genuine first-token semantics. For text-mode `claude -p`, output is
    buffered and the watchdog effectively measures total wallclock.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered so each event resets the timer promptly
    )
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    last_activity = [time.monotonic()]
    lock = threading.Lock()

    def _drain(stream, chunks):
        try:
            for line in iter(stream.readline, ""):
                with lock:
                    chunks.append(line)
                    last_activity[0] = time.monotonic()
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()

    try:
        while proc.poll() is None:
            with lock:
                idle_for = time.monotonic() - last_activity[0]
            if idle_for > idle_timeout_s:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                t_out.join(timeout=2)
                t_err.join(timeout=2)
                raise TimeoutError(
                    f"No output from claude for {idle_timeout_s}s "
                    f"(idle timeout — total wallclock is unbounded). "
                    f"Raise the Connection timeout setting if your model needs longer.",
                    "".join(out_chunks),
                    "".join(err_chunks),
                )
            time.sleep(0.5)
        # Process exited cleanly — give drainers a moment to flush.
        t_out.join(timeout=2)
        t_err.join(timeout=2)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    return proc.returncode, "".join(out_chunks), "".join(err_chunks)


def extract_text_from_stream_json(raw_stdout: str) -> str:
    """Pull the final assistant text out of a stream-json transcript.

    `claude -p --output-format stream-json --verbose` emits one JSON
    object per line. The terminal `result` event contains the final
    text in its `result` field — that's what we want. If no `result`
    event is present (truncated stream, error mid-flight), fall back
    to concatenating any text deltas we can find.
    """
    final_result: str | None = None
    text_deltas: list[str] = []
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev_type = ev.get("type")
        if ev_type == "result":
            # The terminal event — `result` holds the final string.
            final_result = ev.get("result") or final_result
        elif ev_type == "assistant":
            # Partial deltas — collected as a fallback for truncated streams.
            msg = ev.get("message") or {}
            for block in (msg.get("content") or []):
                if block.get("type") == "text":
                    txt = block.get("text") or ""
                    if txt:
                        text_deltas.append(txt)
    if final_result is not None:
        return final_result
    return "".join(text_deltas)


def extract_result_event(raw_stdout: str) -> dict | None:
    """The last `result` event of a `claude -p` stream-json transcript, or
    None. It carries subtype ("success", "error_max_budget_usd", ...) and
    total_cost_usd (witnessed with Claude Code 2.1.326, 2026-11-19)."""
    found: dict | None = None
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            found = ev
    return found


def extract_structured_output(raw_stdout: str) -> dict | None:
    """The schema-validated object from a `claude -p --json-schema …`
    stream-json transcript, or None.

    With --json-schema the terminal `result` event carries the validated
    object in `structured_output` (witnessed with Claude Code 2.1.326,
    2026-11-19), beside the same JSON as text in `result`. None when the
    event or the field is missing, or isn't an object: the caller then
    parses the text (#181's soft landing).
    """
    found: dict | None = None
    for line in raw_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            so = ev.get("structured_output")
            found = so if isinstance(so, dict) else None
    return found


# #179: a cost ceiling for the bulk sweeps (bulk-llm-regen-summaries.py,
# bulk-llm-regen-tags.py). Each call passes what is left as its own
# --max-budget-usd and is charged its total_cost_usd.
DEFAULT_MAX_USD = 10.0


class BudgetExceeded(Exception):
    """The sweep's --max-usd is spent, or a call's cost couldn't be read.
    Not a RuntimeError: the retry loop must not swallow it."""


class Budget:
    def __init__(self, cap: float):
        if not cap > 0:
            raise ValueError(f"--max-usd must be > 0, got {cap}")
        self.cap, self.spent = float(cap), 0.0

    def remaining(self) -> float:
        return self.cap - self.spent

    def check(self) -> None:
        if self.remaining() <= 0:
            raise BudgetExceeded(f"spent ${self.spent:.4f} of the ${self.cap:.2f} cap")

    def charge(self, raw_out: str) -> dict:
        """Add the call's total_cost_usd; return its result event."""
        ev = extract_result_event(raw_out) or {}
        cost = ev.get("total_cost_usd")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
            raise BudgetExceeded(f"the call's cost is unreadable ({cost!r}), so the cap can't be "
                                 f"enforced; spent ${self.spent:.4f} before it")
        self.spent += cost
        if ev.get("subtype") == "error_max_budget_usd":
            raise BudgetExceeded(f"the CLI stopped at its budget; spent ${self.spent:.4f} "
                                 f"of the ${self.cap:.2f} cap")
        return ev
