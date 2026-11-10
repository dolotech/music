from __future__ import annotations

import sys
from types import ModuleType
from urllib.error import HTTPError, URLError

import pytest

from music_app.catalog import (
    MinIntervalLimiter,
    SearchService,
    SoundCloudFastSearchBackend,
    TTLCache,
    YtDlpSearchBackend,
)
from music_app.errors import NetworkError, TaskCancelled
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

    yt_dlp_module = ModuleType("yt_dlp")
    yt_dlp_module.YoutubeDL = FakeYDL
    networking_module = ModuleType("yt_dlp.networking")
    impersonate_module = ModuleType("yt_dlp.networking.impersonate")
    impersonate_module.ImpersonateTarget = type(
        "ImpersonateTarget",
        (),
        {"from_str": classmethod(lambda _cls, value: f"target:{value}")},
    )
    yt_dlp_module.networking = networking_module
    monkeypatch.setitem(sys.modules, "yt_dlp", yt_dlp_module)
    monkeypatch.setitem(sys.modules, "yt_dlp.networking", networking_module)
    monkeypatch.setitem(
        sys.modules,
        "yt_dlp.networking.impersonate",
        impersonate_module,
    )
    entries = YtDlpSearchBackend().search("query", 7)
    assert entries == [{"id": "1"}]
    assert calls["url"] == "scsearch7:query"
    assert calls["download"] is False
    assert "impersonate" in calls["options"]


def test_fast_search_backend_normalizes_api_results():
    payload = {
        "collection": [
            {
                "kind": "track",
                "id": 1,
                "title": "成都",
                "duration": 328000,
                "likes_count": 131,
                "permalink_url": "https://soundcloud.com/a/b",
                "downloadable": False,
                "license": "all-rights-reserved",
                "policy": "ALLOW",
                "user": {"username": "赵雷"},
            },
            {
                "kind": "track",
                "id": 2,
                "title": "blocked",
                "permalink_url": "https://soundcloud.com/blocked",
                "policy": "BLOCK",
            },
            {"kind": "playlist"},
        ]
    }
    urls = []
    backend = SoundCloudFastSearchBackend(
        client_id_provider=lambda: "client-id",
        request_json=lambda url: urls.append(url) or payload,
    )
    entries = backend.search("成都", 12)
    assert len(entries) == 1
    assert entries[0]["duration"] == 328
    assert entries[0]["uploader"] == "赵雷"
    assert "client_id=client-id" in urls[0]


def test_fast_search_backend_falls_back_without_client_id():
    fallback = FakeBackend([{"id": "fallback"}])
    backend = SoundCloudFastSearchBackend(
        fallback=fallback,
        client_id_provider=lambda: None,
    )
    assert backend.search("query", 30) == [{"id": "fallback"}]
    assert fallback.calls == [("query", 15)]


def test_fast_search_backend_refreshes_on_auth_error():
    fallback = FakeBackend([{"id": "fallback"}])

    def unauthorized(_url):
        raise HTTPError("https://api", 401, "unauthorized", {}, None)

    backend = SoundCloudFastSearchBackend(
        fallback=fallback,
        client_id_provider=lambda: "expired",
        request_json=unauthorized,
    )
    assert backend.search("query", 10) == [{"id": "fallback"}]
    assert fallback.calls
    assert backend._client_id is None


def test_fast_search_backend_uses_fallback_after_network_error():
    def offline(_url):
        raise NetworkError("offline")

    fallback = FakeBackend([{"id": "fallback"}])
    backend = SoundCloudFastSearchBackend(
        fallback=fallback,
        client_id_provider=lambda: "client-id",
        request_json=offline,
    )
    assert backend.search("query", 30) == [{"id": "fallback"}]
    assert fallback.calls == [("query", 15)]


def test_fast_search_backend_surfaces_network_error_if_fallback_fails():
    def offline(_url):
        raise NetworkError("direct offline")

    backend = SoundCloudFastSearchBackend(
        fallback=FakeBackend(error=NetworkError("fallback offline")),
        client_id_provider=lambda: "client-id",
        request_json=offline,
    )
    with pytest.raises(NetworkError) as caught:
        backend.search("query", 10)
    assert caught.value.detail == "direct offline"


def test_search_service_logs_cache_hit():
    class Logger:
        def __init__(self):
            self.events = []

        def info(self, message, *args, extra=None):
            self.events.append((message, extra))

    logger = Logger()
    service = SearchService(
        SoundCloudFastSearchBackend(
            client_id_provider=lambda: "client",
            request_json=lambda _url: {"collection": sample_entries()},
        ),
        min_interval_seconds=0,
        logger=logger,
    )
    service.search("成都", 10)
    service.search("成都", 10)
    assert any(item[1]["event"] == "search_cache_hit" for item in logger.events)


def test_fast_search_backend_missing_client_id_falls_back():
    fallback = FakeBackend([{"id": "fallback"}])
    backend = SoundCloudFastSearchBackend(
        fallback=fallback,
        client_id_provider=lambda: None,
    )
    assert backend.search("query", 10) == [{"id": "fallback"}]


def test_fast_search_backend_http_exception_falls_back():
    fallback = FakeBackend([{"id": "fallback"}])

    def broken(_url):
        raise ValueError("bad payload")

    backend = SoundCloudFastSearchBackend(
        fallback=fallback,
        client_id_provider=lambda: "client",
        request_json=broken,
    )
    assert backend.search("query", 10) == [{"id": "fallback"}]


def test_fast_search_backend_network_request(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"collection": []}'

    monkeypatch.setattr(
        "music_app.catalog.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    assert SoundCloudFastSearchBackend._request_json(
        "https://api.test",
        2,
    ) == {"collection": []}


def test_fast_search_backend_request_network_error(monkeypatch):
    def offline(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr("music_app.catalog.urlopen", offline)
    with pytest.raises(NetworkError):
        SoundCloudFastSearchBackend._request_json("https://api.test", 2)
