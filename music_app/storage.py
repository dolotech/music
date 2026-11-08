from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import astuple
from datetime import UTC, datetime
from pathlib import Path

from .errors import InvalidMediaError
from .models import AudioInfo, DownloadRecord, TrackCandidate

try:
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover - exercised by lightweight --help runs
    MutagenFile = None


INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(value: str, max_length: int = 160) -> str:
    name = unicodedata.normalize("NFC", str(value or "Untitled"))
    name = INVALID_FILENAME_RE.sub("_", name).strip(" .")
    if not name:
        name = "Untitled"
    if name.upper() in WINDOWS_RESERVED:
        name = f"_{name}"
    return name[:max_length].rstrip(" .") or "Untitled"


def output_template() -> str:
    return "%(title|Untitled)s [%(id)s].%(ext)s"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_audio(path: Path) -> AudioInfo:
    if not path.is_file() or path.stat().st_size == 0:
        raise InvalidMediaError(f"文件不存在或为空：{path}")
    if MutagenFile is None:
        raise InvalidMediaError("缺少音频校验依赖 mutagen")
    try:
        media = MutagenFile(path)
    except Exception as exc:
        raise InvalidMediaError(f"无法解析音频：{exc}") from exc
    if media is None or media.info is None:
        raise InvalidMediaError("文件不包含可识别的音频流")
    length = float(getattr(media.info, "length", 0.0) or 0.0)
    if length <= 0:
        raise InvalidMediaError("音频时长无效")
    return AudioInfo(
        duration=length,
        sample_rate=getattr(media.info, "sample_rate", None),
        channels=getattr(media.info, "channels", None),
        bitrate=getattr(media.info, "bitrate", None),
        codec=media.__class__.__name__,
    )


class DownloadRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    track_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    audio_format TEXT NOT NULL,
                    source_format TEXT,
                    bitrate INTEGER,
                    sample_rate INTEGER,
                    channels INTEGER
                )
                """
            )

    def upsert(self, record: DownloadRecord) -> None:
        values = astuple(record)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO downloads (
                    track_id, title, artist, path, sha256, size, source_url,
                    downloaded_at, audio_format, source_format, bitrate,
                    sample_rate, channels
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    title=excluded.title,
                    artist=excluded.artist,
                    path=excluded.path,
                    sha256=excluded.sha256,
                    size=excluded.size,
                    source_url=excluded.source_url,
                    downloaded_at=excluded.downloaded_at,
                    audio_format=excluded.audio_format,
                    source_format=excluded.source_format,
                    bitrate=excluded.bitrate,
                    sample_rate=excluded.sample_rate,
                    channels=excluded.channels
                """,
                values,
            )

    def find(self, track_id: str) -> DownloadRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM downloads WHERE track_id = ?",
                (track_id,),
            ).fetchone()
        return DownloadRecord(**dict(row)) if row else None

    def all(self) -> list[DownloadRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM downloads ORDER BY downloaded_at DESC"
            ).fetchall()
        return [DownloadRecord(**dict(row)) for row in rows]


class DownloadStaging:
    def __init__(self, output_dir: Path, repository: DownloadRepository) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.repository = repository

    @property
    def outtmpl(self) -> str:
        return output_template()

    def finalize(
        self,
        path: Path,
        track: TrackCandidate,
        *,
        audio_format: str = "m4a",
        source_format: str | None = None,
    ) -> DownloadRecord:
        if path.suffix.lower().lstrip(".") != audio_format.lower():
            raise InvalidMediaError(
                f"输出格式不匹配，期望 {audio_format}：{path.name}"
            )
        info = inspect_audio(path)
        record = DownloadRecord(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            path=str(path.resolve()),
            sha256=sha256_file(path),
            size=path.stat().st_size,
            source_url=track.webpage_url,
            downloaded_at=datetime.now(UTC).isoformat(),
            audio_format=audio_format,
            source_format=source_format,
            bitrate=info.bitrate,
            sample_rate=info.sample_rate,
            channels=info.channels,
        )
        self.repository.upsert(record)
        return record
