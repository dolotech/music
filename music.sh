#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ $# -eq 0 ]]; then
  echo "用法：./music.sh <SoundCloud链接> [更多链接或参数]"
  exit 64
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  exec python3 "$PROJECT_DIR/soundcloud_downloader.py" "$@"
fi

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

if ! "$VENV_PYTHON" -c "import yt_dlp, imageio_ffmpeg, mutagen" >/dev/null 2>&1; then
  echo "首次运行：安装下载依赖..."
  if [[ -f "$PROJECT_DIR/requirements.lock" ]]; then
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -q \
      --require-hashes -r "$PROJECT_DIR/requirements.lock"
  else
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -q \
      -r "$PROJECT_DIR/requirements.txt"
  fi
fi

DRY_RUN=false
HAS_RIGHTS=false
for arg in "$@"; do
  [[ "$arg" == "--dry-run" ]] && DRY_RUN=true
  [[ "$arg" == "--accept-rights" ]] && HAS_RIGHTS=true
done

if [[ "$DRY_RUN" == false && "$HAS_RIGHTS" == false ]]; then
  echo "仅下载你拥有版权、已获授权，或版权方明确允许下载的内容。"
  if [[ -t 0 ]]; then
    read -r -p "确认有权保存所下载内容？[y/N] " answer
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
      echo "已取消。"
      exit 2
    fi
  else
    echo "非交互运行必须传入 --accept-rights。" >&2
    exit 2
  fi
  set -- "$@" --accept-rights
fi

exec "$VENV_PYTHON" "$PROJECT_DIR/soundcloud_downloader.py" "$@"
