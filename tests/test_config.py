from __future__ import annotations

import json

from music_app.config import AppConfig, ConfigStore


def test_config_defaults_for_missing_or_corrupt_file(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    assert store.load(tmp_path) == AppConfig()
    store.path.write_text("{bad", encoding="utf-8")
    assert store.load(tmp_path) == AppConfig()


def test_config_save_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "work" / "config.json"
    store = ConfigStore(path)
    config = AppConfig(
        download_dir="music",
        max_parallel_downloads=99,
        preview_cache_mb=1,
        search_limit=500,
        hide_short_previews=False,
        rights_acknowledged=True,
    )
    store.save(config)
    loaded = store.load(tmp_path)
    assert loaded.download_dir == "music"
    assert loaded.max_parallel_downloads == 8
    assert loaded.preview_cache_mb == 64
    assert loaded.search_limit == 100
    assert loaded.hide_short_previews is False
    assert loaded.rights_acknowledged is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["download_dir"] == "music"


def test_config_load_accepts_partial_payload(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"download_dir": "songs"}', encoding="utf-8")
    loaded = ConfigStore(path).load(tmp_path)
    assert loaded.download_dir == "songs"
    assert loaded.max_parallel_downloads == 3
