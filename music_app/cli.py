from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .downloads import DownloadProgress, DownloadService, DownloadSettings
from .errors import AppError, TaskCancelled, user_error_text
from .models import TrackCandidate, format_duration, track_from_entry
from .security import CancellationToken, normalize_soundcloud_url
from .storage import DownloadRepository, DownloadStaging

RIGHTS_NOTICE = (
    "仅下载你拥有版权、已获授权，或版权方明确允许下载的内容。"
)


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def soundcloud_url(value: str) -> str:
    try:
        return normalize_soundcloud_url(value)
    except AppError as exc:
        raise argparse.ArgumentTypeError(exc.message) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检索并下载你有权保存的 SoundCloud 音频。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("urls", nargs="+", type=soundcloud_url, metavar="URL")
    parser.add_argument("-o", "--output", type=Path, default=Path("download"))
    parser.add_argument(
        "--audio-format",
        choices=("original", "mp3", "m4a", "opus", "flac"),
        default="m4a",
    )
    parser.add_argument("--audio-quality", default="320")
    parser.add_argument("--sample-rate", type=positive_int, default=44100)
    parser.add_argument("--channels", type=positive_int, default=2)
    parser.add_argument("--cookies", type=Path, metavar="COOKIE_FILE")
    parser.add_argument("--no-playlist", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--embed-metadata", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retries", type=positive_int, default=10)
    parser.add_argument("--timeout", type=positive_int, default=30)
    parser.add_argument("--proxy")
    parser.add_argument(
        "--accept-rights",
        action="store_true",
        help="确认你有权保存所下载内容",
    )
    return parser


def resolve_tracks(
    url: str,
    *,
    cookies: Path | None,
    proxy: str | None,
    no_playlist: bool,
) -> list[TrackCandidate]:
    import yt_dlp

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "noplaylist": no_playlist,
    }
    if cookies:
        options["cookiefile"] = str(cookies)
    if proxy:
        options["proxy"] = proxy
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    tracks: list[TrackCandidate] = []

    def collect(value: dict | None) -> None:
        if not value:
            return
        if value.get("entries") is not None:
            for entry in value.get("entries") or []:
                collect(entry)
            return
        candidate = track_from_entry(value, str(value.get("title") or ""))
        if candidate is not None:
            try:
                normalize_soundcloud_url(candidate.webpage_url)
            except AppError:
                return
            tracks.append(candidate)

    collect(info)
    return tracks


def print_tracks(tracks: list[TrackCandidate]) -> None:
    for index, track in enumerate(tracks, start=1):
        print(
            f"{index}. {track.title} — {track.artist} "
            f"({format_duration(track.duration)})"
        )
        print(f"   {track.webpage_url}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"提示：{RIGHTS_NOTICE}\n")

    cookies = args.cookies.expanduser().resolve() if args.cookies else None
    if cookies is not None and not cookies.is_file():
        parser.error(f"Cookie 文件不存在：{cookies}")
    if not args.dry_run and not args.accept_rights:
        parser.error("下载前必须传入 --accept-rights 确认你有权保存该内容")

    output_dir = args.output.expanduser().resolve()
    repository = DownloadRepository(output_dir / ".music_index.sqlite3")
    staging = DownloadStaging(output_dir, repository)
    settings = DownloadSettings(
        audio_format=args.audio_format,
        audio_quality=args.audio_quality,
        sample_rate=args.sample_rate,
        channels=args.channels,
        no_playlist=args.no_playlist,
        embed_metadata=args.embed_metadata,
        retries=args.retries,
        timeout=args.timeout,
        force=args.force,
        proxy=args.proxy,
        cookie_file=cookies,
    )
    service = DownloadService(staging, settings=settings)
    failures = 0

    for url in args.urls:
        try:
            tracks = resolve_tracks(
                url,
                cookies=cookies,
                proxy=args.proxy,
                no_playlist=args.no_playlist,
            )
            if not tracks:
                raise AppError("no_results", "未找到可处理的曲目。")
            if args.dry_run:
                print_tracks(tracks)
                continue

            for index, track in enumerate(tracks, start=1):
                token = CancellationToken()
                print(f"[{index}/{len(tracks)}] {track.title} — {track.artist}")

                def progress(event: DownloadProgress) -> None:
                    print(
                        f"\r{event.message}",
                        end="",
                        flush=True,
                    )

                try:
                    record = service.download(
                        track,
                        token,
                        progress,
                        rights_acknowledged=True,
                    )
                    print(f"\r已保存：{record.path}")
                except KeyboardInterrupt:
                    token.cancel("用户中断")
                    print("\n已取消。", file=sys.stderr)
                    return 130
        except (KeyboardInterrupt, TaskCancelled):
            print("\n已取消。", file=sys.stderr)
            return 130
        except Exception as exc:
            failures += 1
            print(user_error_text(exc), file=sys.stderr)

    if failures:
        print(f"完成，但有 {failures} 个任务失败。", file=sys.stderr)
        return 1
    print(f"处理完成。文件目录：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
