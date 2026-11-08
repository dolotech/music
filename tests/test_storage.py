from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from music_app.errors import InvalidMediaError
from music_app.models import AudioInfo, DownloadRecord
from music_app.storage import (
    DownloadRepository,
    DownloadStaging,
    inspect_audio,
    output_template,
    safe_filename,
    sha256_file,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a/b:c", "a_b_c"),
        ('<>:"/\\|?*', "_________"),
        ("CON", "_CON"),
        ("  title.  ", "title"),
        ("", "Untitled"),
    ],
)
def test_safe_filename(value, expected):
    assert safe_filename(value) == expected


def test_safe_filename_truncates():
    assert safe_filename("x" * 200, max_length=10) == "x" * 10
    assert output_template() == "%(title|Untitled)s [%(id)s].%(ext)s"


def test_sha256_file(tmp_path):
    path = tmp_path / "a"
    path.write_bytes(b"hello")
    assert (
        sha256_file(path)
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_inspect_audio_valid_and_invalid(tmp_path, monkeypatch):
    path = tmp_path / "a.m4a"
    path.write_bytes(b"x")

    class Info:
        length = 10
        sample_rate = 44100
        channels = 2
        bitrate = 96000

    class Media:
        info = Info()

    monkeypatch.setattr("music_app.storage.MutagenFile", lambda _path: Media())
    info = inspect_audio(path)
    assert info.duration == 10
    assert info.codec == "Media"

    monkeypatch.setattr("music_app.storage.MutagenFile", lambda _path: None)
    with pytest.raises(InvalidMediaError):
        inspect_audio(path)
    empty = tmp_path / "empty"
    empty.touch()
    with pytest.raises(InvalidMediaError):
        inspect_audio(empty)


def test_inspect_audio_error_and_invalid_duration(tmp_path, monkeypatch):
    path = tmp_path / "a.m4a"
    path.write_bytes(b"x")

    def broken(_path):
        raise RuntimeError("broken")

    monkeypatch.setattr("music_app.storage.MutagenFile", broken)
    with pytest.raises(InvalidMediaError):
        inspect_audio(path)

    class EmptyInfo:
        length = 0

    class EmptyMedia:
        info = EmptyInfo()

    monkeypatch.setattr("music_app.storage.MutagenFile", lambda _path: EmptyMedia())
    with pytest.raises(InvalidMediaError):
        inspect_audio(path)


def test_repository_crud(tmp_path):
    repository = DownloadRepository(tmp_path / "index.sqlite3")
    record = DownloadRecord(
        track_id="1",
        title="成都",
        artist="赵雷",
        path="/tmp/成都.m4a",
        sha256="abc",
        size=123,
        source_url="https://soundcloud.com/a",
        downloaded_at=datetime.now(UTC).isoformat(),
        audio_format="m4a",
        bitrate=96000,
    )
    repository.upsert(record)
    assert repository.find("1") == record
    assert repository.all() == [record]
    updated = replace(record, title="成都（更新）")
    repository.upsert(updated)
    assert repository.find("1") == updated
    assert repository.find("missing") is None


def test_staging_finalize(tmp_path, monkeypatch, track):
    path = tmp_path / "成都 [722635666].m4a"
    path.write_bytes(b"audio")
    repository = DownloadRepository(tmp_path / "index.sqlite3")
    staging = DownloadStaging(tmp_path, repository)
    monkeypatch.setattr(
        "music_app.storage.inspect_audio",
        lambda _path: AudioInfo(10, 44100, 2, 96000, "MP4"),
    )
    monkeypatch.setattr("music_app.storage.sha256_file", lambda _path: "hash")
    record = staging.finalize(path, track)
    assert record.sha256 == "hash"
    assert record.size == 5
    assert repository.find(track.track_id) == record

    wrong = tmp_path / "wrong.mp3"
    wrong.write_bytes(b"x")
    with pytest.raises(InvalidMediaError):
        staging.finalize(wrong, track)
