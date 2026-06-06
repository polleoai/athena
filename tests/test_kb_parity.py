"""Parity: bin/kb (Python) must produce identical stdout + exit code to
bin/kb-legacy (Bash) for every ported command.

This is the gate that lets the Bash->Python port never change macOS/Linux
behavior: a command is cut over from the legacy-delegating path to a native
Python handler only once its parity case here stays green.

Modeled on tests/test_pipeline_parity.py. Self-skips on Windows (the legacy
Bash can't run there -- Windows correctness is proven by the argus VM matrix
instead).

Each case runs BOTH entry points against the SAME fresh throwaway vault and
compares normalized stdout + exit code. `bin/` is COPIED into the vault (not
symlinked) so both entries resolve the same KB_ROOT: the Python dispatcher's
Path.resolve() follows symlinks, which a symlinked bin/ would send back to the
real repo, while Bash's logical `cd ..` would not -- they must agree.

Run from vault root:
    python3 -m pytest tests/test_kb_parity.py -v
    python3 -m unittest tests.test_kb_parity -v
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _normalize(text: str, vault: str) -> str:
    """Strip the only legitimately-variable parts of kb output so two runs
    against different temp vaults compare equal: the absolute vault path and
    ISO timestamps. Everything else must match byte-for-byte."""
    # Mask the vault path in BOTH the form the test created it (logical, e.g.
    # /tmp/...) and its realpath form (e.g. /private/tmp/... on macOS, where
    # /tmp is a symlink). bin/kb resolves its own root via Path.resolve(), so a
    # command that echoes an absolute path inside the vault (e.g. lint's
    # index.md read error) prints the realpath form, while bin/kb-legacy's
    # logical `pwd` prints the un-resolved form. Both denote the same vault.
    real = os.path.realpath(vault)
    if real != vault:
        text = text.replace(real, "<VAULT>")
    text = text.replace(vault, "<VAULT>")
    text = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", "<TS>", text)
    # `kb undo` echoes the trash manifest's local-time stamp ("2026-06-01
    # 12:42:21"), a space-separated (non-ISO) form. Two runs a clock-tick apart
    # would otherwise differ purely on this wall-clock value.
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<TS>", text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _make_vault() -> str:
    """A throwaway vault with the canonical skeleton and a full copy of bin/."""
    vault = tempfile.mkdtemp(prefix="kb-parity-")
    for sub in (
        "raw/webpages/artifacts",
        "raw/repos/artifacts",
        "raw/papers/artifacts",
        "wiki/format/webpages",
        "wiki/topics",
        "wiki/entities",
        "inbox/Clippings",
    ):
        os.makedirs(os.path.join(vault, sub), exist_ok=True)
    Path(vault, "CLAUDE.md").write_text("# test vault\n")
    Path(vault, "RULES.md").write_text("# RULES\n\n(test vault)\n")
    shutil.copytree(ROOT / "bin", os.path.join(vault, "bin"))
    return vault


def _run(entry: str, args: list[str], vault: str) -> tuple[int, str]:
    """entry: 'kb' (Python) or 'kb-legacy' (Bash). Returns (exit, normalized stdout)."""
    script = os.path.join(vault, "bin", entry)
    cmd = ([sys.executable, script, *args] if entry == "kb"
           else ["/bin/bash", script, *args])
    proc = subprocess.run(cmd, cwd=vault, capture_output=True, text=True)
    return proc.returncode, _normalize(proc.stdout, vault)


def _tree_snapshot(vault: str) -> dict[str, str]:
    """Capture the post-command vault state as {logical-key: content} so two
    runs (Bash in v1, Python in v2) can be compared for byte-identical EFFECT,
    not just stdout. Covers the durable layers a kb mutation touches:
      - wiki/ and raw/ .md files (the knowledge layer)
      - .athena/ exports (kb export's binary/text outputs)
      - .kb-trash/ snapshot dirs (soft-delete + undo manifests)

    Two normalizations make the comparison meaningful across runs:
      * Date/timestamp values inside file CONTENT are masked (the same
        `<TS>`/`<D>` masks `_normalize` applies to stdout) -- date_added,
        manifest timestamps, etc. are genuinely wall-clock and would differ
        a clock-tick apart.
      * .kb-trash/ dir names embed a `YYYYMMDD_HHMMSS_<microsec>_<op>` stamp.
        A command may create SEVERAL snapshot dirs in one run (e.g. ungroup
        snapshots the modify, then trashes the hub). Masking the stamp to a
        constant would COLLIDE those dirs onto one key and silently drop all
        but one. Instead each trash dir is keyed by its ORDINAL position after
        sorting by real name -- TRASH0:, TRASH1:, ... -- so N dirs map to N
        stable, distinct keys and their manifests are compared pairwise.
    """
    import glob
    import json
    import re

    def _mask(content: str) -> str:
        content = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", "<TS>", content)
        content = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<TS>", content)
        # Minute-precision wall-clock (no seconds) -- the dashboards regenerated
        # on every `kb add` stamp `Updated YYYY-MM-DD HH:MM` (URL Tracker, All
        # Pages index). Two runs a clock-tick apart straddle a minute boundary;
        # this is genuinely-volatile wall-clock, not behavior. Mask BEFORE the
        # bare-date rule so the date half isn't consumed first.
        content = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", "<TS>", content)
        content = re.sub(r"\d{4}-\d{2}-\d{2}", "<D>", content)
        return content

    def _canon_manifest(content: str) -> str:
        """A trash manifest's deleted/modified/created arrays are written from
        `list(set(...))`, whose iteration order is randomized per process by
        PYTHONHASHSEED. The SET of files is the behavior; the order is not.
        Sort those arrays so two processes' manifests compare on content, not
        on a hash-seed accident. (Confirmed not a behavioral divergence: the
        Bash and Python handlers run identical `list(set(...))` code.)"""
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return content
        for key in ("deleted", "modified", "created"):
            if isinstance(data.get(key), list):
                data[key] = sorted(data[key])
        return json.dumps(data, indent=2, sort_keys=True)

    out: dict[str, str] = {}
    # `inbox/` is deliberately NOT snapshotted. The ingest arms regenerate two
    # dashboards into inbox/ on every run -- `URL Tracker.md` and (via the
    # page-index) `All Pages`. URL Tracker's "All URLs" section sorts rows by
    # their capture WALL-CLOCK SECOND (descending); when two captures in one
    # run straddle a second boundary the row order flips, even though the row
    # SET, the url-resolved.tsv contents, and the TSV row order are byte-
    # identical between Bash and Python (verified). That ordering is volatile
    # wall-clock, not orchestration behavior, so snapshotting inbox/ would
    # produce spurious diffs. The visible inbox bookkeeping (Found N URLs,
    # Processed N, TSV append) is already covered by the stdout assertion.
    for base in ("wiki", "raw", ".athena"):
        for f in glob.glob(os.path.join(vault, base, "**", "*"), recursive=True):
            if os.path.isfile(f):
                rel = os.path.relpath(f, vault)
                try:
                    out[rel] = _mask(Path(f).read_text(errors="replace"))
                except (IsADirectoryError, OSError):
                    pass
    # .kb-trash/ snapshot dirs -- keyed by ordinal, not by their volatile stamp.
    trash_dirs = sorted(glob.glob(os.path.join(vault, ".kb-trash", "*")))
    for i, d in enumerate(trash_dirs):
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "**", "*"), recursive=True):
            if os.path.isfile(f):
                rel = os.path.relpath(f, d)
                content = _mask(Path(f).read_text(errors="replace"))
                if os.path.basename(f) == "manifest.json":
                    content = _canon_manifest(content)
                out[f"TRASH{i}:{rel}"] = content
    return out


@unittest.skipIf(os.name == "nt", "legacy Bash can't run on Windows")
class KbParity(unittest.TestCase):
    def assert_parity(self, args: list[str]) -> None:
        vault = _make_vault()
        try:
            legacy_code, legacy_out = _run("kb-legacy", args, vault)
            py_code, py_out = _run("kb", args, vault)
            self.assertEqual(legacy_code, py_code,
                             f"exit code differs for {args!r}")
            self.assertEqual(legacy_out, py_out,
                             f"stdout differs for {args!r}")
        finally:
            shutil.rmtree(vault, ignore_errors=True)

    def test_help(self):
        # `help` is now a native handler (cmd_help) -- its output must stay
        # byte-identical to the Bash `help|--help|-h)` arm.
        self.assert_parity(["help"])

    def test_help_no_args(self):
        # Bare `kb` defaults cmd -> "help" in both entry points.
        self.assert_parity([])

    def test_help_long_flag(self):
        self.assert_parity(["--help"])

    def test_help_short_flag(self):
        self.assert_parity(["-h"])

    def test_unknown_command(self):
        # Phase 3 retirement: the dispatcher no longer execs bin/kb-legacy for
        # unmatched commands -- it reports natively. This must stay byte-identical
        # to the Bash `*)` default arm (the legacy script is the oracle here).
        self.assert_parity(["zzz-not-a-real-command"])

    # ── Phase 1, Batch 1 ─────────────────────────────────────
    def test_stats(self):
        self.assert_parity(["stats"])

    def test_list(self):
        self.assert_parity(["list"])

    def test_list_topics(self):
        self.assert_parity(["list", "--topics"])

    def test_list_entities(self):
        self.assert_parity(["list", "--entities"])

    def test_list_recent(self):
        self.assert_parity(["list", "--recent"])

    def test_rules(self):
        self.assert_parity(["rules"])

    def test_rules_add(self):
        self.assert_parity(["rules", "add", "some rule"])

    def test_journal(self):
        self.assert_parity(["journal", "a note"])

    def test_search(self):
        self.assert_parity(["search", "foo"])

    def test_query(self):
        self.assert_parity(["query", "what is X"])

    def test_index(self):
        self.assert_parity(["index"])

    def test_trash(self):
        self.assert_parity(["trash"])

    # ── Extra arg variants exercised by the Bash arms ──────────
    def test_search_no_arg(self):
        self.assert_parity(["search"])

    def test_query_no_arg(self):
        self.assert_parity(["query"])

    def test_list_unknown_filter(self):
        self.assert_parity(["list", "--bogus"])

    def test_journal_recent(self):
        self.assert_parity(["journal", "--recent"])

    def test_index_status(self):
        self.assert_parity(["index", "--status"])

    def test_rules_section(self):
        self.assert_parity(["rules", "--section", "Quality"])

    # ── Phase 1, Batch 2: lint / remove / delete / undo ──────────
    def assert_parity_2vault(self, args: list[str], seed=False) -> None:
        """Run BOTH entries against SEPARATE identical fresh vaults.

        Used for commands that MUTATE the vault (lint auto-fixes _Contents
        blocks; remove/delete move files to .kb-trash/): a single shared vault
        would let the first (legacy) run change state the second (python) run
        then sees, producing a spurious diff. Two identical vaults isolate each
        run so we compare the command's effect on the SAME starting state.

        `seed`: False (no seed), True (one page+raw via _seed_page), or the
        string "two_pages" (two pages+raws, for merge)."""
        v1 = _make_vault()
        v2 = _make_vault()
        try:
            if seed == "two_pages":
                self._seed_page(v1)
                self._seed_page(v2)
                self._seed_second_page(v1)
                self._seed_second_page(v2)
            elif seed:
                self._seed_page(v1)
                self._seed_page(v2)
            legacy_code, legacy_out = _run("kb-legacy", args, v1)
            py_code, py_out = _run("kb", args, v2)
            self.assertEqual(legacy_code, py_code,
                             f"exit code differs for {args!r}")
            self.assertEqual(legacy_out, py_out,
                             f"stdout differs for {args!r}")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    def test_lint(self):
        self.assert_parity_2vault(["lint"])

    def test_lint_seeded(self):
        # Lint a vault that actually contains a page + raw, so the many checks
        # that only fire on real content (frontmatter, raw_path, orphan, tags,
        # Connections, etc.) are exercised for parity -- not just empty-vault.
        self.assert_parity_2vault(["lint"], seed=True)

    def _seed_page(self, vault: str) -> None:
        """Seed an identical wiki page + backing raw into the temp vault, so a
        destructive command (remove/delete) has a real target. Mirrors the
        07-guardrail Part-B seed shape (raw artifact + linked wiki frontmatter)."""
        raw = os.path.join(vault, "raw", "webpages", "artifacts", "example-com-page.md")
        Path(raw).write_text(
            "---\n"
            "url: https://example.com/page\n"
            "title: Example Page\n"
            "---\n\n"
            "# Example Page\n\nSome captured body text.\n"
        )
        page = os.path.join(vault, "wiki", "format", "webpages", "Example Page.md")
        Path(page).write_text(
            "---\n"
            'title: "Example Page"\n'
            "url: https://example.com/page\n"
            'raw_path: "raw/webpages/artifacts/example-com-page.md"\n'
            "related: []\n"
            "---\n\n"
            "# Example Page\n\nSummary body.\n"
        )

    def _seed_second_page(self, vault: str) -> None:
        """A second identical-shape page+raw, so `kb merge` has two real
        sources to combine."""
        raw = os.path.join(vault, "raw", "webpages", "artifacts", "example-com-two.md")
        Path(raw).write_text(
            "---\n"
            "url: https://example.com/two\n"
            "title: Second Page\n"
            "---\n\n"
            "# Second Page\n\nMore captured body text.\n"
        )
        page = os.path.join(vault, "wiki", "format", "webpages", "Second Page.md")
        Path(page).write_text(
            "---\n"
            'title: "Second Page"\n'
            "url: https://example.com/two\n"
            'raw_path: "raw/webpages/artifacts/example-com-two.md"\n'
            "related: []\n"
            "---\n\n"
            "# Second Page\n\nSecond summary body.\n"
        )

    def test_remove_not_found(self):
        self.assert_parity(["remove", "Nonexistent Page", "--yes"])

    def test_delete_not_found(self):
        self.assert_parity(["delete", "Nonexistent Page", "--yes"])

    def test_delete_no_arg(self):
        self.assert_parity(["delete"])

    def test_remove_no_arg(self):
        self.assert_parity(["remove", "--yes"])

    def test_remove_help(self):
        self.assert_parity(["remove", "--help"])

    def test_remove_seeded(self):
        self.assert_parity_2vault(["remove", "Example Page", "--yes"], seed=True)

    def test_delete_seeded(self):
        self.assert_parity_2vault(["delete", "Example Page", "--yes"], seed=True)

    def test_undo_empty(self):
        self.assert_parity(["undo", "--yes"])

    def _assert_parity_delete_then_undo(self) -> None:
        """Seed identical page+raw into two separate identical vaults, delete it
        in each, then undo -- comparing the undo stdout/exit between Bash and
        Python. Separate vaults (not a shared one) because delete mutates state."""
        v1 = _make_vault()
        v2 = _make_vault()
        try:
            self._seed_page(v1)
            self._seed_page(v2)
            _run("kb-legacy", ["delete", "Example Page", "--yes"], v1)
            _run("kb", ["delete", "Example Page", "--yes"], v2)
            legacy_code, legacy_out = _run("kb-legacy", ["undo", "--yes"], v1)
            py_code, py_out = _run("kb", ["undo", "--yes"], v2)
            self.assertEqual(legacy_code, py_code, "undo exit code differs")
            self.assertEqual(legacy_out, py_out, "undo stdout differs")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    def test_undo_after_delete(self):
        self._assert_parity_delete_then_undo()

    # ── Phase 2: grouping / synthesis / maintenance ──────────────
    # Module-delegating: schema-check, migrate-raws, create.
    def test_schema_check(self):
        self.assert_parity_2vault(["schema-check"])

    def test_schema_check_seeded(self):
        self.assert_parity_2vault(["schema-check"], seed=True)

    def test_migrate_raws_dry_run(self):
        self.assert_parity_2vault(["migrate-raws", "--dry-run"], seed=True)

    def test_migrate_raws_seeded(self):
        self.assert_parity_2vault(["migrate-raws"], seed=True)

    def test_create_help(self):
        self.assert_parity(["create", "--help"])

    def test_create_no_name(self):
        self.assert_parity(["create"])

    def test_create_topic_seeded(self):
        # --yes skips the interactive prompt: deterministic full behavior,
        # delegating to kb_commands.py create (creates a topic hub page).
        self.assert_parity_2vault(["create", "My Hub", "--topic", "--yes"])

    def test_create_insight_seeded(self):
        self.assert_parity_2vault(["create", "My Finding", "--insight", "--yes"])

    # Heredoc-lift grouping ops: deterministic surfaces (--help, usage
    # errors, not-found) -- full state-mutation paths need real hub/child
    # pages and are covered by host/VM runs.
    def test_move_help(self):
        self.assert_parity(["move", "--help"])

    def test_move_no_args(self):
        self.assert_parity(["move"])

    def test_move_not_found(self):
        self.assert_parity(["move", "Page A", "--into", "Hub"])

    def test_move_unsafe_name(self):
        self.assert_parity(["move", "../escape", "--into", "Hub"])

    def test_ungroup_help(self):
        self.assert_parity(["ungroup", "--help"])

    def test_ungroup_no_args(self):
        self.assert_parity(["ungroup"])

    def test_ungroup_not_found(self):
        self.assert_parity(["ungroup", "Nonexistent Hub"])

    def test_merge_help(self):
        self.assert_parity(["merge", "--help"])

    def test_merge_too_few(self):
        self.assert_parity(["merge", "OnlyOne"])

    def test_merge_not_found(self):
        self.assert_parity(["merge", "Page A", "Page B"])

    def test_merge_seeded(self):
        # Two real pages -> a real merge: deterministic full behavior.
        self.assert_parity_2vault(
            ["merge", "Example Page", "Second Page", "--into", "Merged", "--yes"],
            seed="two_pages")

    def test_rename_help(self):
        self.assert_parity(["rename", "--help"])

    def test_rename_not_found(self):
        self.assert_parity(["rename", "Nonexistent", "--to", "New", "--yes"])

    def test_rename_seeded(self):
        self.assert_parity_2vault(
            ["rename", "Example Page", "--to", "Renamed Page", "--yes"], seed=True)

    def test_refresh_wiki_help(self):
        self.assert_parity(["refresh-wiki", "--help"])

    def test_refresh_wiki_no_arg(self):
        self.assert_parity(["refresh-wiki"])

    def test_refresh_wiki_not_found(self):
        self.assert_parity(["refresh-wiki", "Nonexistent Page"])

    def test_attach_help(self):
        self.assert_parity(["attach", "--help"])

    def test_attach_no_args(self):
        self.assert_parity(["attach"])

    def test_attach_not_found(self):
        self.assert_parity(["attach", "Page A", "--to", "Hub"])

    def test_detach_help(self):
        self.assert_parity(["detach", "--help"])

    def test_detach_no_args(self):
        self.assert_parity(["detach"])

    def test_detach_not_found(self):
        self.assert_parity(["detach", "Page A", "--from", "Hub"])

    def test_purge_empty(self):
        self.assert_parity(["purge"])

    def test_purge_days(self):
        self.assert_parity(["purge", "10"])

    # Index-dependent synthesis: deterministic no-index path. Full behavior
    # (BERTopic/SpaCy/networkx + populated index) deferred to host/VM.
    def test_topics_no_index(self):
        self.assert_parity(["topics"])

    def test_entities_no_index(self):
        self.assert_parity(["entities"])

    def test_analyze_no_index(self):
        self.assert_parity(["analyze"])

    def test_see_also_no_index(self):
        self.assert_parity(["see-also"])

    # reflect: deterministic --help surface (full report reads vault data /
    # may search the index -> deferred to host/VM).
    def test_reflect_help(self):
        self.assert_parity(["reflect", "--help"])

    # insight: --help + no-title error + empty --list are deterministic.
    def test_insight_help(self):
        self.assert_parity(["insight", "--help"])

    def test_insight_no_title(self):
        self.assert_parity(["insight"])

    def test_insight_list_empty(self):
        self.assert_parity(["insight", "--list"])

    def test_insight_create_seeded(self):
        # Creates wiki/insights/<title>.md (no body -> deterministic).
        self.assert_parity_2vault(["insight", "A New Finding"])

    def test_report_bug(self):
        self.assert_parity(["report-bug"])

    def test_request_feature(self):
        self.assert_parity(["request-feature"])

    def test_feature_alias(self):
        self.assert_parity(["feature"])

    # config: all paths are deterministic (writes .athena/config.json).
    def test_config_show_empty(self):
        self.assert_parity(["config"])

    def test_config_usage(self):
        self.assert_parity(["config", "bogus"])

    def test_config_set_seeded(self):
        self.assert_parity_2vault(["config", "set", "llm", "ollama"])

    def test_config_set_missing_key(self):
        self.assert_parity_2vault(["config", "set", "llm", "claude"])

    def test_config_unset_missing(self):
        self.assert_parity_2vault(["config", "unset", "llm"])

    # export: deterministic --help / unknown-format surfaces. Full export
    # (renders templates over vault data) deferred to host/VM.
    def test_export_help(self):
        self.assert_parity(["export", "--help"])

    def test_export_no_args(self):
        self.assert_parity(["export"])

    # ── Phase 3: network / ingest commands ──────────────────────
    # add / batch / capture-deep / backfill-assets / retry-assets.
    #
    # The network paths (bin/kb-capture, capture-deep.js, LLM synthesis) are
    # NOT parity-tested here -- they're non-deterministic and covered by the
    # host/VM 03-inbox spec. We exercise only the deterministic surfaces:
    # empty-inbox "nothing to process", unsupported-file error, no-arg usage,
    # and -- crucially -- the LOCAL no-network clipping-ingest path (a seeded
    # inbox/Clippings/*.md run end-to-end through process_clip + wiki_page).

    def test_add_no_args_empty(self):
        # No inbox URLs, no clippings -> "Nothing to process." Pure local.
        self.assert_parity_2vault(["add"])

    def test_add_unsupported_file(self):
        # First arg is an existing file of an unsupported type -> deterministic
        # error from classify_file. No network.
        self._assert_parity_2vault_seedfn(
            ["add", "junk.xyz"],
            lambda v: Path(v, "junk.xyz").write_text("not a supported type\n"))

    def test_add_seeded_clipping(self):
        # The 03-inbox LOCAL path: a Web-Clipper .md sitting in inbox/Clippings/
        # is processed end-to-end (process_clip -> raw, then orphan-scan ->
        # wiki_page.py) with NO network. example.com is not a promote-eligible
        # (LinkedIn) domain, so no capture-deep; the test vault has no
        # scripts/bulk-llm-regen-summaries.py, so no LLM synthesis fires.
        # Deterministic end-to-end -> full parity.
        self._assert_parity_2vault_seedfn(["add"], self._seed_clipping)

    def test_batch_empty_inbox(self):
        self.assert_parity_2vault(["batch"])

    def test_capture_deep_no_arg(self):
        # Usage goes to STDERR (exit 2); stdout is empty for both routes.
        self.assert_parity(["capture-deep"])

    def test_backfill_assets_empty(self):
        self.assert_parity_2vault(["backfill-assets"])

    def test_retry_assets_empty(self):
        self.assert_parity_2vault(["retry-assets"])

    def _assert_parity_2vault_seedfn(self, args: list[str], seed_fn) -> None:
        """Like assert_parity_2vault but with a per-vault callable seeder, for
        ingest cases that need a custom fixture (a clipping file, a junk file)."""
        v1 = _make_vault()
        v2 = _make_vault()
        try:
            seed_fn(v1)
            seed_fn(v2)
            legacy_code, legacy_out = _run("kb-legacy", args, v1)
            py_code, py_out = _run("kb", args, v2)
            self.assertEqual(legacy_code, py_code,
                             f"exit code differs for {args!r}")
            self.assertEqual(legacy_out, py_out,
                             f"stdout differs for {args!r}")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    def _seed_clipping(self, vault: str) -> None:
        """Drop a deterministic, non-LinkedIn Web-Clipper .md into Clippings/."""
        Path(vault, "inbox", "Clippings", "example-clip.md").write_text(
            "---\n"
            'title: "Example Article Title"\n'
            'source: "https://example.com/some-article"\n'
            "author:\n"
            "published:\n"
            "created: 2026-05-09\n"
            'description: "An example description."\n'
            "tags:\n"
            '  - "clippings"\n'
            "---\n\n"
            "# Example Article Title\n\n"
            "This is the body of the clipped article. It has a few sentences of "
            "content\nso the raw writer does not flag it as degraded or thin "
            "content.\n"
        )

    # ── Phase 4: FULL-behavior parity for the 13 previously surface-only cmds ──
    #
    # Each of these was parity-tested only on its --help / empty / error
    # surface. Here we seed REAL vault state (pages, hubs, journals, indexes)
    # and drive the command's full path. For state-mutating commands we compare
    # the resulting VAULT TREE too (not just stdout), via _tree_snapshot.
    #
    # Determinism was confirmed empirically (Bash-vs-Bash on a fixed seed):
    #   deterministic -> byte-parity (reflect, insight, export, merge, move,
    #     ungroup, attach, detach, refresh-wiki, see-also, entities)
    #   NONdeterministic -> structural assertion (topics, analyze) -- see the
    #     dedicated tests for the exact volatile fields and why.

    def _index(self, vault: str) -> None:
        """Build the search index in `vault` (read-only seed step -- legacy is
        the oracle, so we index with it; the index DB is impl-agnostic)."""
        _run("kb-legacy", ["index"], vault)

    def assert_parity_2vault_tree(self, args: list[str], seed) -> None:
        """Like assert_parity_2vault but ALSO asserts the post-command vault
        TREE is byte-identical between the Bash run (v1) and the Python run
        (v2). `seed` is a per-vault callable. This is the strong form: it
        proves the command's EFFECT on disk -- created/moved/trashed files and
        their content -- matches, not merely its stdout."""
        v1 = _make_vault()
        v2 = _make_vault()
        try:
            seed(v1)
            seed(v2)
            legacy_code, legacy_out = _run("kb-legacy", args, v1)
            py_code, py_out = _run("kb", args, v2)
            self.assertEqual(legacy_code, py_code,
                             f"exit code differs for {args!r}")
            self.assertEqual(legacy_out, py_out, f"stdout differs for {args!r}")
            self.assertEqual(_tree_snapshot(v1), _tree_snapshot(v2),
                             f"vault tree differs for {args!r}")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    # ── Seeders for full-behavior cases ──────────────────────────

    def _seed_hub_unattached(self, vault: str) -> None:
        """Two webpages + an EMPTY topic hub (no members). For move/attach
        INTO the hub. The hub is seeded directly with a fixed date so its
        frontmatter date fields can't differ a clock-tick apart between runs."""
        for title, slug in (("Example Page", "example-com-page"),
                            ("Second Page", "example-com-two")):
            Path(vault, "raw", "webpages", "artifacts", f"{slug}.md").write_text(
                f"---\nurl: https://example.com/{slug}\ntitle: {title}\n---\n\n"
                f"# {title}\n\nbody.\n")
            Path(vault, "wiki", "format", "webpages", f"{title}.md").write_text(
                f'---\ntitle: "{title}"\nsource_type: "webpage"\n'
                f"url: https://example.com/{slug}\n"
                f'raw_path: "raw/webpages/artifacts/{slug}.md"\n'
                f"tags: [t]\nrelated: []\n---\n\n# {title}\n\nbody.\n")
        Path(vault, "wiki", "topics", "My Hub.md").write_text(
            '---\ntitle: "My Hub"\nsource_type: "topic"\n'
            "date_added: 2026-01-01\nlast_updated: 2026-01-01\n"
            'tags: [topic]\nrelated:\nsummary: "Hub page for My Hub."\n---\n\n\n'
            "## Resources\n\n| Resource | Type | Tags |\n|---|---|---|\n\n"
            "## Keywords\n[[my hub]]\n")

    def _seed_hub_with_members(self, vault: str) -> None:
        """A topic hub that ALREADY contains two child pages (related: + table
        row + each child's back-link). For ungroup / detach / move-already-in."""
        for title, slug in (("Example Page", "example-com-page"),
                            ("Second Page", "example-com-two")):
            Path(vault, "raw", "webpages", "artifacts", f"{slug}.md").write_text(
                f"---\nurl: https://example.com/{slug}\ntitle: {title}\n---\n\n"
                f"# {title}\n\nbody.\n")
            Path(vault, "wiki", "format", "webpages", f"{title}.md").write_text(
                f'---\ntitle: "{title}"\nsource_type: "webpage"\n'
                f"url: https://example.com/{slug}\n"
                f'raw_path: "raw/webpages/artifacts/{slug}.md"\n'
                f'tags: [t]\nrelated:\n  - "[[My Hub]]"\n---\n\n'
                f"# {title}\n\nbody.\n\n## Connections\n\n- [[My Hub]]\n")
        Path(vault, "wiki", "topics", "My Hub.md").write_text(
            '---\ntitle: "My Hub"\nsource_type: "topic"\n'
            "date_added: 2026-01-01\nlast_updated: 2026-01-01\n"
            'tags: [topic]\nrelated:\n  - "[[Example Page]]"\n'
            '  - "[[Second Page]]"\nsummary: "Hub page for My Hub."\n---\n\n\n'
            "## Resources\n\n| Resource | Type | Tags |\n|---|---|---|\n"
            "| [[Example Page]] | webpage | [t] |\n"
            "| [[Second Page]] | webpage | [t] |\n\n## Keywords\n[[my hub]]\n")

    def _seed_refreshable(self, vault: str) -> None:
        """A wiki page whose frontmatter raw_path points at a real raw with
        enough body that refresh-wiki's process_clip rewrite is non-trivial."""
        Path(vault, "raw", "webpages", "artifacts", "example-com-page.md").write_text(
            "---\nurl: https://example.com/page\ntitle: Example Page\n---\n\n"
            "# Example Page\n\nSome captured body text. It has several sentences "
            "so it is not flagged as thin. The transformer architecture is "
            "described here in some detail for the refresh path.\n")
        Path(vault, "wiki", "format", "webpages", "Example Page.md").write_text(
            '---\ntitle: "Example Page"\nsource_type: "webpage"\n'
            "url: https://example.com/page\n"
            'raw_path: "raw/webpages/artifacts/example-com-page.md"\n'
            "tags: [t]\nrelated: []\n---\n\n# Example Page\n\nStale summary.\n")

    def _seed_journal_fixed(self, vault: str) -> None:
        """Seed today's journal file DIRECTLY (not via `kb journal`, whose
        `### HH:MM` header could tick between runs) with fixed entries within
        reflect's 7-day window. Both vaults get byte-identical journal input,
        so reflect's report is identical apart from today's date (which both
        runs share)."""
        import time
        today = time.strftime("%Y-%m-%d")
        Path(vault, "wiki", "journal").mkdir(parents=True, exist_ok=True)
        Path(vault, "wiki", "journal", f"Journal-{today}.md").write_text(
            f'---\ntitle: "Journal-{today}"\nsource_type: "topic"\n'
            f"date_added: {today}\nlast_updated: {today}\n"
            f"tags: [journal]\nrelated:\n---\n\n"
            f"### 09:15\n\nLearned about gradient descent and how the learning "
            f"rate affects convergence.\n\n---\n\n"
            f"### 14:30\n\nRevisited transformers; attention is the key idea in "
            f"the BERT paper.\n\n---\n\n")

    def _seed_multi_indexed(self, vault: str) -> None:
        """Several real, cross-linked wiki pages, THEN build the search index.
        Drives the index-backed synthesis commands (topics/entities/analyze/
        see-also). Two clusters (transformers / RL) with named entities
        (Google, OpenAI, DeepMind, Geoffrey Hinton) and related: edges so the
        graph is connected enough for community detection."""
        pages = {
            "Transformers Overview": ("transformers",
                ["BERT Language Model", "Attention Mechanisms"],
                "Transformers use self-attention. Google introduced the "
                "transformer architecture. Geoffrey Hinton influenced deep "
                "learning. The transformer revolutionized NLP and attention "
                "is the key idea."),
            "BERT Language Model": ("bert",
                ["Transformers Overview", "GPT Models"],
                "BERT is a bidirectional transformer by Google. It uses masked "
                "language modeling. BERT pretraining uses large text corpora "
                "from Wikipedia and books."),
            "GPT Models": ("gpt",
                ["BERT Language Model", "Attention Mechanisms"],
                "GPT models are autoregressive transformers from OpenAI. GPT "
                "uses causal attention. OpenAI released GPT for text generation "
                "and language understanding."),
            "Attention Mechanisms": ("attn",
                ["Transformers Overview", "GPT Models"],
                "Attention mechanisms let models focus on relevant tokens. "
                "Self-attention and cross-attention are used in transformers "
                "by Google and OpenAI."),
            "Reinforcement Learning Basics": ("rl",
                ["Deep Q Networks", "Policy Gradients"],
                "Reinforcement learning trains agents via reward signals. "
                "DeepMind pioneered RL. Q-learning and policy gradients are "
                "core methods for agents."),
            "Deep Q Networks": ("dqn",
                ["Reinforcement Learning Basics", "Policy Gradients"],
                "Deep Q networks combine RL with deep neural networks. DeepMind "
                "used DQN to play Atari games with experience replay."),
            "Policy Gradients": ("pg",
                ["Reinforcement Learning Basics", "Deep Q Networks"],
                "Policy gradient methods optimize policies directly. REINFORCE "
                "and actor-critic are policy gradient algorithms used by "
                "DeepMind."),
            "AlphaGo System": ("alphago",
                ["Deep Q Networks", "Reinforcement Learning Basics"],
                "AlphaGo by DeepMind beat the world champion at Go. It combines "
                "Monte Carlo tree search with reinforcement learning and deep "
                "neural networks."),
        }
        for title, (slug, rel, body) in pages.items():
            Path(vault, "raw", "webpages", "artifacts", f"{slug}.md").write_text(
                f"---\nurl: https://example.com/{slug}\ntitle: {title}\n---\n\n"
                f"# {title}\n\n{body}\n")
            rellist = "".join(f'  - "[[{r}]]"\n' for r in rel)
            conn = "".join(f"- [[{r}]]\n" for r in rel)
            Path(vault, "wiki", "format", "webpages", f"{title}.md").write_text(
                f'---\ntitle: "{title}"\nsource_type: "webpage"\n'
                f"url: https://example.com/{slug}\n"
                f'raw_path: "raw/webpages/artifacts/{slug}.md"\n'
                f"tags: [ml]\nrelated:\n{rellist}---\n\n# {title}\n\n{body}\n\n"
                f"## Connections\n\n{conn}")
        self._index(vault)

    # ── reflect: deterministic full report (seeded journal) ──────
    def test_reflect_full(self):
        # Real journal entries within the 7-day window -> reflect builds its
        # full report (entry list, recently-modified pages, task prompt).
        # Deterministic: byte-parity with seeded-journal vaults.
        self._assert_parity_2vault_seedfn(["reflect"], self._seed_journal_fixed)

    # ── insight: create a page WITH a body ───────────────────────
    def test_insight_create_with_body(self):
        # The body path (vs the empty test_insight_create_seeded): writes
        # wiki/insights/<title>.md with content and suppresses the
        # "fill-insight" hint. Compare stdout + the created file's content.
        self.assert_parity_2vault_tree(
            ["insight", "My Finding", "This is the body of a polished insight."],
            self._seed_refreshable)

    # ── export: full render over seeded pages (writes files) ─────
    def test_export_stats_summary(self):
        # Renders a multi-chart dashboard to wiki/exports/kb-dashboard.md.
        self.assert_parity_2vault_tree(
            ["export", "stats_summary", "kb_stats"], self._seed_refreshable)

    def test_export_table_csv(self):
        # CSV export of page metadata to .athena/exports/export.csv.
        self.assert_parity_2vault_tree(
            ["export", "table_export", "kb_stats", "--format", "csv"],
            self._seed_refreshable)

    def test_export_table_json(self):
        self.assert_parity_2vault_tree(
            ["export", "table_export", "kb_stats", "--format", "json"],
            self._seed_refreshable)

    # ── merge: full two-page merge, compare tree too ─────────────
    def test_merge_seeded_tree(self):
        # Strengthens test_merge_seeded: also assert the merged page + both
        # trashed sources match between impls.
        def seed(v):
            self._seed_page(v)
            self._seed_second_page(v)
        self.assert_parity_2vault_tree(
            ["merge", "Example Page", "Second Page", "--into", "Merged", "--yes"],
            seed)

    # ── grouping: move / ungroup / attach / detach (full + tree) ──
    def test_move_into_hub(self):
        self.assert_parity_2vault_tree(
            ["move", "Example Page", "--into", "My Hub"],
            self._seed_hub_unattached)

    def test_attach_to_hub(self):
        self.assert_parity_2vault_tree(
            ["attach", "Example Page", "--to", "My Hub"],
            self._seed_hub_unattached)

    def test_detach_from_hub(self):
        self.assert_parity_2vault_tree(
            ["detach", "Example Page", "--from", "My Hub"],
            self._seed_hub_with_members)

    def test_ungroup_hub(self):
        # Hub with two members -> dissolve. --yes deletes the hub (to trash)
        # and frees the children. Exercises BOTH snapshot ops (modify children,
        # trash hub) -- the ordinal-keyed trash compare in _tree_snapshot is
        # what makes the two snapshot dirs compare correctly.
        self.assert_parity_2vault_tree(
            ["ungroup", "My Hub", "--yes"], self._seed_hub_with_members)

    # ── refresh-wiki: replay process_clip over the backing raw ───
    def test_refresh_wiki_full(self):
        self.assert_parity_2vault_tree(
            ["refresh-wiki", "Example Page"], self._seed_refreshable)

    # ── see-also: index-backed, deterministic (pure SQL) ─────────
    def test_see_also_for_page(self):
        # Reads keywords from the index and lists shared-concept neighbours.
        # Pure SQL over the keyword table -> deterministic -> byte-parity.
        self._assert_parity_2vault_seedfn(
            ["see-also", "Transformers"], self._seed_multi_indexed)

    # ── entities: index-backed SpaCy NER, deterministic ──────────
    def test_entities_extract(self):
        # `--extract` runs SpaCy NER over all pages and writes the entity
        # table, then prints the top entities. SpaCy NER is deterministic
        # (confirmed Bash-vs-Bash byte-stable) -> byte-parity.
        self._assert_parity_2vault_seedfn(
            ["entities", "--extract"], self._seed_multi_indexed)

    # ── topics: NONDETERMINISTIC (BERTopic/UMAP/HDBSCAN) -- STRUCTURAL ──
    #
    # Confirmed empirically: `kb topics` is NOT byte-stable run-to-run on a
    # FIXED seeded+indexed vault, run through the SAME (Bash) implementation.
    # BERTopic's UMAP+HDBSCAN pipeline is stochastic; on small corpora HDBSCAN
    # can even reduce to a degenerate (0-sample) array and raise inside
    # BERTopic, so the topic count AND the exit code vary draw-to-draw. (In
    # THIS environment it consistently hits that degenerate path -> exit 1,
    # stdout just the "Discovering topics..." banner.) Byte-parity is therefore
    # impossible IN PRINCIPLE -- the legacy oracle isn't stable against itself.
    #
    # What IS stable and shared between the two impls (cmd_topics.py is a
    # verbatim lift of the Bash heredoc -- both import the same discover_topics)
    # is the deterministic stdout PREFIX printed before the stochastic model
    # runs. We assert that prefix matches; we do NOT assert exit code or the
    # topic list (genuinely volatile). The full topic-list path is covered by
    # the host/VM matrix, where a larger real corpus makes BERTopic converge.
    def test_topics_structural(self):
        v1 = _make_vault()
        v2 = _make_vault()
        try:
            self._seed_multi_indexed(v1)
            self._seed_multi_indexed(v2)
            _, legacy_out = _run("kb-legacy", ["topics"], v1)
            _, py_out = _run("kb", ["topics"], v2)
            # Stable, shared prefix line (printed before the stochastic model).
            self.assertEqual(legacy_out.splitlines()[0], "Discovering topics...")
            self.assertEqual(legacy_out.splitlines()[0], py_out.splitlines()[0],
                             "topics stdout prefix differs")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    # ── analyze: NONDETERMINISTIC (networkx community detection) -- STRUCTURAL ──
    #
    # Confirmed empirically: `kb analyze` is NOT byte-stable run-to-run on a
    # FIXED seed via the SAME (Bash) impl -- 5 runs gave 5 different outputs.
    # networkx's greedy/louvain community detection is randomized: the LABEL
    # keywords (derived from page ordering) and the WITHIN-community page order
    # shuffle draw-to-draw. Byte-parity is impossible in principle.
    #
    # What IS stable and shared (cmd_analyze.py is a verbatim lift) is the
    # STRUCTURE: the graph-stats line, the community COUNT, and the community
    # PARTITION as a set of frozensets of page names. We assert those match
    # between Bash and Python; we do NOT assert label text or page ordering.
    def test_analyze_structural(self):
        import re

        def parse(out: str):
            lines = out.splitlines()
            graph = [l.strip() for l in lines if l.strip().startswith("Graph:")]
            comms = []
            cur = None
            for l in lines:
                m = re.match(r"\s*\[\s*(\d+) pages\]", l)
                if m:
                    if cur is not None:
                        comms.append((cur[0], frozenset(cur[1])))
                    cur = (int(m.group(1)), [])
                elif (cur is not None and l.startswith("             ")
                      and l.strip() and "more" not in l):
                    cur[1].append(l.strip())
            if cur is not None:
                comms.append((cur[0], frozenset(cur[1])))
            return graph, len(comms), frozenset(comms)

        v1 = _make_vault()
        v2 = _make_vault()
        try:
            self._seed_multi_indexed(v1)
            self._seed_multi_indexed(v2)
            legacy_code, legacy_out = _run("kb-legacy", ["analyze"], v1)
            py_code, py_out = _run("kb", ["analyze"], v2)
            lg, ln, lp = parse(legacy_out)
            pg, pn, pp = parse(py_out)
            self.assertEqual(legacy_code, py_code, "analyze exit differs")
            self.assertEqual(lg, pg, "analyze graph stats differ")
            self.assertEqual(ln, pn, "analyze community count differs")
            self.assertEqual(lp, pp, "analyze community partition differs")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    # ── Phase 5: STUBBED-NETWORK ingest parity ───────────────────────
    #
    # Phase 3 (above) parity-tested only the deterministic SURFACES of the
    # ingest arms (empty inbox, unsupported file, the LOCAL clipping path).
    # The actual capture orchestration -- the loop that shells bin/kb-capture,
    # snapshots ls -t / _newest_raw, gates BEFORE_RAW != LAST_RAW, then drives
    # wiki_page.py over the new raw -- was left to the host/VM matrix because
    # the network step is non-deterministic.
    #
    # Here we STUB the network boundary INSIDE the throwaway vault so BOTH
    # implementations consume one identical fixture. _make_vault() copies bin/
    # into the vault and BOTH entries resolve helpers from that copy, so a stub
    # placed in <vault>/bin (or <vault>/bin/lib) feeds Bash AND Python the same
    # bytes. With the network frozen, ANY remaining stdout/tree divergence is a
    # REAL orchestration-glue bug -- which is the only thing that differs
    # between the two impls (the extractor modules are shared, unchanged code).
    #
    # Four seams are stubbed:
    #   * <vault>/bin/kb-capture        -> fake Bash writer (add/batch paths)
    #   * <vault>/scripts/capture-deep.js -> fake node script (capture-deep)
    #   * <vault>/bin/lib/asset_download.py:_download -> fixed bytes
    #     (backfill-assets / retry-assets)
    # Each stub records a FIXED output shape -- never live network.

    # A fixed raw body the stub kb-capture writes for a webpage capture. Several
    # sentences so raw_writer never flags it as thin/degraded, and so wiki_page's
    # heuristic synthesis (no LLM in the test vault) produces a real summary.
    _STUB_WEBPAGE_BODY = (
        "## Content\n\n"
        "This is a recorded webpage capture used for parity testing. The "
        "article discusses retrieval augmented generation and how vector "
        "stores improve answer grounding. It contains several sentences of "
        "real prose so the raw writer does not flag it as degraded content "
        "and the wiki synthesizer has material to summarize.\n"
    )
    _STUB_REPO_BODY = (
        "- **Stars:** 1234\n- **Language:** Python\n- **Topics:** llm, rag\n"
        "- **Last updated:** 2026-01-01\n- **Clipped:** 2026-01-01\n"
        "- **Content fetched:** yes (metadata + README)\n\n"
        "## Description\n\nA recorded repo capture for parity testing.\n\n"
        "## README\n\n"
        "This project provides a small retrieval library. It indexes documents "
        "and serves nearest-neighbour lookups over embeddings. The README has "
        "enough prose that the wiki synthesizer builds a real summary.\n"
    )

    def _install_stub_kb_capture(self, vault: str) -> None:
        """Overwrite <vault>/bin/kb-capture with a deterministic PYTHON stub.

        kb-capture is now a cross-platform Python program invoked via the
        interpreter (`[sys.executable, kb-capture, url]` from cmd_add/cmd_batch),
        so the stub is Python too — with a `#!/usr/bin/env python3` shebang so the
        Bash `bin/kb-legacy` path (which execs `<root>/bin/kb-capture <url>`)
        runs it via shebang on POSIX, and the Python `bin/kb` path runs it under
        sys.executable. ONE stub, both kb implementations call it identically →
        any output diff is the add/batch orchestration we're parity-testing.

        For each URL the real kb-capture would hit the network (curl health
        check, arcus_html, gh api, arxiv scrape). The stub instead writes a FIXED
        recorded raw via the REAL shared wiki_schema.py writer and prints the same
        `Detected type:`/`Slug:`/`Saved:` lines. Routing: github.com -> repo,
        else -> webpage (both add-arm dispatch branches). No network, byte-stable.
        """
        stub = f"""#!/usr/bin/env python3
import os, re, subprocess, sys, tempfile
KB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
url = sys.argv[1]
if re.search(r"github\\.com/[^/]+/[^/]+", url):
    typ, title = "repo", None
    parts = "/".join(re.sub(r"https?://github\\.com/", "", url).split("/")[:2])
    slug, title = parts.replace("/", "--"), parts
    body = {self._STUB_REPO_BODY!r}
else:
    typ, title = "webpage", "Recorded Capture"
    s = re.sub(r"^www\\.", "", re.sub(r"^https?://", "", url)).rstrip("/").lower()
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", s)).strip("-")
    body = {self._STUB_WEBPAGE_BODY!r}
print("Detected type: " + typ)
print("Slug: " + slug)
print("Fetching content (stub)...")
with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
    fh.write(body)
    body_tmp = fh.name
new_raw = subprocess.run(
    [sys.executable, os.path.join(KB_ROOT, "bin", "lib", "wiki_schema.py"), "write",
     "--vault", KB_ROOT, "--source-type", typ, "--url", url, "--title", title,
     "--extra", "clipped_via=kb-capture", "--extra", "clipped_at=2026-01-01",
     "--input", body_tmp],
    capture_output=True, text=True).stdout.strip()
os.unlink(body_tmp)
# realpath both sides so the displayed path is stable across /tmp vs
# /private/tmp symlink resolution (a stub artifact, not real behavior).
rel = os.path.relpath(os.path.realpath(new_raw), os.path.realpath(KB_ROOT))
print("")
print("Saved: " + rel)
"""
        path = os.path.join(vault, "bin", "kb-capture")
        Path(path).write_text(stub)
        os.chmod(path, 0o755)

    def _install_stub_capture_deep(self, vault: str, body: str = "") -> None:
        """Drop a fake <vault>/scripts/capture-deep.js that echoes a recorded
        line and exits 0 -- no Playwright, no browser, no network. Both entries
        exec the SAME node script, so the orchestration around it (env wiring,
        exit-code forwarding) is what's compared."""
        os.makedirs(os.path.join(vault, "scripts"), exist_ok=True)
        line = body or "[capture-deep] stub: wrote clip (recorded, no network)"
        script = (
            "#!/usr/bin/env node\n"
            f"process.stdout.write({line!r} + '\\n');\n"
            "process.exit(0);\n"
        )
        Path(vault, "scripts", "capture-deep.js").write_text(script)

    def _install_stub_asset_download(self, vault: str) -> None:
        """Monkeypatch <vault>/bin/lib/asset_download.py so its sole network
        primitive `_download(url)` returns FIXED bytes instead of hitting the
        wire. We append a redefinition at module end (later def wins) -- all
        the surrounding deterministic logic (sha256 content-addressing, ext
        derivation, sidecar, body rewrite) stays the real shared code. Both
        the backfill and retry bodies import this patched module."""
        mod = os.path.join(vault, "bin", "lib", "asset_download.py")
        patch = (
            "\n\n# ── PARITY TEST STUB (appended) ──────────────────────\n"
            "# Freeze the network boundary: return a fixed 1x1 PNG for any URL\n"
            "# so download_assets() runs its full deterministic rewrite logic\n"
            "# without touching the wire.\n"
            "_STUB_PNG = bytes([\n"
            "    0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A,0x00,0x00,0x00,0x0D,\n"
            "    0x49,0x48,0x44,0x52,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,\n"
            "    0x08,0x06,0x00,0x00,0x00,0x1F,0x15,0xC4,0x89,0x00,0x00,0x00,\n"
            "    0x0A,0x49,0x44,0x41,0x54,0x78,0x9C,0x63,0x00,0x01,0x00,0x00,\n"
            "    0x05,0x00,0x01,0x0D,0x0A,0x2D,0xB4,0x00,0x00,0x00,0x00,0x49,\n"
            "    0x45,0x4E,0x44,0xAE,0x42,0x60,0x82,\n"
            "])\n"
            "def _download(url):  # noqa: F811 -- intentional test override\n"
            "    return _STUB_PNG, 'image/png'\n"
        )
        with open(mod, "a", encoding="utf-8") as fh:
            fh.write(patch)

    # ── add <url>: webpage branch (arcus_html seam, via stub kb-capture) ──
    def test_add_url_webpage_stubbed(self):
        # A non-github URL routes through kb-capture's webpage branch. Stubbed
        # capture writes a fixed raw; the add arm then snapshots _newest_raw,
        # gates BEFORE!=LAST, and drives wiki_page.py over the new raw. All of
        # that glue must match Bash. Assert stdout + the full vault tree.
        def seed(v):
            self._install_stub_kb_capture(v)
        self.assert_parity_2vault_tree(
            ["add", "https://example.com/some-article"], seed)

    # ── add <url>: repo branch (kb-capture non-webpage seam) ──
    def test_add_url_repo_stubbed(self):
        # A github URL routes through the repo branch -> raw/repos/. Exercises
        # the OTHER dispatch branch + repo-shaped wiki synthesis.
        def seed(v):
            self._install_stub_kb_capture(v)
        self.assert_parity_2vault_tree(
            ["add", "https://github.com/acme/widget"], seed)

    # ── add <url1> <url2>: multi-URL loop ──
    def test_add_multi_url_stubbed(self):
        # The multi-URL branch (≥2 URLs): banner, per-URL separators, the
        # `Done. Added N sources.` tail, then wiki synthesis on the newest raw.
        # NOTE: the arm sleeps 1s between captures in BOTH impls -- identical.
        def seed(v):
            self._install_stub_kb_capture(v)
        self.assert_parity_2vault_tree(
            ["add", "https://example.com/a", "https://github.com/acme/b"], seed)

    # ── add (no args): inbox URLs + orphan-scan + dashboards ──
    def test_add_inbox_urls_stubbed(self):
        # Seed inbox/url-new.txt, stub capture. The no-arg path: prints
        # `Found N URLs`, loops kb-capture, archives to url-resolved.tsv,
        # then the dead-page sweep + orphan-raw -> wiki synthesis + dashboard
        # regen all run. The richest orchestration surface in the add arm.
        def seed(v):
            self._install_stub_kb_capture(v)
            Path(v, "inbox", "url-new.txt").write_text(
                "https://example.com/inbox-article\n"
                "https://github.com/acme/inbox-repo\n")
        self.assert_parity_2vault_tree(["add"], seed)

    # ── batch: inbox loop + TSV bookkeeping + inbox prune ──
    def test_batch_stubbed(self):
        def seed(v):
            self._install_stub_kb_capture(v)
            Path(v, "inbox", "url-new.txt").write_text(
                "https://example.com/batch-one\n"
                "https://github.com/acme/batch-two\n")
        self.assert_parity_2vault_tree(["batch"], seed)

    # ── capture-deep <url>: node exec + exit-code forwarding ──
    def test_capture_deep_stubbed(self):
        # Stub the node script so it echoes a recorded line and exits 0. The
        # arm just wires KB_ROOT into env and forwards node's exit code; the
        # capture-deep.js stdout itself is identical for both (same script).
        v1 = _make_vault()
        v2 = _make_vault()
        try:
            self._install_stub_capture_deep(v1)
            self._install_stub_capture_deep(v2)
            legacy_code, legacy_out = _run(
                "kb-legacy", ["capture-deep", "https://linkedin.com/posts/x"], v1)
            py_code, py_out = _run(
                "kb", ["capture-deep", "https://linkedin.com/posts/x"], v2)
            self.assertEqual(legacy_code, py_code, "capture-deep exit differs")
            self.assertEqual(legacy_out, py_out, "capture-deep stdout differs")
        finally:
            shutil.rmtree(v1, ignore_errors=True)
            shutil.rmtree(v2, ignore_errors=True)

    def _seed_raw_with_remote_image(self, vault: str) -> None:
        """A raw webpage referencing a remote image, so backfill-assets has a
        download to perform. The stub _download returns fixed PNG bytes, so the
        content-addressed local filename (sha256) is identical in both runs."""
        Path(vault, "raw", "webpages", "artifacts", "img-page.md").write_text(
            "---\n"
            "title: Image Page\n"
            "url: https://example.com/img-page\n"
            "source_type: webpage\n"
            "---\n\n"
            "# Image Page\n\n"
            "Some text.\n\n"
            "![a diagram](https://cdn.example.com/diagram.png)\n\n"
            "More text after the image.\n")

    # ── backfill-assets: download + raw rewrite + sidecar (stub _download) ──
    def test_backfill_assets_stubbed(self):
        def seed(v):
            self._install_stub_asset_download(v)
            self._seed_raw_with_remote_image(v)
        self.assert_parity_2vault_tree(["backfill-assets"], seed)

    # ── retry-assets: re-attempt from asset-retry.tsv (stub _download) ──
    def test_retry_assets_stubbed(self):
        def seed(v):
            self._install_stub_asset_download(v)
            self._seed_raw_with_remote_image(v)
            # Seed a retry queue pointing at the seeded page's remote image.
            Path(v, "inbox", "asset-retry.tsv").write_text(
                "page_slug\turl\talt\terror\ttried_at\n"
                "img-page\thttps://cdn.example.com/diagram.png\ta diagram\t"
                "URLError: prior failure\t2026-01-01T00:00:00Z\n")
        self.assert_parity_2vault_tree(["retry-assets"], seed)


if __name__ == "__main__":
    unittest.main()
