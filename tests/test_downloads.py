from __future__ import annotations

from unittest.mock import Mock

import pytest

from music_app.downloads import (
    DownloadService,
    DownloadSettings,
    build_ydl_options,
    find_output_path,
    require_download_rights,
)
from music_app.errors import (
    AppError,
    DrmProtectedError,
    RightsNotAcknowledged,
    TaskCancelled,
)
from music_app.security import CancellationToken


def test_require_download_rights(track):
    with pytest.raises(RightsNotAcknowledged):
        require_download_rights(False, track)
    require_download_rights(True, track)


def test_require_download_rights_rejects_not_downloadable(track):
    import dataclasses

    denied = dataclasses.replace(track, downloadable=False)
    with pytest.raises(AppError) as caught:
        require_download_rights(True, denied)
    assert caught.value.code == "not_downloadable"
    drm = dataclasses.replace(track, is_drm=True)
    with pytest.raises(DrmProtectedError):
        require_download_rights(True, drm)


def test_build_ydl_options(tmp_path, monkeypatch):
    monkeypatch.setattr("music_app.downloads.find_ffmpeg", lambda: "/ffmpeg")
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("cookie", encoding="utf-8")
    settings = DownloadSettings(
        audio_format="mp3",
        force=True,
        proxy="http://proxy",
        cookie_file=cookie,
    )
    options = build_ydl_options(settings, tmp_path, "%(id)s.%(ext)s")
    assert options["proxy"] == "http://proxy"
    assert options["cookiefile"] == str(cookie)
    assert options["overwrites"] is True
    assert options["ffmpeg_location"] == "/ffmpeg"
    assert options["postprocessors"][0]["preferredcodec"] == "mp3"


def test_build_ydl_options_m4a_fixup(tmp_path, monkeypatch):
    monkeypatch.setattr("music_app.downloads.find_ffmpeg", lambda: "/ffmpeg")
    options = build_ydl_options(
        DownloadSettings(audio_format="m4a"),
        tmp_path,
        "%(id)s.%(ext)s",
    )
    assert [item["key"] for item in options["postprocessors"]] == [
        "FFmpegExtractAudio",
        "FFmpegFixupM4a",
    ]


def test_find_output_path_variants(tmp_path):
    file_path = tmp_path / "a.m4a"
    file_path.write_bytes(b"x")
    ydl = Mock()
    ydl.prepare_filename.return_value = str(file_path)
    assert find_output_path(None, ydl) is None
    requested = {"requested_downloads": [{"filepath": str(file_path)}]}
    assert find_output_path(requested, ydl) == file_path
    assert find_output_path({"filename": str(file_path)}, ydl) == file_path
    assert find_output_path({"id": "1"}, ydl) == file_path
    ydl.prepare_filename.side_effect = KeyError()
    assert find_output_path({"id": "1"}, ydl) is None


def test_download_service_success(tmp_path, track):
    output = tmp_path / "成都 [722635666].m4a"
    record = object()
    staging = Mock()
    staging.output_dir = tmp_path
    staging.outtmpl = "%(id)s.%(ext)s"
    staging.finalize.return_value = record
    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download=True):
            output.write_bytes(b"audio")
            captured["url"] = url
            return {"requested_downloads": [{"filepath": str(output)}]}

    progress = []
    service = DownloadService(
        staging,
        ydl_factory=lambda options: FakeYDL(options),
    )
    result = service.download(
        track,
        CancellationToken(),
        progress.append,
        rights_acknowledged=True,
    )
    assert result is record
    assert captured["url"] == track.webpage_url
    assert progress[-1].percent == 100


def test_download_progress_hooks(tmp_path, track):
    output = tmp_path / "a.m4a"
    output.write_bytes(b"x")
    staging = Mock()
    staging.output_dir = tmp_path
    staging.outtmpl = "%(id)s.%(ext)s"
    staging.finalize.return_value = object()
    values = []

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, *_args, **_kwargs):
            hook = self.options["progress_hooks"][0]
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            hook({"status": "downloading", "fragment_index": 2, "fragment_count": 4})
            hook({"status": "finished"})
            return {"filename": str(output)}

    service = DownloadService(staging, ydl_factory=lambda o: FakeYDL(o))
    service.download(
        track,
        CancellationToken(),
        values.append,
        rights_acknowledged=True,
    )
    assert values[0].percent == 50
    assert values[1].percent == 50
    assert values[1].message == "正在下载 50%"


def test_download_service_cancellation_and_errors(tmp_path, track):
    staging = Mock()
    staging.output_dir = tmp_path
    staging.outtmpl = "%(id)s.%(ext)s"
    token = CancellationToken()
    token.cancel()
    with pytest.raises(TaskCancelled):
        DownloadService(staging).download(
            track,
            token,
            lambda _progress: None,
            rights_acknowledged=True,
        )

    class MissingYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, *_args, **_kwargs):
            return {}

        def prepare_filename(self, _info):
            return str(tmp_path / "missing.m4a")

    with pytest.raises(AppError) as caught:
        DownloadService(
            staging,
            ydl_factory=lambda options: MissingYDL(options),
        ).download(
            track,
            CancellationToken(),
            lambda _progress: None,
            rights_acknowledged=True,
        )
    assert caught.value.code == "missing_output"


def test_download_rejects_unacknowledged_rights(tmp_path, track):
    staging = Mock()
    staging.output_dir = tmp_path
    staging.outtmpl = "%(id)s.%(ext)s"
    with pytest.raises(RightsNotAcknowledged):
        DownloadService(staging).download(
            track,
            CancellationToken(),
            lambda _progress: None,
            rights_acknowledged=False,
        )


def test_download_cancelled_from_progress_hook(tmp_path, track):
    staging = Mock()
    staging.output_dir = tmp_path
    staging.outtmpl = "%(id)s.%(ext)s"
    token = CancellationToken()

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, *_args, **_kwargs):
            token.cancel()
            self.options["progress_hooks"][0](
                {"status": "downloading", "downloaded_bytes": 1}
            )

    with pytest.raises(TaskCancelled):
        DownloadService(
            staging,
            ydl_factory=lambda options: FakeYDL(options),
        ).download(
            track,
            token,
            lambda _progress: None,
            rights_acknowledged=True,
        )


def test_build_options_without_ffmpeg_original_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("music_app.downloads.find_ffmpeg", lambda: None)
    settings = DownloadSettings(audio_format="original", embed_metadata=True)
    options = build_ydl_options(settings, tmp_path, "%(id)s.%(ext)s")
    assert "ffmpeg_location" not in options
    assert options["postprocessors"] == [
        {"key": "FFmpegMetadata", "add_metadata": True}
    ]


def test_find_output_path_ignores_missing_requested_file(tmp_path):
    ydl = Mock()
    ydl.prepare_filename.return_value = str(tmp_path / "missing.m4a")
    path = find_output_path(
        {"requested_downloads": [{"filename": str(tmp_path / "missing.m4a")}]},
        ydl,
    )
    assert path is None
