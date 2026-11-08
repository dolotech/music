from __future__ import annotations

import sys
from types import SimpleNamespace

from music_app.ffmpeg_tools import find_ffmpeg


def test_find_ffmpeg_prefers_system(monkeypatch):
    monkeypatch.setattr("music_app.ffmpeg_tools.shutil.which", lambda _name: "/ffmpeg")
    assert find_ffmpeg() == "/ffmpeg"


def test_find_ffmpeg_falls_back_to_imageio(monkeypatch):
    monkeypatch.setattr("music_app.ffmpeg_tools.shutil.which", lambda _name: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "/bundled"),
    )
    assert find_ffmpeg() == "/bundled"


def test_find_ffmpeg_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr("music_app.ffmpeg_tools.shutil.which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    assert find_ffmpeg() is None
