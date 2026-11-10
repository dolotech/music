from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "SoundCloudMusic"


def app_data_dir() -> Path:
    override = os.environ.get("SOUNDCLOUD_MUSIC_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / APP_DIR_NAME
    return Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ) / APP_DIR_NAME
