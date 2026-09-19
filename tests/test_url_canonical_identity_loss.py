"""Canonicalization must not silently discard a URL's identity.

`canonicalize` decides what to drop by matching host and param NAMES. It has
no notion of whether the part it dropped was carrying the identity of the
content. Three gaps followed from that, all fixed here:

  A. Ambiguous short params (`s`, `t`, `ref`) were on the GLOBAL denylist, so
     they were deleted on every host — including hosts where they name the
     content. They stay stripped on the strip-all social hosts, where they are
     documented tracking noise.
  B. LinkedIn carried post identity in the query on more than one param name.
     Query identity is now lifted into the path for ANY LinkedIn path, with
     strip-all kept as the backstop so no raw param survives into a canonical.
  C. Anything not anticipated by A or B must FAIL LOUDLY rather than resolve to
     a content-less root. `CanonicalizeResult.identity_lost` marks the case.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "lib"))

from url_canonical import canonicalize  # noqa: E402

ID = "7506400425349103616"


# ─── A. ambiguous short params ───────────────────────────────────────
def test_ambiguous_short_params_survive_on_ordinary_hosts():
    for url in (
        f"https://example.com/view?s=A7KD92",
        f"https://forum.example.org/thread?t=88421",
        f"https://docs.example.io/page?ref=section-4",
    ):
        assert canonicalize(url).url == url, f"identity dropped from {url}"


def test_same_params_still_stripped_on_social_hosts():
    """x.com documents ?s= and ?t= as share tracking; strip-all still wins."""
    assert canonicalize(
        "https://x.com/regent0x_/status/2049499354323399002?s=12&t=SqfI4N"
    ).url == "https://x.com/regent0x_/status/2049499354323399002"


def test_proven_tracking_params_still_stripped_everywhere():
    """`usp` occurs 3x in the vault corpus and is tracking in all 3."""
    assert canonicalize(
        "https://drive.google.com/file/d/10fTanszc6DiRMvF9xvVdUouokxUI6u9A/view?usp=drivesdk"
    ).url == "https://drive.google.com/file/d/10fTanszc6DiRMvF9xvVdUouokxUI6u9A/view"
    assert canonicalize("https://example.com/a?utm_source=x&fbclid=y").url == \
        "https://example.com/a"


# ─── B. LinkedIn query identity on any path ──────────────────────────
def test_linkedin_urn_params_all_lift_to_the_same_key():
    keys = {
        canonicalize(
            f"https://www.linkedin.com/feed/?{param}=urn%3Ali%3Aactivity%3A{ID}"
        ).url
        for param in ("highlightedUpdateUrn", "shareUrn", "updateUrn")
    }
    assert keys == {f"https://linkedin.com/posts/ugcpost-{ID}"}


def test_linkedin_path_identity_wins_over_a_stray_query_urn():
    """An explicit post path is authoritative; a stray param must not hijack
    it, and must not survive into the canonical either (duplicate-page class)."""
    assert canonicalize(
        f"https://www.linkedin.com/feed/update/urn:li:activity:{ID}/"
        "?highlightedUpdateUrn=urn%3Ali%3Aactivity%3A1111111111111111111"
    ).url == f"https://linkedin.com/posts/ugcpost-{ID}"


def test_linkedin_tracking_params_still_die():
    assert canonicalize(
        "https://www.linkedin.com/posts/jimmymesta_how-do-you-secure-activity-7447273590000000000-aB1c"
        "?rcm=ACoA&utm_source=share&utm_medium=ios_app"
    ).url == "https://linkedin.com/posts/ugcpost-7447273590000000000"


# ─── C. the catch-all invariant ──────────────────────────────────────
def test_identity_loss_flagged_when_canonical_becomes_a_content_less_root():
    r = canonicalize(
        "https://www.linkedin.com/feed/?someFutureParam=urn%3Ali%3Aactivity%3A" + ID
    )
    assert r.identity_lost, "a dropped non-tracking query reaching /feed must flag"


def test_route_shaped_fragments_flagged():
    for url in (
        "https://app.example.com/#/post/12345",
        "https://groups.google.com/forum#!topic/comp.lang.python/abc123",
    ):
        assert canonicalize(url).identity_lost, f"{url} lost its route silently"


def test_ordinary_urls_are_not_flagged():
    """The flag must stay quiet on healthy input, or nobody will read it."""
    for url in (
        "https://example.com/guide#installation",      # anchor, decoration
        "https://heygen.com/?utm_source=x",            # homepage + tracking only
        "https://claude.md/",                          # bare homepage, no query
        "https://arxiv.org/abs/2405.21060v3",
        f"https://www.linkedin.com/feed/?highlightedUpdateUrn=urn%3Ali%3Aactivity%3A{ID}",
        "https://www.youtube.com/watch?v=abc123&utm_source=share",
        "https://drive.google.com/file/d/10fTan/view?usp=drivesdk",
    ):
        assert not canonicalize(url).identity_lost, f"false positive on {url}"


# ─── The notice, and who acts on it ──────────────────────────────────
# Three ingest entry points canonicalize a user-supplied URL. They need the
# same diagnosis but NOT the same response:
#   kb add / unified ingest  — fetch FROM the url, so a shared key means
#                              fetching the wrong thing. Refuse.
#   Web Clipper              — already holds content the browser pulled, so
#                              refusing would discard a good capture. Warn.
# The wording lives in one place so the three cannot drift apart.

def test_notice_is_none_for_healthy_urls():
    from url_canonical import identity_loss_notice
    for url in ("https://example.com/guide#installation",
                "https://arxiv.org/abs/2405.21060",
                "https://www.youtube.com/watch?v=abc123&utm_source=share"):
        assert identity_loss_notice(url) is None


def test_notice_names_the_url_and_the_shared_key():
    from url_canonical import identity_loss_notice
    msg = identity_loss_notice(
        "https://www.linkedin.com/feed/?someFutureParam=urn%3Ali%3Aactivity%3A" + ID)
    assert msg is not None
    assert "https://linkedin.com/feed" in msg, "must name the key it collapsed onto"
    assert "permalink" in msg.lower(), "must say what to do instead"


def test_refusal_respects_the_override(monkeypatch):
    from url_canonical import identity_loss_refusal
    url = "https://www.linkedin.com/feed/?someFutureParam=urn%3Ali%3Aactivity%3A" + ID
    monkeypatch.delenv("ATHENA_ALLOW_IDENTITYLESS", raising=False)
    assert identity_loss_refusal(url) is not None
    monkeypatch.setenv("ATHENA_ALLOW_IDENTITYLESS", "1")
    assert identity_loss_refusal(url) is None, "override must let the capture through"


def test_unified_ingest_refuses_an_identityless_url(monkeypatch):
    import unified_ingest
    monkeypatch.delenv("ATHENA_ALLOW_IDENTITYLESS", raising=False)
    inp = unified_ingest.IngestInput(
        url="https://www.linkedin.com/feed/?someFutureParam=urn%3Ali%3Aactivity%3A" + ID,
        vault_root=Path("/tmp"),
    )
    try:
        unified_ingest.ingest(inp)
    except unified_ingest.UnifiedIngestError as exc:
        assert "linkedin.com/feed" in str(exc)
    else:
        raise AssertionError("unified ingest accepted an identity-less URL")


def test_process_clip_warns_but_does_not_refuse():
    """The clipper's response is a warning — it must not raise."""
    import inspect, process_clip
    src = inspect.getsource(process_clip)
    assert "identity_loss_notice" in src, "clipper must consult the notice"
    assert "raise ProcessClipError(identity" not in src, \
        "clipper must warn, not refuse — it already holds real content"
