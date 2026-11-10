from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import AppError
from .ffmpeg_tools import find_ffmpeg
from .media import ResolvedMedia


class AudioBackend(Protocol):
    def load(self, source: Path | ResolvedMedia) -> None: ...
    def play(self, start: float) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def unload(self) -> None: ...
    def get_busy(self) -> bool: ...


class PygameBackend:
    def __init__(self) -> None:
        import pygame

        pygame.mixer.init(frequency=44100, size=-16, channels=2)
        self.pygame = pygame

    def load(self, source: Path | ResolvedMedia) -> None:
        if not isinstance(source, Path):
            raise AppError(
                "unsupported_stream",
                "Pygame 后端不支持流媒体源。",
            )
        self.pygame.mixer.music.load(str(source))

    def play(self, start: float) -> None:
        self.pygame.mixer.music.play(start=start)

    def pause(self) -> None:
        self.pygame.mixer.music.pause()

    def stop(self) -> None:
        self.pygame.mixer.music.stop()

    def unload(self) -> None:
        self.pygame.mixer.music.unload()

    def get_busy(self) -> bool:
        return self.pygame.mixer.music.get_busy()

    def close(self) -> None:
        self.pygame.mixer.quit()


class StreamingPcmBackend:
    """Stream FFmpeg PCM directly to the default audio output device."""

    def __init__(
        self,
        *,
        ffmpeg_finder: Callable[[], str | None] = find_ffmpeg,
        output_factory: Callable[[], Any] | None = None,
    ) -> None:
        import sounddevice as sounddevice

        self.sounddevice = sounddevice
        self.ffmpeg_finder = ffmpeg_finder
        self.output_factory = output_factory or self._default_output
        self.source: ResolvedMedia | None = None
        self.last_error: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._sink = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._abort_event = threading.Event()
        self._ready_event = threading.Event()
        self._playing = False
        self._finished = False
        self._buffering = False
        self._lock = threading.RLock()

    def _default_output(self) -> Any:
        return self.sounddevice.RawOutputStream(
            samplerate=44100,
            channels=2,
            dtype="int16",
            latency="high",
        )

    def load(self, source: Path | ResolvedMedia) -> None:
        if not isinstance(source, ResolvedMedia):
            raise AppError(
                "unsupported_stream",
                "流式后端需要远程媒体源。",
            )
        self.stop()
        self.source = source

    def play(self, start: float) -> None:
        with self._lock:
            if self.source is None:
                raise AppError("player_not_loaded", "没有可播放的媒体源。")
            ffmpeg = self.ffmpeg_finder()
            if not ffmpeg:
                raise AppError("ffmpeg_missing", "未找到 FFmpeg。")
            self._stop_worker()
            self._stop_event = threading.Event()
            self._abort_event = threading.Event()
            self._finished = False
            self.last_error = None
            self._playing = True
            self._buffering = True
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._stream_worker,
                args=(
                    ffmpeg,
                    self.source,
                    max(0.0, start),
                    self._stop_event,
                    self._abort_event,
                ),
                name="pcm-stream",
                daemon=True,
            )
            self._thread.start()
            threading.Thread(
                target=self._startup_watchdog,
                args=(self._ready_event, self._thread),
                name="pcm-startup-watchdog",
                daemon=True,
            ).start()

    def _startup_watchdog(
        self,
        ready_event: threading.Event,
        worker: threading.Thread,
    ) -> None:
        if ready_event.wait(12):
            return
        if worker.is_alive():
            self.last_error = "音频流首包超时"
            self._playing = False
            self._buffering = False
            self._stop_worker()

    def _stream_worker(
        self,
        ffmpeg: str,
        source: ResolvedMedia,
        start: float,
        stop_event: threading.Event,
        abort_event: threading.Event,
    ) -> None:
        process = None
        sink = None
        received_chunk = False
        stderr_lines: deque[str] = deque(maxlen=20)
        try:
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-rw_timeout",
                "15000000",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-protocol_whitelist",
                "file,http,https,tcp,tls,crypto",
            ]
            if source.headers:
                header_block = "".join(
                    f"{key}: {value}\r\n"
                    for key, value in source.headers.items()
                )
                command.extend(["-headers", header_block])
            if start > 0:
                command.extend(["-ss", f"{start:.3f}"])
            command.extend(
                [
                    "-i",
                    source.url,
                    "-vn",
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "s16le",
                    "pipe:1",
                ]
            )
            if os.name == "nt":
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
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
                    start_new_session=True,
                )
            sink = self.output_factory()
            with self._lock:
                self._process = process
                self._sink = sink
            stdout = process.stdout
            if stdout is None:
                raise AppError("stream_start_failed", "无法读取音频流。")

            def read_stderr() -> None:
                stderr_pipe = getattr(process, "stderr", None)
                if stderr_pipe is None:
                    return
                for line in stderr_pipe:
                    stderr_lines.append(line.rstrip())

            stderr_thread = threading.Thread(
                target=read_stderr,
                name="pcm-stderr",
                daemon=True,
            )
            stderr_thread.start()
            while not stop_event.is_set() and not abort_event.is_set():
                chunk = stdout.read(32 * 1024)
                if not chunk:
                    break
                received_chunk = True
                if self._buffering:
                    with self._lock:
                        self._buffering = False
                    self._ready_event.set()
                try:
                    sink.write(chunk)
                except Exception:
                    if stop_event.is_set() or abort_event.is_set():
                        break
                    raise
        except Exception as exc:
            if not (stop_event.is_set() or abort_event.is_set()):
                self.last_error = str(exc)
        finally:
            stopped = stop_event.is_set()
            if process is not None:
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2)
                if "stderr_thread" in locals():
                    stderr_thread.join(timeout=1)
            with self._lock:
                self._playing = False
                self._finished = not stopped
                self._buffering = False
                self._ready_event.set()
                if not received_chunk and not stopped and self.last_error is None:
                    detail = "\n".join(stderr_lines).strip()
                    returncode = process.returncode if process is not None else None
                    self.last_error = (
                        "音频流未返回数据"
                        + (f"（FFmpeg 退出码 {returncode}）" if returncode else "")
                        + (f"\n{detail}" if detail else "")
                    )
                self._process = None
                self._sink = None
            if process is not None:
                self._terminate_process(process)
            if sink is not None:
                with suppress(Exception):
                    if abort_event.is_set():
                        pass
                    elif stopped:
                        sink.abort()
                    elif hasattr(sink, "stop"):
                        sink.stop()
                    else:
                        sink.abort()

    def pause(self) -> None:
        with self._lock:
            self._playing = False
            self._stop_worker()

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._finished = False
            self._stop_worker()

    def unload(self) -> None:
        self.stop()
        self.source = None

    def get_busy(self) -> bool:
        thread = self._thread
        return bool(
            self._playing
            and not self._finished
            and thread is not None
            and thread.is_alive()
        )

    def is_buffering(self) -> bool:
        return self._buffering

    def close(self) -> None:
        self.unload()

    def _stop_worker(self) -> None:
        self._stop_event.set()
        self._abort_event.set()
        process = self._process
        sink = self._sink
        thread = self._thread
        if sink is not None:
            with suppress(Exception):
                sink.abort()
        if process is not None:
            self._terminate_process(process)
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=2)
        self._thread = None
        self._process = None
        self._sink = None

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name != "nt":
            with suppress(AttributeError, OSError):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                try:
                    process.wait(timeout=1)
                    return
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                process.kill()
            process.wait()


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    loaded: bool
    playing: bool
    position: float
    duration: float
    finished: bool
    buffering: bool = False


class AudioPlayer:
    def __init__(
        self,
        backend: AudioBackend,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.clock = clock
        self.loaded = False
        self.playing = False
        self.buffering = False
        self.path: Path | ResolvedMedia | None = None
        self.duration = 0.0
        self.position = 0.0
        self._start_position = 0.0
        self._started_at = 0.0

    def load(
        self,
        path: Path | ResolvedMedia,
        duration: float,
        autoplay: bool = True,
    ) -> None:
        try:
            self.release()
            self.backend.load(path)
        except Exception as exc:
            raise AppError(
                "player_load_failed",
                "无法加载试听音频。",
                detail=str(exc),
            ) from exc
        self.loaded = True
        self.path = path
        self.duration = max(1.0, float(duration))
        self.position = 0.0
        if autoplay:
            self.play_from(0.0)

    def play_from(self, position: float) -> None:
        if not self.loaded:
            return
        position = max(0.0, min(self.duration, position))
        try:
            self.backend.play(position)
        except Exception as exc:
            raise AppError(
                "player_play_failed",
                "无法播放试听音频。",
                detail=str(exc),
            ) from exc
        self.position = position
        self._start_position = position
        self.buffering = bool(
            getattr(self.backend, "is_buffering", lambda: False)()
        )
        self._started_at = self.clock()
        self.playing = True

    def toggle(self) -> None:
        if not self.loaded:
            return
        if self.playing:
            self._sync()
            self.backend.pause()
            self.playing = False
        else:
            if self.position >= self.duration - 0.05:
                self.position = 0.0
            self.play_from(self.position)

    def seek(self, position: float) -> None:
        if not self.loaded:
            return
        was_playing = self.playing
        self.backend.stop()
        self.playing = False
        self.position = max(0.0, min(self.duration, position))
        if was_playing:
            self.play_from(self.position)

    def stop(self) -> None:
        if self.loaded:
            self.backend.stop()
        self.playing = False
        self.position = 0.0

    def release(self) -> None:
        with suppress(Exception):
            self.backend.stop()
        with suppress(Exception):
            self.backend.unload()
        self.loaded = False
        self.playing = False
        self.buffering = False
        self.path = None
        self.position = 0.0

    def _sync(self) -> None:
        if self.playing and not self.buffering:
            self.position = min(
                self.duration,
                self._start_position + self.clock() - self._started_at,
            )

    def tick(self) -> PlayerSnapshot:
        finished = False
        if self.loaded and self.playing:
            was_buffering = self.buffering
            self.buffering = bool(
                getattr(self.backend, "is_buffering", lambda: False)()
            )
            if was_buffering and not self.buffering:
                self._started_at = self.clock()
                self._start_position = self.position
            if not self.buffering and not self.backend.get_busy():
                self.playing = False
                self.position = self.duration
                finished = True
            elif not self.buffering:
                self._sync()
        return PlayerSnapshot(
            loaded=self.loaded,
            playing=self.playing,
            position=self.position,
            duration=self.duration,
            finished=finished,
            buffering=self.buffering,
        )
