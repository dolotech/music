from __future__ import annotations

import json

from music_app import paths, selftest


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SOUNDCLOUD_MUSIC_DATA_DIR", str(tmp_path))
    assert paths.app_data_dir() == tmp_path


def test_selftest_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(selftest, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(selftest, "find_ffmpeg", lambda: "/bin/true")
    monkeypatch.setattr(selftest.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(
        selftest.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {"returncode": 0, "stdout": "ffmpeg version test\n"},
        )(),
    )
    import sounddevice

    monkeypatch.setattr(
        sounddevice,
        "query_devices",
        lambda **_kwargs: {"name": "Test Speakers"},
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setenv("SOUNDCLOUD_MUSIC_SELFTEST_REPORT", str(report_path))
    code = selftest.main()
    assert code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["audio_output"] == "Test Speakers"
    assert "python" in capsys.readouterr().out


def test_selftest_failure_report(monkeypatch, tmp_path):
    monkeypatch.setattr(selftest, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(selftest, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(selftest.platform, "platform", lambda: "test-platform")
    import sounddevice

    def no_audio(**_kwargs):
        raise RuntimeError("no output")

    monkeypatch.setattr(sounddevice, "query_devices", no_audio)
    report = selftest.run_selftest()
    assert report["ok"] is False
    assert any("audio output" in item for item in report["failures"])
    assert any("ffmpeg" in item for item in report["failures"])
