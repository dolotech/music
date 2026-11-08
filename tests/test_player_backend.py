from __future__ import annotations

import sys
from types import SimpleNamespace

from music_app.player import PygameBackend


def test_pygame_backend_delegates(monkeypatch, tmp_path):
    calls = []

    class Music:
        def load(self, path):
            calls.append(("load", path))

        def play(self, start):
            calls.append(("play", start))

        def pause(self):
            calls.append(("pause",))

        def stop(self):
            calls.append(("stop",))

        def unload(self):
            calls.append(("unload",))

        def get_busy(self):
            return True

    mixer = SimpleNamespace(
        init=lambda **_kwargs: calls.append(("init",)),
        quit=lambda: calls.append(("quit",)),
        music=Music(),
    )
    monkeypatch.setitem(sys.modules, "pygame", SimpleNamespace(mixer=mixer))
    backend = PygameBackend()
    backend.load(tmp_path / "a.mp3")
    backend.play(2)
    backend.pause()
    backend.stop()
    backend.unload()
    assert backend.get_busy()
    backend.close()
    assert ("play", 2) in calls
    assert ("quit",) in calls
