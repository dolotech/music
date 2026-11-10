from __future__ import annotations

import sys
import threading
import time
import wave
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from math import sin, tau
from types import SimpleNamespace

import imageio_ffmpeg
import pytest

from music_app.errors import AppError
from music_app.media import ResolvedMedia
from music_app.player import PygameBackend, StreamingPcmBackend


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


def test_streaming_backend_streams_pcm(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    written = []

    class Sink:
        def write(self, chunk):
            written.append(chunk)

        def abort(self):
            pass

    class Stdout:
        def __init__(self):
            self.chunks = [b"one", b"two", b""]

        def read(self, _size):
            return self.chunks.pop(0)

    class Process:
        pid = 123

        def __init__(self, *_args, **_kwargs):
            self.stdout = Stdout()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    commands = []
    monkeypatch.setattr(
        "music_app.player.subprocess.Popen",
        lambda command, **_kwargs: commands.append(command) or Process(),
    )
    backend = StreamingPcmBackend(
        ffmpeg_finder=lambda: "/ffmpeg",
        output_factory=Sink,
    )
    backend.load(
        ResolvedMedia(
            url="https://media/a.m3u8",
            headers={"User-Agent": "x"},
            duration=10,
        )
    )
    backend.play(5)
    deadline = time.monotonic() + 2
    while (
        backend._thread
        and backend._thread.is_alive()
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert written == [b"one", b"two"]
    assert "-ss" in commands[0]
    assert backend.last_error is None
    backend.close()


def test_streaming_backend_validation(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    backend = StreamingPcmBackend(
        ffmpeg_finder=lambda: None,
        output_factory=lambda: SimpleNamespace(
            write=lambda _x: None,
            abort=lambda: None,
        ),
    )
    with pytest.raises(AppError):
        backend.load(tmp_path / "a.mp3")
    backend.load(ResolvedMedia("https://media/a", {}, 1))
    with pytest.raises(AppError):
        backend.play(0)


def test_streaming_backend_pause_stops_worker(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    stopped = threading.Event()

    class BlockingStdout:
        def read(self, _size):
            stopped.wait(2)
            return b""

    class Process:
        stdout = BlockingStdout()

        def poll(self):
            return None

        def terminate(self):
            stopped.set()

        def wait(self, timeout=None):
            return 0

        def kill(self):
            stopped.set()

    class Sink:
        def write(self, _chunk):
            pass

        def abort(self):
            stopped.set()

    monkeypatch.setattr(
        "music_app.player.subprocess.Popen",
        lambda *_args, **_kwargs: Process(),
    )
    backend = StreamingPcmBackend(
        ffmpeg_finder=lambda: "/ffmpeg",
        output_factory=Sink,
    )
    backend.load(ResolvedMedia("https://media/a", {}, 10))
    backend.play(0)
    time.sleep(0.05)
    assert backend.is_buffering()
    backend.pause()
    assert not backend.get_busy()
    assert backend._thread is None


def test_streaming_backend_ignores_stopped_stream_race(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    write_entered = threading.Event()
    release_write = threading.Event()

    class Sink:
        def write(self, _chunk):
            write_entered.set()
            release_write.wait(2)
            raise RuntimeError("Stream is stopped [PaErrorCode -9983]")

        def abort(self):
            release_write.set()
            raise RuntimeError("Stream is stopped [PaErrorCode -9983]")

    class Stdout:
        def read(self, _size):
            return b"pcm"

    class Process:
        stdout = Stdout()
        returncode = 0

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(
        "music_app.player.subprocess.Popen",
        lambda *_args, **_kwargs: Process(),
    )
    backend = StreamingPcmBackend(
        ffmpeg_finder=lambda: "/ffmpeg",
        output_factory=Sink,
    )
    backend.load(ResolvedMedia("https://media/a", {}, 10))
    backend.play(0)
    assert write_entered.wait(1)
    backend.pause()
    assert backend.last_error is None
    assert not backend.get_busy()


def test_streaming_backend_real_ffmpeg_local_http(tmp_path):
    audio = tmp_path / "tone.wav"
    rate = 44100
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for index in range(rate):
            sample = int(6000 * sin(tau * 440 * index / rate))
            frames.extend(sample.to_bytes(2, "little", signed=True) * 2)
        wav.writeframes(frames)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    handler = partial(QuietHandler, directory=str(tmp_path))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    except PermissionError:
        pytest.skip("loopback sockets are unavailable in this sandbox")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class CountingSink:
        def __init__(self):
            self.bytes = 0

        def write(self, chunk):
            self.bytes += len(chunk)

        def abort(self):
            pass

    sink = CountingSink()
    backend = StreamingPcmBackend(
        ffmpeg_finder=imageio_ffmpeg.get_ffmpeg_exe,
        output_factory=lambda: sink,
    )
    backend.load(
        ResolvedMedia(
            f"http://127.0.0.1:{server.server_port}/tone.wav",
            {},
            1,
        )
    )
    try:
        backend.play(0)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and sink.bytes < 64 * 1024:
            time.sleep(0.02)
    finally:
        backend.pause()
        server.shutdown()
        server.server_close()
    assert sink.bytes >= 64 * 1024
    assert backend.last_error is None
