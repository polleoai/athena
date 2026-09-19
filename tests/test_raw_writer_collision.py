"""write_raw must never overwrite a raw that belongs to a DIFFERENT source.

`capture.resolve_raw_path` already computed a `-2` filename on collision and
printed that promise to the user — then `handle_webpage` reassigned `raw_file`
from `wiki_schema_write()`, discarding it. The guard sat outside the component
that owns path derivation, so its decision was advisory.

`raw_writer.write_raw` is the single choke point every writer funnels through
(kb-capture, Web Clipper, MCP). The rule belongs here, where there is no
return value left for a caller to ignore.

Distinct from the two guards already in write_raw:
  * the size guard needs the existing file to be substantially bigger,
  * the marker guard needs the new content to match a known-bad phrase.
Both can be out-voted by a thin-vs-thin overwrite or an unlisted wording.
"Different source URL" needs neither a size nor a list.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import raw_writer  # noqa: E402


def _write(vault: Path, url: str, title: str, body: str) -> Path:
    return raw_writer.write_raw(
        vault_root=vault, source_type="webpage", url=url,
        title=title, body=body, slug_override="collide-me",
    )


class DistinctSourceCollision(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_same_url_still_overwrites_in_place(self):
        """Re-capture and refresh-wiki depend on updating the same file."""
        first = _write(self.vault, "https://example.com/a", "A", "original body")
        second = _write(self.vault, "https://example.com/a", "A", "fresher body")
        self.assertEqual(first, second)
        self.assertIn("fresher body", second.read_text(encoding="utf-8"))

    def test_different_url_does_not_clobber(self):
        """The regression: a second source must not land on the first's file."""
        first = _write(self.vault, "https://example.com/a", "A",
                       "post one body, which is the valuable one")
        second = _write(self.vault, "https://example.com/b", "B", "post two body")

        self.assertNotEqual(first, second)
        self.assertTrue(first.exists(), "the original raw must survive")
        self.assertIn("post one body", first.read_text(encoding="utf-8"))
        self.assertIn("post two body", second.read_text(encoding="utf-8"))

    def test_collision_suffix_is_sequential(self):
        a = _write(self.vault, "https://example.com/a", "A", "body a")
        b = _write(self.vault, "https://example.com/b", "B", "body b")
        c = _write(self.vault, "https://example.com/c", "C", "body c")
        self.assertEqual(
            [p.stem for p in (a, b, c)],
            ["collide-me", "collide-me-2", "collide-me-3"],
        )

    def test_thin_content_cannot_clobber_a_different_source(self):
        """The exact shape that lost the LinkedIn raw: thin, unlisted wording,
        different source. Neither existing guard fires; this one must."""
        first = _write(self.vault, "https://linkedin.com/posts/ugcpost-111",
                       "Real Post", "a real captured post body " * 20)
        # Thin, and worded so the marker list would NOT have caught it.
        wall = _write(self.vault, "https://linkedin.com/posts/ugcpost-222",
                      "Members-only content", "Email or phone Password")
        self.assertNotEqual(first, wall)
        self.assertIn("a real captured post", first.read_text(encoding="utf-8"))

    def test_same_url_with_only_tracking_params_is_not_a_collision(self):
        """Canonicalization runs first, so ?utm_source noise still dedups."""
        a = _write(self.vault, "https://example.com/p", "P", "body one")
        b = _write(self.vault, "https://example.com/p?utm_source=x", "P", "body two")
        self.assertEqual(a, b)


class DegradedMarkers(unittest.TestCase):
    def test_new_to_linkedin_wall_is_a_known_degraded_marker(self):
        """The wall that got through said 'New to LinkedIn?' — not the
        'Sign Up | LinkedIn' / 'Join LinkedIn now' the list knew about."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name)
        _write(vault, "https://example.com/a", "Real", "a real body " * 400)
        # Padded past 1KB so the SIZE guard cannot fire — otherwise this test
        # passes without the marker list ever being consulted.
        wall = ("By continuing, you agree to LinkedIn's User Agreement\n"
                "Email or phone Password Forgot password?\n" * 40)
        self.assertGreater(len(wall), 1024)
        with self.assertRaises(raw_writer.DegradedContentError) as ctx:
            raw_writer.write_raw(
                vault_root=vault, source_type="webpage",
                url="https://example.com/a", title="New to LinkedIn?",
                body=wall, slug_override="collide-me",
            )
        self.assertIn("degraded-fetch", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
