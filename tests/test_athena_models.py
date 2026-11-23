"""scripts/_athena_models.py: the Python scripts' model ids and cold-start
budgets come from the vendored Gryphon registry (Athena dev issue #182).

The bulk regen scripts hardcoded claude-opus-4-7 and _athena_timeout kept a
hand copy of Gryphon's cold-start table; Gryphon 2.13.0 retired those ids.
Expected values are read from the registry here too, never hardcoded, so the
next Gryphon bump re-checks itself.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _athena_models as models  # noqa: E402
import _athena_timeout as timeout  # noqa: E402

DIST = ROOT / "vendor" / "gryphon" / "packages" / "provider-config" / "dist"


def _registry(js: str):
    return json.loads(subprocess.run(
        ["node", "-e", f'const r=require({json.dumps(str(DIST / "registry.js"))});'
                       f'const a=require({json.dumps(str(DIST / "pricing" / "anthropic.js"))});'
                       f"process.stdout.write(JSON.stringify({js}))"],
        capture_output=True, text=True, check=True).stdout)


class ClaudeModel(unittest.TestCase):
    def setUp(self):
        models._lookup.cache_clear()

    def test_aliases_resolve_through_the_registry(self):
        for alias in ("opus", "sonnet", "haiku"):
            with self.subTest(alias=alias):
                self.assertEqual(models.claude_model(alias), _registry(f'a.resolveModel("{alias}")'))

    def test_a_retired_id_moves_to_its_successor(self):
        retired = [k for k in _registry("r.RETIRED_MODEL_MIGRATION") if k.startswith("claude-")]
        self.assertTrue(retired)
        for rid in retired:
            with self.subTest(id=rid):
                self.assertEqual(models.claude_model(rid), _registry(f'r.retiredModelSuccessor("{rid}")'))

    def test_1m_suffix_resolves_to_the_base_model(self):
        self.assertEqual(models.claude_model("opus[1m]"), models.claude_model("opus"))

    def test_bad_input_is_refused(self):
        for bad in ("", "Opus", "opus; rm -rf", "../x", "--model"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                models.claude_model(bad)

    def test_no_node_raises_never_a_stale_literal(self):
        with mock.patch.object(shutil, "which", return_value=None):
            with self.assertRaises(models.RegistryUnavailable):
                models.claude_model("opus")

    def test_missing_registry_raises(self):
        with mock.patch.object(models, "_DIST", ROOT / "nope"):
            with self.assertRaises(models.RegistryUnavailable):
                models.claude_model("sonnet")


class ColdStart(unittest.TestCase):
    def setUp(self):
        models._lookup.cache_clear()

    def test_budget_is_the_registrys(self):
        mid = models.claude_model("opus")
        self.assertEqual(models.cold_start_seconds(mid), _registry(f'r.coldStartMsFor("{mid}")') // 1000)

    def test_timeout_uses_it_without_an_override(self):
        mid = models.claude_model("haiku")
        vault = ROOT / "tests" / "_no_such_vault"  # no data.json → no override
        self.assertEqual(timeout.resolve_timeout_seconds(mid, vault=vault),
                         models.cold_start_seconds(mid))

    def test_registry_failure_falls_back_loudly_to_the_default(self):
        with mock.patch.object(models, "_DIST", ROOT / "nope"), \
                mock.patch("sys.stderr") as err:
            got = timeout.resolve_timeout_seconds("opus", vault=ROOT / "tests" / "_no_such_vault")
        self.assertEqual(got, timeout._DEFAULT_TIMEOUT_S)
        self.assertTrue(err.write.called, "the fallback must say so on stderr")


class Callers(unittest.TestCase):
    def test_bulk_scripts_take_the_model_from_the_registry(self):
        for name in ("bulk-llm-regen-summaries.py", "bulk-llm-regen-tags.py"):
            src = (SCRIPTS / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn('MODEL = claude_model("opus")', src)
                self.assertNotRegex(src, r'MODEL = "claude-')

    def test_timeout_table_is_gone(self):
        self.assertNotIn("_MODEL_TIMEOUTS_S", (SCRIPTS / "_athena_timeout.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
