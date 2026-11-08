from __future__ import annotations

import os
import subprocess


def run_script(*args):
    return subprocess.run(
        ["./music.sh", *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_shell_scripts_are_executable_and_parse():
    for path in ("music.sh", "music_gui.sh"):
        assert os.access(path, os.X_OK)
        result = subprocess.run(
            ["bash", "-n", path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_music_help_without_dependencies():
    result = run_script("--help")
    assert result.returncode == 0
    assert "--accept-rights" in result.stdout


def test_music_requires_arguments():
    result = run_script()
    assert result.returncode == 64
    assert "用法" in result.stdout
