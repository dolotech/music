from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import AppError
from .ffmpeg_tools import find_ffmpeg
from .models import TrackCandidate
from .security import CancellationToken


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    url: str
    headers: dict[str, str]
    duration: float | None


class MediaResolver(Protocol):
    def resolve(
        self,
        track: TrackCandidate,
        token: CancellationToken,
    ) -> ResolvedMedia: ...


class YtDlpMediaResolver:
    def resolve(
        self,
        track: TrackCandidate,
        token: CancellationToken,
    ) -> ResolvedMedia:
        import yt_dlp

        token.raise_if_cancelled()
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 30,
            "retries": 3,
            "extractor_retries": 3,
        }
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            options["impersonate"] = ImpersonateTarget.from_str("chrome")
        except (ImportError, ValueError):
            pass
        last_error = None
        info = None
        for attempt in range(3):
            token.raise_if_cancelled()
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(track.webpage_url, download=False)
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    token.wait(0.5 * (attempt + 1))
        if info is None:
            raise last_error or AppError(
                "media_resolve_failed",
                "无法解析音频流。",
            )
        token.raise_if_cancelled()
        formats = [
            item
            for item in (info or {}).get("formats") or []
            if item.get("url")
            and item.get("acodec") not in (None, "none")
            and item.get("vcodec") in (None, "none")
        ]
        if not formats and (info or {}).get("url"):
            formats = [info]
        if not formats:
            raise AppError(
                "no_playable_stream",
                "该歌曲没有可用的音频播放流。",
            )
        media = max(
            formats,
            key=lambda item: item.get("abr") or item.get("tbr") or 0,
        )
        headers: dict[str, str] = {}
        for source in (info or {}, media):
            for key, value in (source.get("http_headers") or {}).items():
                if isinstance(key, str) and isinstance(value, str):
                    headers[key] = value
        duration = (info or {}).get("duration")
        return ResolvedMedia(
            url=str(media["url"]),
            headers=headers,
            duration=float(duration) if isinstance(duration, (int, float)) else None,
        )


class ProcessRunner:
    def run(
        self,
        command: list[str],
        token: CancellationToken,
        timeout: float,
    ) -> tuple[int, str, str]:
        if os.name == "nt":
            process: subprocess.Popen[str] = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0x00000200,
                ),
            )
        else:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        started = time.monotonic()
        while process.poll() is None:
            if token.is_cancelled():
                self._stop_process_tree(process)
                token.raise_if_cancelled()
            if time.monotonic() - started > timeout:
                self._stop_process_tree(process, force=True)
                stdout, stderr = process.communicate()
                raise AppError(
                    "preview_timeout",
                    "试听准备超时。",
                    detail=stderr.strip(),
                    retryable=True,
                )
            time.sleep(0.05)
        stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr

    @staticmethod
    def _stop_process_tree(
        process: subprocess.Popen[str],
        *,
        force: bool = False,
    ) -> None:
        if force:
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (AttributeError, OSError):
                    process.kill()
            else:
                process.kill()
            process.wait()
            return
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (AttributeError, OSError):
                process.terminate()
        else:
            process.terminate()
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                return
            except (AttributeError, OSError):
                pass
        process.kill()
        process.wait()


class PreviewCache:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max(1024, max_bytes)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._paths: OrderedDict[str, Path] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, track_id: str) -> Path | None:
        with self._lock:
            path = self._paths.get(track_id)
            if path and path.is_file():
                self._paths.move_to_end(track_id)
                return path
            self._paths.pop(track_id, None)
            return None

    def add(self, track_id: str, path: Path, preserve: set[str] | None = None) -> None:
        with self._lock:
            self._paths[track_id] = path
            self._paths.move_to_end(track_id)
            preserve = preserve or set()
            total = sum(
                item.stat().st_size
                for item in self._paths.values()
                if item.exists()
            )
            while total > self.max_bytes and self._paths:
                old_id, old_path = next(iter(self._paths.items()))
                if old_id in preserve:
                    self._paths.move_to_end(old_id)
                    if all(key in preserve for key in self._paths):
                        break
                    continue
                self._paths.pop(old_id, None)
                old_path.unlink(missing_ok=True)
                total = sum(
                    item.stat().st_size
                    for item in self._paths.values()
                    if item.exists()
                )


class PreviewService:
    def __init__(
        self,
        cache: PreviewCache,
        *,
        resolver: MediaResolver | None = None,
        runner: ProcessRunner | None = None,
        ffmpeg_finder: Callable[[], str | None] = find_ffmpeg,
    ) -> None:
        self.cache = cache
        self.resolver = resolver or YtDlpMediaResolver()
        self.runner = runner or ProcessRunner()
        self.ffmpeg_finder = ffmpeg_finder

    def resolve_media(
        self,
        track: TrackCandidate,
        token: CancellationToken,
    ) -> ResolvedMedia:
        return self.resolver.resolve(track, token)

    def prepare(
        self,
        track: TrackCandidate,
        token: CancellationToken,
        *,
        preserve: set[str] | None = None,
    ) -> Path:
        cached = self.cache.get(track.track_id)
        if cached is not None:
            return cached
        ffmpeg = self.ffmpeg_finder()
        if not ffmpeg:
            raise AppError(
                "ffmpeg_missing",
                "未找到 FFmpeg，无法准备试听。",
            )
        token.raise_if_cancelled()
        media = self.resolver.resolve(track, token)
        temporary = self.cache.directory / f"{track.track_id}.part.mp3"
        output = self.cache.directory / f"{track.track_id}.mp3"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if media.headers:
            header_block = "".join(
                f"{key}: {value}\r\n"
                for key, value in media.headers.items()
            )
            command.extend(["-headers", header_block])
        command.extend(
            [
                "-i",
                media.url,
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-f",
                "mp3",
                str(temporary),
            ]
        )
        expected_duration = track.duration or media.duration or 600
        try:
            returncode, _, stderr = self.runner.run(
                command,
                token,
                timeout=max(180, round(expected_duration) + 180),
            )
            token.raise_if_cancelled()
            if returncode != 0 or not temporary.is_file():
                temporary.unlink(missing_ok=True)
                raise AppError(
                    "preview_failed",
                    "试听准备失败。",
                    detail=(
                        stderr.strip().splitlines()[-1]
                        if stderr.strip()
                        else None
                    ),
                    retryable=True,
                )
            os.replace(temporary, output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        self.cache.add(track.track_id, output, preserve)
        return output
