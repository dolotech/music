from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .models import TrackCandidate, track_from_entry
from .security import CancellationToken, normalize_soundcloud_url


class SearchBackend(Protocol):
    def search(self, query: str, limit: int) -> list[dict]: ...


class YtDlpSearchBackend:
    def search(self, query: str, limit: int) -> list[dict]:
        import yt_dlp

        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "playlistend": limit,
            "socket_timeout": 30,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"scsearch{limit}:{query}",
                download=False,
            )
        return list((info or {}).get("entries") or [])


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    value: list[TrackCandidate]


class TTLCache:
    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries: dict[tuple[str, int], _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[str, int]) -> list[TrackCandidate] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self.clock():
                self._entries.pop(key, None)
                return None
            return list(entry.value)

    def set(self, key: tuple[str, int], value: list[TrackCandidate]) -> None:
        with self._lock:
            self._entries[key] = _CacheEntry(
                expires_at=self.clock() + self.ttl_seconds,
                value=list(value),
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class MinIntervalLimiter:
    def __init__(
        self,
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self._last_request = float("-inf")
        self._lock = threading.Lock()

    def wait(self, token: CancellationToken | None = None) -> None:
        with self._lock:
            now = self.clock()
            delay = self._last_request + self.interval_seconds - now
            if delay > 0:
                if token is not None:
                    if token.wait(delay):
                        token.raise_if_cancelled()
                else:
                    self.sleeper(delay)
            self._last_request = self.clock()


class SearchService:
    def __init__(
        self,
        backend: SearchBackend,
        *,
        cache_ttl_seconds: float = 60.0,
        min_interval_seconds: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self.cache = TTLCache(cache_ttl_seconds, clock)
        self.limiter = MinIntervalLimiter(
            min_interval_seconds,
            clock,
            sleeper,
        )

    def search(
        self,
        query: str,
        limit: int = 30,
        token: CancellationToken | None = None,
    ) -> list[TrackCandidate]:
        normalized_query = " ".join(query.casefold().split())
        if not normalized_query:
            return []
        if token is not None:
            token.raise_if_cancelled()
        key = (normalized_query, limit)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        self.limiter.wait(token)
        raw_entries = self.backend.search(query.strip(), limit)
        candidates: list[TrackCandidate] = []
        seen: set[str] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            url = entry.get("webpage_url")
            if not url:
                continue
            try:
                normalize_soundcloud_url(str(url))
            except Exception:
                continue
            candidate = track_from_entry(entry, query)
            if candidate is None or candidate.track_id in seen:
                continue
            seen.add(candidate.track_id)
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (item.score, item.likes),
            reverse=True,
        )
        self.cache.set(key, candidates)
        return list(candidates)
