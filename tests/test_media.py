from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_app.errors import AppError, TaskCancelled
from music_app.media import (
    PreviewCache,
    PreviewService,
    ProcessRunner,
    ResolvedMedia,
    YtDlpMediaResolver,
)
from music_app.security import CancellationToken


class FakeResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, track, token):
        self.calls += 1
        token.raise_if_cancelled()
        return ResolvedMedia("https://media.test/a.m3u8", {"User-Agent": "x"}, 100)


class FakeRunner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.command = None

    def run(self, command, token, timeout):
        token.raise_if_cancelled()
        self.command = command
        Path(command[-1]).write_bytes(b"x" * 2000)
        return self.returncode, "", "error" if self.returncode else ""


def test_preview_cache_lru_and_preserve(tmp_path):
    cache = PreviewCache(tmp_path, max_bytes=2000)
    one = tmp_path / "1.mp3"
    two = tmp_path / "2.mp3"
    three = tmp_path / "3.mp3"
    for path in (one, two, three):
        path.write_bytes(b"x" * 800)
    cache.add("1", one)
    cache.add("2", two)
    assert cache.get("1") == one
    cache.add("3", three)
    assert cache.get("2") is None
    assert one.exists() and three.exists()
    assert cache.get("missing") is None


def test_preview_cache_preserves_current_file_when_possible(tmp_path):
    cache = PreviewCache(tmp_path, 1024)
    one = tmp_path / "1.mp3"
    two = tmp_path / "2.mp3"
    one.write_bytes(b"x" * 800)
    two.write_bytes(b"x" * 800)
    cache.add("1", one, preserve={"1"})
    cache.add("2", two, preserve={"1"})
    assert one.exists()


def test_preview_service_success_and_cache(tmp_path, track):
    resolver = FakeResolver()
    runner = FakeRunner()
    service = PreviewService(
        PreviewCache(tmp_path, 1024 * 1024),
        resolver=resolver,
        runner=runner,
        ffmpeg_finder=lambda: "/ffmpeg",
    )
    path = service.prepare(track, CancellationToken())
    assert path.suffix == ".mp3"
    assert path.exists()
    assert "-headers" in runner.command
    assert path == service.prepare(track, CancellationToken())
    assert resolver.calls == 1


def test_preview_service_errors(tmp_path, track):
    service = PreviewService(
        PreviewCache(tmp_path, 1024 * 1024),
        resolver=FakeResolver(),
        runner=FakeRunner(),
        ffmpeg_finder=lambda: None,
    )
    with pytest.raises(AppError) as caught:
        service.prepare(track, CancellationToken())
    assert caught.value.code == "ffmpeg_missing"

    token = CancellationToken()
    token.cancel()
    service.ffmpeg_finder = lambda: "/ffmpeg"
    with pytest.raises(TaskCancelled):
        service.prepare(track, token)

    failed = PreviewService(
        PreviewCache(tmp_path / "failed", 1024 * 1024),
        resolver=FakeResolver(),
        runner=FakeRunner(returncode=1),
        ffmpeg_finder=lambda: "/ffmpeg",
    )
    with pytest.raises(AppError):
        failed.prepare(track, CancellationToken())


def test_preview_service_cleans_partial_file_on_cancel(tmp_path, track):
    class CancellingRunner:
        def run(self, command, token, timeout):
            Path(command[-1]).write_bytes(b"partial")
            raise TaskCancelled()

    service = PreviewService(
        PreviewCache(tmp_path, 1024 * 1024),
        resolver=FakeResolver(),
        runner=CancellingRunner(),
        ffmpeg_finder=lambda: "/ffmpeg",
    )
    with pytest.raises(TaskCancelled):
        service.prepare(track, CancellationToken())
    assert not (tmp_path / f"{track.track_id}.part.mp3").exists()


def test_process_runner_success_and_cancel(monkeypatch):
    events = []

    class Process:
        returncode = 0

        def __init__(self, *_args, **_kwargs):
            self.calls = 0

        def poll(self):
            self.calls += 1
            return 0

        def communicate(self):
            return "out", "err"

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            events.append("kill")

    monkeypatch.setattr("music_app.media.subprocess.Popen", Process)
    runner = ProcessRunner()
    assert runner.run(["x"], CancellationToken(), 5) == (0, "out", "err")

    class RunningProcess(Process):
        def poll(self):
            return None

    # Force cancellation after Popen and before the next loop check.
    class CancelToken:
        def __init__(self):
            self.calls = 0

        def is_cancelled(self):
            self.calls += 1
            return self.calls > 1

        def raise_if_cancelled(self):
            raise TaskCancelled()

    monkeypatch.setattr("music_app.media.subprocess.Popen", RunningProcess)
    with pytest.raises(TaskCancelled):
        runner.run(["x"], CancelToken(), 5)
    assert "terminate" in events


def test_ytdlp_media_resolver(monkeypatch):
    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            return {
                "duration": 120,
                "http_headers": {"Referer": "x"},
                "formats": [
                    {
                        "url": "https://media/a",
                        "acodec": "mp4a",
                        "vcodec": "none",
                        "abr": 96,
                    },
                    {
                        "url": "https://media/b",
                        "acodec": "mp4a",
                        "vcodec": "none",
                        "abr": 160,
                    },
                    {"url": "bad", "acodec": "none", "vcodec": "vp9"},
                ],
            }

    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        SimpleNamespace(YoutubeDL=FakeYDL),
    )
    media = YtDlpMediaResolver().resolve(
        type("Track", (), {"webpage_url": "https://soundcloud.com/a"})(),
        CancellationToken(),
    )
    assert media.url == "https://media/b"
    assert media.headers["Referer"] == "x"
    assert media.duration == 120


def test_ytdlp_media_resolver_fallback_and_no_stream(monkeypatch):
    responses = [
        {
            "duration": 5,
            "url": "https://media/direct",
            "acodec": "mp3",
            "vcodec": "none",
        },
        {"formats": []},
    ]

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            return responses.pop(0)

    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        SimpleNamespace(YoutubeDL=FakeYDL),
    )
    resolver = YtDlpMediaResolver()
    media = resolver.resolve(
        type("Track", (), {"webpage_url": "https://soundcloud.com/a"})(),
        CancellationToken(),
    )
    assert media.url == "https://media/direct"
    with pytest.raises(AppError):
        resolver.resolve(
            type("Track", (), {"webpage_url": "https://soundcloud.com/a"})(),
            CancellationToken(),
        )


def test_process_runner_timeout(monkeypatch):
    events = []

    class Process:
        returncode = -9

        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            return 0

        def communicate(self):
            return "", "timed out"

    ticks = iter([0.0, 10.0, 10.0])
    monkeypatch.setattr("music_app.media.subprocess.Popen", Process)
    monkeypatch.setattr("music_app.media.time.monotonic", lambda: next(ticks))
    with pytest.raises(AppError) as caught:
        ProcessRunner().run(["x"], CancellationToken(), 1)
    assert caught.value.code == "preview_timeout"
    assert events == ["kill"]
