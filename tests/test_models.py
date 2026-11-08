from __future__ import annotations

import pytest

from music_app.models import (
    TrackCandidate,
    format_count,
    format_duration,
    fuzzy_score,
    normalize_text,
    track_from_entry,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "--:--"),
        (-1, "--:--"),
        (0, "0:00"),
        (65, "1:05"),
        (3661, "1:01:01"),
    ],
)
def test_format_duration(value, expected):
    assert format_duration(value) == expected


def test_format_count():
    assert format_count(999) == "999"
    assert format_count(12345) == "1.2万"


def test_normalize_text_handles_unicode():
    assert normalize_text(" Ｃｈéng 都! ") == "chéng都"


def test_fuzzy_score_prefers_exact_title_and_artist():
    exact = fuzzy_score("赵雷 成都", "成都", "赵雷")
    unrelated = fuzzy_score("赵雷 成都", "完全不同的歌曲", "另一个歌手")
    assert exact > unrelated
    assert fuzzy_score("", "成都", "赵雷") == 0.0
    assert fuzzy_score("成都", "", "赵雷") == 0.0


def test_track_from_entry_parses_metadata():
    candidate = track_from_entry(
        {
            "id": "123",
            "title": "成都",
            "uploader": "赵雷",
            "duration": 300,
            "like_count": "131",
            "webpage_url": "https://soundcloud.com/a/b",
            "downloadable": True,
            "is_drm": False,
            "license": "all-rights-reserved",
        },
        "成都",
    )
    assert candidate is not None
    assert candidate.track_id == "123"
    assert candidate.likes == 131
    assert candidate.downloadable is True
    assert candidate.is_drm is False
    assert candidate.duration_text == "5:00"


def test_track_from_entry_defaults_and_rejects_missing_url():
    assert track_from_entry({"title": "x"}, "x") is None
    candidate = track_from_entry(
        {
            "id": 2,
            "title": "",
            "duration": "bad",
            "like_count": "bad",
            "webpage_url": "https://soundcloud.com/x",
            "downloadable": "yes",
            "is_drm": "no",
        },
        "",
    )
    assert candidate is not None
    assert candidate.title == "未命名歌曲"
    assert candidate.artist == "未知作者"
    assert candidate.duration is None
    assert candidate.likes == 0
    assert candidate.downloadable is None
    assert candidate.is_drm is None


def test_track_properties(track):
    assert track.duration_text == "5:28"
    assert track.score_text == "90%"
    assert track.likes_text == "339"
    assert isinstance(track, TrackCandidate)
