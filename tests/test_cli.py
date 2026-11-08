from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from music_app import cli
from music_app.models import DownloadRecord


def record(track):
    return DownloadRecord(
        track_id=track.track_id,
        title=track.title,
        artist=track.artist,
        path="/tmp/out.m4a",
        sha256="hash",
        size=1,
        source_url=track.webpage_url,
        downloaded_at="now",
        audio_format="m4a",
    )


def test_parser_validates_url_and_positive_numbers():
    parser = cli.build_parser()
    args = parser.parse_args(["--dry-run", "https://on.soundcloud.com/a"])
    assert args.urls == ["https://on.soundcloud.com/a"]
    with pytest.raises(SystemExit):
        parser.parse_args(["https://evil.test/a"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--retries", "0", "https://soundcloud.com/a"])


def test_resolve_tracks_handles_playlist_and_skips_invalid(monkeypatch):
    calls = {}

    class FakeYDL:
        def __init__(self, options):
            calls["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download=False):
            calls["url"] = url
            return {
                "entries": [
                    {
                        "id": "1",
                        "title": "A",
                        "uploader": "U",
                        "duration": 10,
                        "webpage_url": "https://soundcloud.com/a",
                    },
                    {"id": "bad", "webpage_url": "https://evil.test/b"},
                ]
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    tracks = cli.resolve_tracks(
        "https://soundcloud.com/playlist",
        cookies=None,
        proxy=None,
        no_playlist=False,
    )
    assert [track.track_id for track in tracks] == ["1"]
    assert calls["options"]["noplaylist"] is False
    assert calls["download" if False else "url"] == "https://soundcloud.com/playlist"


def test_print_tracks(capsys, track):
    cli.print_tracks([track])
    output = capsys.readouterr().out
    assert "成都" in output
    assert "5:28" in output
    assert track.webpage_url in output


def test_main_dry_run(monkeypatch, capsys, track):
    monkeypatch.setattr(cli, "resolve_tracks", lambda *_a, **_k: [track])
    code = cli.main(
        ["--dry-run", "https://on.soundcloud.com/a"]
    )
    assert code == 0
    assert "成都" in capsys.readouterr().out


def test_main_requires_rights():
    with pytest.raises(SystemExit):
        cli.main(["https://on.soundcloud.com/a"])


def test_main_success(monkeypatch, capsys, track, tmp_path):
    monkeypatch.setattr(cli, "resolve_tracks", lambda *_a, **_k: [track])
    seen = {}

    class FakeService:
        def __init__(self, *_args, settings=None, **_kwargs):
            seen["settings"] = settings

        def download(self, item, token, progress, *, rights_acknowledged):
            progress(SimpleNamespace(message="working"))
            seen["rights"] = rights_acknowledged
            return record(item)

    monkeypatch.setattr(cli, "DownloadService", FakeService)
    code = cli.main(
        [
            "--accept-rights",
            "-o",
            str(tmp_path),
            "https://on.soundcloud.com/a",
        ]
    )
    assert code == 0
    assert seen["rights"] is True
    assert "已保存" in capsys.readouterr().out


def test_main_handles_failures_and_interrupts(monkeypatch, capsys, track, tmp_path):
    monkeypatch.setattr(cli, "resolve_tracks", lambda *_a, **_k: [track])

    class FailureService:
        def __init__(self, *_args, **_kwargs):
            pass

        def download(self, *_args, **_kwargs):
            raise RuntimeError("failed")

    monkeypatch.setattr(cli, "DownloadService", FailureService)
    assert (
        cli.main(
            [
                "--accept-rights",
                "-o",
                str(tmp_path),
                "https://on.soundcloud.com/a",
            ]
        )
        == 1
    )

    class InterruptService(FailureService):
        def download(self, *_args, **_kwargs):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "DownloadService", InterruptService)
    assert (
        cli.main(
            [
                "--accept-rights",
                "-o",
                str(tmp_path),
                "https://on.soundcloud.com/a",
            ]
        )
        == 130
    )


def test_main_rejects_missing_cookie(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--dry-run",
                "--cookies",
                str(tmp_path / "missing.txt"),
                "https://on.soundcloud.com/a",
            ]
        )


def test_main_resolve_error_and_no_tracks(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "resolve_tracks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    assert (
        cli.main(
            [
                "--accept-rights",
                "-o",
                str(tmp_path),
                "https://on.soundcloud.com/a",
            ]
        )
        == 1
    )
    monkeypatch.setattr(cli, "resolve_tracks", lambda *_args, **_kwargs: [])
    assert (
        cli.main(
            [
                "--accept-rights",
                "-o",
                str(tmp_path),
                "https://on.soundcloud.com/a",
            ]
        )
        == 1
    )
