#!/usr/bin/env python3
"""Compatibility entry point for the modular desktop application."""

import sys

from music_app.models import (
    TrackCandidate,
    format_count,
    format_duration,
    fuzzy_score,
)
from music_app.ui import SoundCloudApp, create_default_services, main

__all__ = [
    "SoundCloudApp",
    "TrackCandidate",
    "create_default_services",
    "format_count",
    "format_duration",
    "fuzzy_score",
    "main",
]


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from music_app.selftest import main as selftest_main

        raise SystemExit(selftest_main())
    raise SystemExit(main())
