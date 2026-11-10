from __future__ import annotations

import os
import threading
import time

import pytest

from music_app.errors import TaskCancelled
from music_app.media import PreviewCache, PreviewService, YtDlpMediaResolver
from music_app.models import TrackCandidate
from music_app.player import StreamingPcmBackend
from music_app.security import CancellationToken

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("RUN_NETWORK_TESTS") != "1",
        reason="set RUN_NETWORK_TESTS=1 to run live SoundCloud tests",
    ),
]


def resolve_media_or_skip(track):
    last_error = None
    for _attempt in range(3):
        try:
            return YtDlpMediaResolver().resolve(track, CancellationToken())
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    pytest.skip(f"SoundCloud media API unavailable: {last_error}")


def test_live_search_contract():
    from music_app.catalog import SearchService, SoundCloudFastSearchBackend

    started = time.monotonic()
    results = SearchService(
        SoundCloudFastSearchBackend(),
        cache_ttl_seconds=0,
        min_interval_seconds=0,
    ).search("成都 赵雷", 5)
    elapsed = time.monotonic() - started
    assert results
    assert elapsed < 15
    assert all(
        item.webpage_url.startswith("https://soundcloud.com/")
        for item in results
    )


def test_live_preview_transfer_can_be_cancelled(tmp_path):
    track = TrackCandidate(
        track_id="722635666",
        title="成都",
        artist="고현",
        duration=328.409,
        webpage_url="https://soundcloud.com/4pcll7h0u1ie/9osysemxdfxa",
        score=1.0,
        likes=339,
        downloadable=True,
    )
    token = CancellationToken()
    service = PreviewService(PreviewCache(tmp_path, 64 * 1024 * 1024))
    result = []

    def worker():
        try:
            service.prepare(track, token)
            result.append("completed")
        except TaskCancelled:
            result.append("cancelled")
        except Exception as exc:
            result.append(f"error:{exc}")

    thread = threading.Thread(target=worker)
    thread.start()
    partial = tmp_path / f"{track.track_id}.part.mp3"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if partial.exists() and partial.stat().st_size > 0:
            break
        time.sleep(0.05)
    if not partial.exists():
        token.cancel("live stream did not produce data")
        thread.join(5)
        detail = result[0] if result else "no first packet"
        pytest.skip(f"SoundCloud media API unavailable: {detail}")
    token.cancel("test cancellation")
    thread.join(5)
    assert not thread.is_alive()
    assert result == ["cancelled"]
    assert not partial.exists()


def test_live_streaming_backend_consumes_pcm_without_cache_file(tmp_path):
    track = TrackCandidate(
        track_id="722635666",
        title="成都",
        artist="고현",
        duration=328.409,
        webpage_url="https://soundcloud.com/4pcll7h0u1ie/9osysemxdfxa",
        score=1.0,
        likes=339,
        downloadable=True,
    )
    media = resolve_media_or_skip(track)

    class DiscardSink:
        def __init__(self):
            self.bytes = 0

        def write(self, chunk):
            self.bytes += len(chunk)

        def abort(self):
            pass

    sink = DiscardSink()
    backend = StreamingPcmBackend(output_factory=lambda: sink)
    backend.load(media)
    backend.play(0)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and sink.bytes < 64 * 1024:
        time.sleep(0.05)
    backend.pause()
    if sink.bytes < 64 * 1024:
        pytest.skip(
            "SoundCloud CDN returned no PCM data: "
            f"{backend.last_error or 'startup timeout'}"
        )
    assert not backend.get_busy()
    assert list(tmp_path.iterdir()) == []
