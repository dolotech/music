from __future__ import annotations

import pytest

from music_app.catalog import (
    MinIntervalLimiter,
    SearchService,
    TTLCache,
    YtDlpSearchBackend,
)
from music_app.errors import TaskCancelled
from music_app.security import CancellationToken


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeBackend:
    def __init__(self, entries=None, error=None):
        self.entries = entries or []
        self.error = error
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.entries


def sample_entries():
    return [
        {
            "id": "1",
            "title": "成都",
            "uploader": "赵雷",
            "duration": 300,
            "like_count": 10,
            "webpage_url": "https://soundcloud.com/a/1",
        },
        {
            "id": "1",
            "title": "duplicate",
            "webpage_url": "https://soundcloud.com/a/duplicate",
        },
        {
            "id": "bad",
            "title": "bad",
            "webpage_url": "https://soundcloud.com.evil.test/bad",
        },
        "not a dict",
        {"id": "2", "title": "without url"},
    ]


def test_search_service_filters_deduplicates_and_caches():
    clock = Clock()
    backend = FakeBackend(sample_entries())
    service = SearchService(
        backend,
        cache_ttl_seconds=10,
        min_interval_seconds=0,
        clock=clock,
    )
    first = service.search("成都", 10)
    second = service.search("  成都 ", 10)
    assert [item.track_id for item in first] == ["1"]
    assert second == first
    assert backend.calls == [("成都", 10)]
    assert second is not first


def test_search_cache_expiry_and_empty_query():
    clock = Clock()
    backend = FakeBackend(sample_entries())
    service = SearchService(
        backend,
        cache_ttl_seconds=5,
        min_interval_seconds=0,
        clock=clock,
    )
    assert service.search("   ") == []
    service.search("成都", 10)
    clock.value = 6
    service.search("成都", 10)
    assert len(backend.calls) == 2


def test_search_service_propagates_backend_errors():
    service = SearchService(
        FakeBackend(error=RuntimeError("offline")),
        min_interval_seconds=0,
        sleeper=lambda _delay: None,
    )
    with pytest.raises(RuntimeError):
        service.search("x")


def test_search_cancellation_before_backend():
    backend = FakeBackend()
    token = CancellationToken()
    token.cancel()
    service = SearchService(backend)
    with pytest.raises(TaskCancelled):
        service.search("x", token=token)
    assert backend.calls == []


def test_ttl_cache_set_clear():
    clock = Clock()
    cache = TTLCache(1, clock)
    cache.set(("x", 1), [])
    assert cache.get(("x", 1)) == []
    cache.clear()
    assert cache.get(("x", 1)) is None


def test_min_interval_limiter_waits_and_honors_token():
    clock = Clock()
    sleeps = []
    limiter = MinIntervalLimiter(
        2,
        clock,
        lambda delay: sleeps.append(delay),
    )
    limiter.wait()
    clock.value = 0.5
    limiter.wait()
    assert sleeps == [1.5]

    waits = []
    token = CancellationToken()

    class FalseEvent:
        def wait(self, timeout):
            waits.append(timeout)
            return False

    token._event = FalseEvent()
    clock.value = 0.6
    limiter.wait(token)
    assert waits == [1.9]


def test_ytdlp_backend_uses_options(monkeypatch):
    calls = {}

    class FakeYDL:
        def __init__(self, options):
            calls["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download):
            calls["url"] = url
            calls["download"] = download
            return {"entries": [{"id": "1"}]}

    monkeypatch.setitem(
        __import__("sys").modules,
        "yt_dlp",
        type("YDLModule", (), {"YoutubeDL": FakeYDL}),
    )
    entries = YtDlpSearchBackend().search("query", 7)
    assert entries == [{"id": "1"}]
    assert calls["url"] == "scsearch7:query"
    assert calls["download"] is False
