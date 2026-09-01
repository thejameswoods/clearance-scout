"""Retry behavior for Home Depot's sitemap fetch -- confirmed live
2026-09-01 that a single unretried timeout aborted an entire scheduled
scan over a normal, recoverable network hiccup."""

from __future__ import annotations

import pytest

from adapters.home_depot.departments import _fetch_locs


class _FakeResponse:
    def __init__(self, ok: bool, status: int = 200, text: str = ""):
        self.ok = ok
        self.status = status
        self._text = text

    def text(self) -> str:
        return self._text


class _FakeRequest:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)  # each: "timeout" | _FakeResponse
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        behavior = self.behaviors.pop(0)
        if behavior == "timeout":
            raise TimeoutError("simulated sitemap timeout")
        return behavior


class _FakeBrowserContext:
    def __init__(self, behaviors):
        self.request = _FakeRequest(behaviors)


def test_retries_on_timeout_then_succeeds():
    ctx = _FakeBrowserContext(["timeout", _FakeResponse(ok=True, text="<url><loc>https://x/b/Wire/N-abc</loc></url>")])

    locs = _fetch_locs(ctx, "https://example.com/sitemap.xml", retries=2, retry_delay_seconds=0)

    assert ctx.request.calls == 2
    assert locs == ["https://x/b/Wire/N-abc"]


def test_gives_up_and_raises_after_exhausting_retries():
    ctx = _FakeBrowserContext(["timeout", "timeout", "timeout"])

    with pytest.raises(TimeoutError):
        _fetch_locs(ctx, "https://example.com/sitemap.xml", retries=2, retry_delay_seconds=0)

    assert ctx.request.calls == 3  # initial attempt + 2 retries


def test_404_returns_empty_immediately_without_retrying():
    ctx = _FakeBrowserContext([_FakeResponse(ok=False, status=404)])

    locs = _fetch_locs(ctx, "https://example.com/sitemap.xml", retries=2, retry_delay_seconds=0)

    assert locs == []
    assert ctx.request.calls == 1  # a 404 can't improve on retry, so it doesn't use one


def test_non_404_error_response_retries_then_raises():
    ctx = _FakeBrowserContext([_FakeResponse(ok=False, status=500), _FakeResponse(ok=False, status=500)])

    with pytest.raises(RuntimeError):
        _fetch_locs(ctx, "https://example.com/sitemap.xml", retries=1, retry_delay_seconds=0)

    assert ctx.request.calls == 2
