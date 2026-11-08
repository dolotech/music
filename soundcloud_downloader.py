#!/usr/bin/env python3
"""Compatibility entry point for the modular downloader CLI."""

from music_app.cli import build_parser, main, resolve_tracks

__all__ = ["build_parser", "main", "resolve_tracks"]


if __name__ == "__main__":
    raise SystemExit(main())
