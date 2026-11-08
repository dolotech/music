from __future__ import annotations

import os

import pytest

from music_app.models import TrackCandidate

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def track() -> TrackCandidate:
    return TrackCandidate(
        track_id="722635666",
        title="成都",
        artist="고현",
        duration=328.409,
        webpage_url="https://soundcloud.com/example/chengdu",
        score=0.9,
        likes=339,
        downloadable=True,
    )
