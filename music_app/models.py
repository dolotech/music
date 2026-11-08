from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True, slots=True)
class TrackCandidate:
    track_id: str
    title: str
    artist: str
    duration: float | None
    webpage_url: str
    score: float
    likes: int = 0
    license: str | None = None
    downloadable: bool | None = None
    is_drm: bool | None = None

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration)

    @property
    def score_text(self) -> str:
        return f"{round(self.score * 100)}%"

    @property
    def likes_text(self) -> str:
        return format_count(self.likes)


@dataclass(frozen=True, slots=True)
class AudioInfo:
    duration: float
    sample_rate: int | None
    channels: int | None
    bitrate: int | None
    codec: str | None


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    track_id: str
    title: str
    artist: str
    path: str
    sha256: str
    size: int
    source_url: str
    downloaded_at: str
    audio_format: str
    source_format: str | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def fuzzy_score(query: str, title: str, artist: str) -> float:
    query_norm = normalize_text(query)
    title_norm = normalize_text(title)
    artist_norm = normalize_text(artist)
    if not query_norm or not title_norm:
        return 0.0

    title_ratio = SequenceMatcher(None, query_norm, title_norm).ratio()
    combined_ratio = SequenceMatcher(
        None, query_norm, f"{title_norm}{artist_norm}"
    ).ratio()
    contains_score = 1.0 if query_norm in title_norm else 0.0
    artist_match = (
        1.0
        if artist_norm
        and (artist_norm in query_norm or query_norm in artist_norm)
        else 0.0
    )
    tokens = [
        token for token in query.casefold().split() if normalize_text(token)
    ]
    if tokens:
        hits = sum(
            normalize_text(token) in f"{title_norm}{artist_norm}"
            for token in tokens
        )
        token_score = hits / len(tokens)
    else:
        token_score = contains_score

    score = (
        0.40 * title_ratio
        + 0.25 * combined_ratio
        + 0.15 * contains_score
        + 0.10 * artist_match
        + 0.10 * token_score
    )
    return max(0.0, min(1.0, score))


def format_duration(seconds: object) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "--:--"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_count(value: int) -> str:
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return f"{value:,}"


def track_from_entry(entry: dict, query: str) -> TrackCandidate | None:
    webpage_url = entry.get("webpage_url")
    if not webpage_url:
        return None
    title = str(entry.get("title") or "未命名歌曲")
    artist = str(
        entry.get("uploader")
        or entry.get("artist")
        or entry.get("channel")
        or "未知作者"
    )
    duration = entry.get("duration")
    if not isinstance(duration, (int, float)):
        duration = None
    try:
        likes = max(0, int(entry.get("like_count") or 0))
    except (TypeError, ValueError):
        likes = 0
    downloadable = entry.get("downloadable")
    if not isinstance(downloadable, bool):
        downloadable = None
    is_drm = entry.get("is_drm")
    if not isinstance(is_drm, bool):
        is_drm = None
    return TrackCandidate(
        track_id=str(entry.get("id") or webpage_url),
        title=title,
        artist=artist,
        duration=duration,
        webpage_url=str(webpage_url),
        score=fuzzy_score(query, title, artist),
        likes=likes,
        license=entry.get("license"),
        downloadable=downloadable,
        is_drm=is_drm,
    )
