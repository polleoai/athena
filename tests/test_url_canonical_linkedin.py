"""LinkedIn URL forms that carry the post URN in the QUERY, not the path.

LinkedIn's notification and email links use
`/feed/?highlightedUpdateUrn=urn:li:activity:<ID>` — the post's identity
lives entirely in the query string. `linkedin.com` is in
`_STRIP_ALL_PARAMS_HOSTS`, so without an explicit normalization these URLs
canonicalize to bare `https://linkedin.com/feed`: the logged-out feed root.

Three consequences, all from that one collapse:
  1. Capture fetches the feed login wall instead of the post.
  2. EVERY such URL shares the `linkedin.com/feed` dedup key, so the second
     one is reported as an "exists" duplicate of the first, unrelated post.
  3. `resolve_linkedin` is gated on `linkedin.com/posts/` appearing in the
     canonical, so the authenticated capture-deep path never fires.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin" / "lib"))

from url_canonical import canonicalize  # noqa: E402

ACTIVITY_ID = "7506400425349103616"
CANONICAL = f"https://linkedin.com/posts/ugcpost-{ACTIVITY_ID}"


def test_highlighted_update_urn_keeps_post_identity():
    """The notification form must not collapse to the bare feed root."""
    url = (
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn%3Ali%3Aactivity%3A{ACTIVITY_ID}"
        "&rcm=ACoAAAALAeoBlS0hZ-2JY1jOHjCYl-9WhCkrlj4"
    )
    assert canonicalize(url).url == CANONICAL


def test_unencoded_urn_form_also_normalizes():
    """Pasted-from-address-bar URLs arrive with the colons unescaped."""
    url = (
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn:li:activity:{ACTIVITY_ID}"
    )
    assert canonicalize(url).url == CANONICAL


def test_ugcpost_urn_type_in_query_normalizes():
    """highlightedUpdateUrn carries ugcPost URNs too, not just activity."""
    url = (
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn%3Ali%3AugcPost%3A{ACTIVITY_ID}"
    )
    assert canonicalize(url).url == CANONICAL


def test_agrees_with_the_path_form_for_the_same_post():
    """The notification link and the permalink are the SAME post.

    This is the duplicate-page bug class `_normalize_linkedin` exists to
    prevent (0.9.13 Praneeta, 0.10.11) — a post clipped via two URL forms
    must produce one key, hence one wiki page.
    """
    from_query = canonicalize(
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn%3Ali%3Aactivity%3A{ACTIVITY_ID}"
    ).url
    from_path = canonicalize(
        f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY_ID}/"
    ).url
    assert from_query == from_path


def test_two_different_posts_do_not_share_a_key():
    """The collision-bucket regression: distinct posts, distinct keys."""
    a = canonicalize(
        "https://www.linkedin.com/feed/?highlightedUpdateUrn=urn%3Ali%3Aactivity%3A1111111111111111111"
    ).url
    b = canonicalize(
        "https://www.linkedin.com/feed/?highlightedUpdateUrn=urn%3Ali%3Aactivity%3A2222222222222222222"
    ).url
    assert a != b


def test_bare_feed_url_is_left_alone():
    """A genuine feed root has no post identity and must stay as it is."""
    assert canonicalize("https://www.linkedin.com/feed/").url == "https://linkedin.com/feed"


def test_normalization_is_idempotent():
    once = canonicalize(
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn%3Ali%3Aactivity%3A{ACTIVITY_ID}"
    ).url
    assert canonicalize(once).url == once


# ─── Resolvable (navigation) form ────────────────────────────────────
# canonicalize() produces the dedup IDENTITY. The network layer needs a URL
# LinkedIn will actually serve. For the notification form those differ twice
# over: the canonical /posts/ugcpost-<id> key is synthetic (see
# is_unservable_canonical), and the ORIGINAL /feed/?highlightedUpdateUrn=...
# renders the whole feed with the post merely highlighted — which is how the
# "LinkedIn Feed Snapshot" pages full of unrelated posts got captured.

def test_resolvable_url_rewrites_notification_link_to_the_permalink():
    from url_canonical import resolvable_url
    url = (
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn%3Ali%3Aactivity%3A{ACTIVITY_ID}&rcm=ACoAAAALAeoB"
    )
    assert resolvable_url(url) == (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY_ID}/"
    )


def test_resolvable_url_preserves_the_urn_type():
    """activity and ugcPost are DIFFERENT namespaces.

    The canonical key drops the type deliberately (identity only needs the
    numeric id). A permalink must keep it — the wrong type yields LinkedIn's
    "Invalid post link" page, the failure this whole split exists to avoid.
    """
    from url_canonical import resolvable_url
    url = (
        "https://www.linkedin.com/feed/?highlightedUpdateUrn="
        f"urn%3Ali%3AugcPost%3A{ACTIVITY_ID}"
    )
    assert resolvable_url(url) == (
        f"https://www.linkedin.com/feed/update/urn:li:ugcPost:{ACTIVITY_ID}/"
    )


def test_resolvable_url_leaves_ordinary_urls_untouched():
    from url_canonical import resolvable_url
    for url in (
        "https://example.com/a?b=c",
        "https://www.linkedin.com/feed/",
        f"https://www.linkedin.com/posts/someone_thing-activity-{ACTIVITY_ID}-aB1c",
        "",
    ):
        assert resolvable_url(url) == url
