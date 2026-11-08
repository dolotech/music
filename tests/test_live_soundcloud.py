from __future__ import annotations

import os
import threading
import time

import pytest

from music_app.errors import TaskCancelled
from music_app.media import PreviewCache, PreviewService
from music_app.models import TrackCandidate
from music_app.security import CancellationToken

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("RUN_NETWORK_TESTS") != "1",
        reason="set RUN_NETWORK_TESTS=1 to run live SoundCloud tests",
    ),
]


def test_live_search_contract():
    from music_app.catalog import SearchService, YtDlpSearchBackend

    results = SearchService(
        YtDlpSearchBackend(),
        cache_ttl_seconds=0,
        min_interval_seconds=0,
    ).search("成都 赵雷", 5)
    assert results
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

    thread = threading.Thread(target=worker)
    thread.start()
    partial = tmp_path / f"{track.track_id}.part.mp3"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if partial.exists() and partial.stat().st_size > 0:
            break
        time.sleep(0.05)
    assert partial.exists()
    token.cancel("test cancellation")
    thread.join(5)
    assert not thread.is_alive()
    assert result == ["cancelled"]
    assert not partial.exists()
