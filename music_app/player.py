from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import AppError


class AudioBackend(Protocol):
    def load(self, path: Path) -> None: ...
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

    def load(self, path: Path) -> None:
        self.pygame.mixer.music.load(str(path))

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


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    loaded: bool
    playing: bool
    position: float
    duration: float
    finished: bool


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
        self.path: Path | None = None
        self.duration = 0.0
        self.position = 0.0
        self._start_position = 0.0
        self._started_at = 0.0

    def load(self, path: Path, duration: float, autoplay: bool = True) -> None:
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
        self.path = None
        self.position = 0.0

    def _sync(self) -> None:
        if self.playing:
            self.position = min(
                self.duration,
                self._start_position + self.clock() - self._started_at,
            )

    def tick(self) -> PlayerSnapshot:
        finished = False
        if self.loaded and self.playing:
            if not self.backend.get_busy():
                self.playing = False
                self.position = self.duration
                finished = True
            else:
                self._sync()
        return PlayerSnapshot(
            loaded=self.loaded,
            playing=self.playing,
            position=self.position,
            duration=self.duration,
            finished=finished,
        )
