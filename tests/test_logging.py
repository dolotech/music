from __future__ import annotations

import json
import logging

from music_app.json_logging import JsonFormatter, configure_logging


def test_json_formatter_redacts_and_includes_fields():
    record = logging.LogRecord(
        "music_app",
        logging.ERROR,
        "",
        0,
        "failed https://media.test/x?token=secret",
        (),
        None,
    )
    record.event = "download_failed"
    record.track_id = "1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "download_failed"
    assert payload["track_id"] == "1"
    assert "secret" not in payload["message"]


def test_configure_logging_replaces_owned_handler(tmp_path):
    path = tmp_path / "logs" / "app.jsonl"
    logger = configure_logging(path)
    configure_logging(path)
    logger.error("boom", extra={"event": "test_event"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "test_event"
