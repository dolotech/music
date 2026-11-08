from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    download_dir: str = "download"
    max_parallel_downloads: int = 3
    preview_cache_mb: int = 512
    search_limit: int = 30
    hide_short_previews: bool = True
    rights_acknowledged: bool = False

    def validate(self) -> None:
        self.max_parallel_downloads = max(
            1, min(8, int(self.max_parallel_downloads))
        )
        self.preview_cache_mb = max(64, min(4096, int(self.preview_cache_mb)))
        self.search_limit = max(1, min(100, int(self.search_limit)))


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, project_dir: Path) -> AppConfig:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            config = AppConfig(
                download_dir=str(payload.get("download_dir", "download")),
                max_parallel_downloads=int(
                    payload.get("max_parallel_downloads", 3)
                ),
                preview_cache_mb=int(payload.get("preview_cache_mb", 512)),
                search_limit=int(payload.get("search_limit", 30)),
                hide_short_previews=bool(
                    payload.get("hide_short_previews", True)
                ),
                rights_acknowledged=bool(
                    payload.get("rights_acknowledged", False)
                ),
            )
            config.validate()
            return config
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with suppress(OSError):
            os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
