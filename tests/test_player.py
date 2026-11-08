from __future__ import annotations

import pytest

from music_app.errors import AppError
from music_app.player import AudioPlayer


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class Backend:
    def __init__(self):
        self.loaded = None
        self.playing = False
        self.busy = True
        self.calls = []
        self.load_error = None
        self.play_error = None

    def load(self, path):
        if self.load_error:
            raise self.load_error
        self.loaded = path
        self.calls.append(("load", path))

    def play(self, start):
        if self.play_error:
            raise self.play_error
        self.playing = True
        self.calls.append(("play", start))

    def pause(self):
        self.playing = False
        self.calls.append(("pause",))

    def stop(self):
        self.playing = False
        self.calls.append(("stop",))

    def unload(self):
        self.loaded = None
        self.calls.append(("unload",))

    def get_busy(self):
        return self.busy


def test_player_full_lifecycle(tmp_path):
    clock = Clock()
    backend = Backend()
    player = AudioPlayer(backend, clock)
    path = tmp_path / "a.mp3"
    player.load(path, 10)
    assert player.loaded and player.playing
    clock.value = 2
    assert player.tick().position == 2
    player.toggle()
    assert not player.playing
    player.toggle()
    assert player.playing
    player.seek(5)
    assert player.position == 5
    player.stop()
    assert player.position == 0
    player.release()
    assert not player.loaded
    assert backend.calls[-1] == ("unload",)


def test_player_finished_and_replay(tmp_path):
    clock = Clock()
    backend = Backend()
    player = AudioPlayer(backend, clock)
    player.load(tmp_path / "a.mp3", 10)
    backend.busy = False
    snapshot = player.tick()
    assert snapshot.finished
    assert snapshot.position == 10
    player.toggle()
    assert player.playing
    assert player.position == 0


def test_player_errors(tmp_path):
    backend = Backend()
    backend.load_error = RuntimeError("bad format")
    player = AudioPlayer(backend)
    with pytest.raises(AppError) as caught:
        player.load(tmp_path / "a.mp3", 10)
    assert caught.value.code == "player_load_failed"

    backend.load_error = None
    backend.play_error = RuntimeError("no device")
    with pytest.raises(AppError) as caught:
        player.load(tmp_path / "a.mp3", 10)
    assert caught.value.code == "player_play_failed"


def test_player_ignores_operations_before_load():
    player = AudioPlayer(Backend())
    player.toggle()
    player.seek(2)
    player.stop()
    assert not player.loaded
    assert player.tick().loaded is False


def test_player_seek_while_paused_and_release_errors(tmp_path):
    backend = Backend()
    player = AudioPlayer(backend)
    player.load(tmp_path / "a.mp3", 10, autoplay=False)
    player.seek(4)
    assert player.position == 4
    assert not player.playing

    class BrokenBackend(Backend):
        def stop(self):
            raise RuntimeError("stop")

        def unload(self):
            raise RuntimeError("unload")

    broken = AudioPlayer(BrokenBackend())
    broken.release()
    assert not broken.loaded
