from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tkinter
from pathlib import Path

from .ffmpeg_tools import find_ffmpeg
from .paths import app_data_dir
from .version import __version__


def run_selftest() -> dict:
    checks: dict[str, object] = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir": str(app_data_dir()),
        "tk": tkinter.TkVersion,
    }
    failures = []

    try:
        import brotli  # noqa: F401
        import certifi
        import curl_cffi
        import mutagen  # noqa: F401
        import pygame  # noqa: F401
        import sounddevice  # noqa: F401
        import yt_dlp

        checks["yt_dlp"] = yt_dlp.version.__version__
        checks["certifi"] = certifi.where()
        checks["curl_cffi"] = curl_cffi.__version__
        checks["brotli"] = True
    except Exception as exc:
        failures.append(f"dependency import failed: {exc}")

    try:
        import sounddevice

        device = sounddevice.query_devices(kind="output")
        checks["audio_output"] = device.get("name")
    except Exception as exc:
        failures.append(f"audio output unavailable: {exc}")

    ffmpeg = find_ffmpeg()
    checks["ffmpeg"] = ffmpeg
    if not ffmpeg:
        failures.append("ffmpeg not found")
    else:
        bundle_root_value = getattr(sys, "_MEIPASS", "")
        if (
            getattr(sys, "frozen", False)
            and bundle_root_value
            and not Path(ffmpeg).resolve().is_relative_to(
                Path(bundle_root_value).resolve()
            )
        ):
            failures.append("ffmpeg is not bundled inside the application")
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            failures.append("ffmpeg failed to start")
        else:
            checks["ffmpeg_version"] = result.stdout.splitlines()[0]

    data_dir = app_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".selftest"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir_writable"] = True
    except OSError as exc:
        checks["data_dir_writable"] = False
        failures.append(f"data directory not writable: {exc}")

    checks["ok"] = not failures
    checks["failures"] = failures
    return checks


def main() -> int:
    report = run_selftest()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    report_path = os.environ.get("SOUNDCLOUD_MUSIC_SELFTEST_REPORT")
    if report_path:
        Path(report_path).write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
