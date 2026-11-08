from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import AppError, DrmProtectedError, RightsNotAcknowledged, TaskCancelled
from .ffmpeg_tools import find_ffmpeg
from .models import TrackCandidate
from .security import CancellationToken
from .storage import DownloadRecord, DownloadStaging


class YoutubeDLContext(Protocol):
    def __enter__(self) -> YoutubeDLContext: ...
    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None: ...
    def extract_info(self, url: str, download: bool = True) -> dict: ...
    def prepare_filename(self, info: dict) -> str: ...


YoutubeDLFactory = Callable[[dict[str, Any]], YoutubeDLContext]


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    percent: float
    message: str
    downloaded_bytes: int = 0
    total_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DownloadSettings:
    audio_format: str = "m4a"
    audio_quality: str = "320"
    sample_rate: int = 44100
    channels: int = 2
    no_playlist: bool = True
    embed_metadata: bool = False
    retries: int = 10
    timeout: int = 30
    force: bool = False
    proxy: str | None = None
    cookie_file: Path | None = None


def default_ydl_factory(options: dict[str, Any]) -> YoutubeDLContext:
    import yt_dlp

    return yt_dlp.YoutubeDL(options)


def require_download_rights(
    acknowledged: bool,
    track: TrackCandidate,
) -> None:
    if not acknowledged:
        raise RightsNotAcknowledged()
    if track.is_drm:
        raise DrmProtectedError("搜索结果标记为 DRM 内容")
    if track.downloadable is False:
        raise AppError(
            "not_downloadable",
            "该曲目未开放下载，只能通过官方渠道播放。",
        )


def build_ydl_options(
    settings: DownloadSettings,
    output_dir: Path,
    outtmpl: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    options: dict[str, Any] = {
        "paths": {"home": str(output_dir.resolve())},
        "outtmpl": {"default": outtmpl},
        "format": (
            "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"
        ),
        "noplaylist": settings.no_playlist,
        "continuedl": True,
        "retries": settings.retries,
        "fragment_retries": settings.retries,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": settings.timeout,
        "windowsfilenames": True,
        "trim_file_name": 180,
        "overwrites": settings.force,
        "consoletitle": False,
    }
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    if settings.cookie_file:
        options["cookiefile"] = str(settings.cookie_file)
    if settings.proxy:
        options["proxy"] = settings.proxy

    postprocessors: list[dict[str, Any]] = []
    if settings.audio_format == "original":
        if ffmpeg:
            postprocessors.append({"key": "FFmpegFixupM4a"})
    else:
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.audio_format,
                "preferredquality": settings.audio_quality,
                "nopostoverwrites": False,
            }
        )
        if settings.audio_format == "m4a" and ffmpeg:
            postprocessors.append({"key": "FFmpegFixupM4a"})
        options["postprocessor_args"] = {
            "extractaudio": [
                "-ar",
                str(settings.sample_rate),
                "-ac",
                str(settings.channels),
            ]
        }
    if settings.embed_metadata:
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
    if postprocessors:
        options["postprocessors"] = postprocessors
    return options


def find_output_path(
    info: dict | None,
    ydl: YoutubeDLContext,
) -> Path | None:
    if not info:
        return None
    for downloaded in info.get("requested_downloads") or []:
        for key in ("filepath", "_filename", "filename"):
            if downloaded.get(key):
                path = Path(downloaded[key])
                if path.exists():
                    return path
    for key in ("filepath", "_filename", "filename"):
        if info.get(key):
            path = Path(info[key])
            if path.exists():
                return path
    try:
        prepared = Path(ydl.prepare_filename(info))
    except (KeyError, TypeError, ValueError):
        return None
    return prepared if prepared.exists() else None


class DownloadService:
    def __init__(
        self,
        staging: DownloadStaging,
        *,
        settings: DownloadSettings | None = None,
        ydl_factory: YoutubeDLFactory = default_ydl_factory,
        logger: logging.Logger | None = None,
    ) -> None:
        self.staging = staging
        self.settings = settings or DownloadSettings()
        self.ydl_factory = ydl_factory
        self.logger = logger or logging.getLogger("music_app")

    def download(
        self,
        track: TrackCandidate,
        token: CancellationToken,
        on_progress: Callable[[DownloadProgress], None],
        *,
        rights_acknowledged: bool,
    ) -> DownloadRecord:
        require_download_rights(rights_acknowledged, track)
        token.raise_if_cancelled()

        def progress_hook(data: dict) -> None:
            token.raise_if_cancelled()
            status = data.get("status")
            if status != "downloading":
                return
            downloaded = int(data.get("downloaded_bytes") or 0)
            total_value = (
                data.get("total_bytes") or data.get("total_bytes_estimate")
            )
            total = int(total_value) if total_value else None
            percent = (
                min(100.0, downloaded * 100.0 / total)
                if total
                else 0.0
            )
            if not total:
                fragment_index = data.get("fragment_index")
                fragment_count = data.get("fragment_count")
                if fragment_index and fragment_count:
                    percent = min(
                        100.0,
                        float(fragment_index) * 100.0 / fragment_count,
                    )
            on_progress(
                DownloadProgress(
                    percent=percent,
                    message=f"正在下载 {percent:.0f}%",
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                )
            )

        options = build_ydl_options(
            self.settings,
            self.staging.output_dir,
            self.staging.outtmpl,
        )
        options["progress_hooks"] = [progress_hook]
        options["quiet"] = True
        options["noprogress"] = True
        options["no_warnings"] = False

        try:
            with self.ydl_factory(options) as ydl:
                info = ydl.extract_info(track.webpage_url, download=True)
                path = find_output_path(info, ydl)
            if path is None:
                raise AppError(
                    "missing_output",
                    "下载结束，但没有找到生成的音频文件。",
                )
            token.raise_if_cancelled()
            record_format = self.settings.audio_format
            if record_format == "original":
                record_format = path.suffix.lower().lstrip(".") or "original"
            record = self.staging.finalize(
                path,
                track,
                audio_format=record_format,
                source_format=(info or {}).get("ext"),
            )
            on_progress(
                DownloadProgress(percent=100.0, message="下载完成")
            )
            return record
        except TaskCancelled:
            raise
        except Exception:
            self.logger.exception(
                "download failed",
                extra={"event": "download_failed", "track_id": track.track_id},
            )
            raise
