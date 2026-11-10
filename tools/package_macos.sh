#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PY="./.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

"$PY" -m ruff check music_app tests tools soundcloud_gui.py soundcloud_downloader.py
"$PY" -m mypy music_app --ignore-missing-imports
SDL_AUDIODRIVER=dummy "$PY" -m pytest -m "not network"
"$PY" tools/build_pyinstaller.py SoundCloudMusic.spec --noconfirm --clean "$@"
