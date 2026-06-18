"""Shared pytest fixtures for the Athena test suite."""
import pytest


@pytest.fixture(autouse=True)
def _hermetic_tweet_resync(monkeypatch):
    """Keep the suite hermetic: the Web-Clipper /status/ re-sync makes a live
    syndication-CDN call during ingest. Disable it by default so tests that
    happen to feed an x.com /status/ web-clipper clip (pipeline-parity, writers)
    don't hit the network. Tests that exercise the re-sync re-enable it
    explicitly and mock `fetch_tweet.fetch_tweet_json`.
    """
    monkeypatch.setenv("ATHENA_DISABLE_TWEET_RESYNC", "1")
