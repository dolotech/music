from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import NetworkError
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
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            options["impersonate"] = ImpersonateTarget.from_str("chrome")
        except (ImportError, ValueError):
            pass
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"scsearch{limit}:{query}",
                download=False,
            )
        return list((info or {}).get("entries") or [])


class SoundCloudFastSearchBackend:
    """Use SoundCloud's search endpoint directly and fall back to yt-dlp."""

    API_URL = "https://api-v2.soundcloud.com/search/tracks"

    def __init__(
        self,
        *,
        fallback: SearchBackend | None = None,
        client_id_provider: Callable[[], str | None] | None = None,
        request_json: Callable[[str], dict] | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.fallback = fallback or YtDlpSearchBackend()
        self.client_id_provider = client_id_provider or self._cached_client_id
        self.request_json = request_json or (
            lambda url: self._request_json(url, timeout)
        )
        self._client_id: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _cached_client_id() -> str | None:
        try:
            import yt_dlp

            with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                return ydl.cache.load("soundcloud", "client_id")
        except Exception:
            return None

    @staticmethod
    def _request_json(url: str, timeout: float) -> dict:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            raise NetworkError(str(exc)) from exc

    def _get_client_id(self) -> str | None:
        with self._lock:
            if self._client_id is None:
                self._client_id = self.client_id_provider()
            return self._client_id

    def _clear_client_id(self) -> None:
        with self._lock:
            self._client_id = None

    def search(self, query: str, limit: int) -> list[dict]:
        client_id = self._get_client_id()
        if not client_id:
            return self.fallback.search(query, min(limit, 15))
        url = f"{self.API_URL}?" + urlencode(
            {
                "q": query,
                "client_id": client_id,
                "limit": limit,
                "offset": 0,
                "linked_partitioning": 1,
            }
        )
        try:
            payload = self.request_json(url)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                self._clear_client_id()
                return self.fallback.search(query, min(limit, 15))
            raise NetworkError(str(exc)) from exc
        except NetworkError as direct_error:
            try:
                return self.fallback.search(query, min(limit, 15))
            except Exception as fallback_error:
                raise direct_error from fallback_error
        except Exception:
            self._clear_client_id()
            return self.fallback.search(query, min(limit, 15))
        entries = [
            self._normalize(item)
            for item in payload.get("collection") or []
            if isinstance(item, dict) and item.get("kind") == "track"
        ]
        return [entry for entry in entries if entry is not None]

    @staticmethod
    def _normalize(item: dict) -> dict | None:
        if item.get("policy") in {"BLOCK", "SNIP"}:
            return None
        url = item.get("permalink_url")
        if not url:
            return None
        duration_ms = item.get("duration")
        return {
            "id": item.get("id"),
            "title": item.get("title"),
            "uploader": (item.get("user") or {}).get("username"),
            "duration": (
                duration_ms / 1000
                if isinstance(duration_ms, (int, float))
                else None
            ),
            "like_count": item.get("likes_count") or 0,
            "webpage_url": url,
            "downloadable": item.get("downloadable"),
            "license": item.get("license"),
            "is_drm": item.get("is_drm"),
        }


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
        cache_ttl_seconds: float = 300.0,
        min_interval_seconds: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self.backend = backend
        self.cache = TTLCache(cache_ttl_seconds, clock)
        self.limiter = MinIntervalLimiter(
            min_interval_seconds,
            clock,
            sleeper,
        )
        self.logger = logger

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
            if self.logger:
                self.logger.info(
                    "search cache hit",
                    extra={"event": "search_cache_hit"},
                )
            return cached

        started = time.monotonic()
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
        if self.logger:
            self.logger.info(
                "search completed in %.3fs with %d results",
                time.monotonic() - started,
                len(candidates),
                extra={"event": "search_completed"},
            )
        return list(candidates)
