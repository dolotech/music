#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ -z "${SSL_CERT_FILE:-}" && -f /etc/ssl/cert.pem ]]; then
  export SSL_CERT_FILE=/etc/ssl/cert.pem
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：未找到 python3。请先安装 Python 3。" >&2
  exit 1
fi

VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "首次运行：创建 Python 虚拟环境..."
  python3 -m venv "$PROJECT_DIR/.venv"
fi

if ! "$VENV_PYTHON" -c "import yt_dlp, imageio_ffmpeg, mutagen, pygame, sounddevice, certifi, curl_cffi, brotli" >/dev/null 2>&1; then
  echo "首次运行：安装下载依赖..."
  if [[ -f "$PROJECT_DIR/requirements.lock" ]]; then
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -q \
      --require-hashes -r "$PROJECT_DIR/requirements.lock"
  else
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -q \
      -r "$PROJECT_DIR/requirements.txt"
  fi
fi

exec "$VENV_PYTHON" "$PROJECT_DIR/soundcloud_gui.py"
