from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path

import pytest

from music_app.config import AppConfig
from music_app.downloads import DownloadProgress
from music_app.errors import DrmProtectedError, TaskCancelled
from music_app.media import ResolvedMedia
from music_app.models import DownloadRecord, TrackCandidate
from music_app.player import PlayerSnapshot
from music_app.security import CancellationToken
from music_app.ui import (
    AppServices,
    SoundCloudApp,
    create_default_services,
)

pytestmark = pytest.mark.gui


class FakeConfigStore:
    def __init__(self):
        self.saved = []

    def save(self, config):
        self.saved.append(config)


class FakeSearch:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def search(self, query, limit, token):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.results


class FakeDownload:
    def __init__(self, error=None, block=False):
        self.error = error
        self.block = block
        self.release = threading.Event()
        self.calls = []

    def download(self, track, token, on_progress, rights_acknowledged):
        self.calls.append((track, rights_acknowledged))
        on_progress(DownloadProgress(25, "正在下载 25%", 25, 100))
        if self.block:
            self.release.wait(2)
        if token.is_cancelled():
            raise TaskCancelled()
        if self.error:
            raise self.error
        on_progress(DownloadProgress(100, "下载完成", 100, 100))
        return DownloadRecord(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            path=str(Path("/tmp") / f"{track.title}.m4a"),
            sha256="hash",
            size=1,
            source_url=track.webpage_url,
            downloaded_at="now",
            audio_format="m4a",
        )


class FakePreview:
    def __init__(self, path):
        self.path = path
        self.calls = []

    def prepare(self, track, token, preserve=None):
        self.calls.append((track, preserve))
        token.raise_if_cancelled()
        return self.path


class FakePlayer:
    def __init__(self):
        self.loaded = None
        self.duration = 0
        self.playing = False
        self.position = 0.0
        self.calls = []

    def load(self, path, duration, autoplay=True):
        self.loaded = path
        self.duration = duration
        self.playing = autoplay
        self.position = 0.0
        self.calls.append(("load", path, duration))

    def toggle(self):
        self.playing = not self.playing
        self.calls.append(("toggle", self.playing))

    def seek(self, position):
        self.position = position
        self.calls.append(("seek", position))

    def stop(self):
        self.playing = False
        self.position = 0
        self.calls.append(("stop",))

    def release(self):
        self.loaded = None
        self.playing = False
        self.position = 0
        self.calls.append(("release",))

    def tick(self):
        return PlayerSnapshot(
            loaded=self.loaded is not None,
            playing=self.playing,
            position=self.position,
            duration=self.duration,
            finished=False,
        )


@pytest.fixture
def gui(monkeypatch, tmp_path):
    monkeypatch.setattr("music_app.ui.messagebox.showinfo", lambda *args: None)
    monkeypatch.setattr("music_app.ui.messagebox.showerror", lambda *args: None)
    monkeypatch.setattr("music_app.ui.messagebox.askyesno", lambda *args: True)
    root = tk.Tk()
    root.withdraw()
    config = AppConfig(download_dir=str(tmp_path), search_limit=10)
    store = FakeConfigStore()
    preview_file = tmp_path / "preview.mp3"
    preview_file.write_bytes(b"audio")
    download = FakeDownload()
    player = FakePlayer()
    services = AppServices(
        config=config,
        config_store=store,
        search_service=FakeSearch(),
        download_service=download,
        preview_service=FakePreview(preview_file),
        player=player,
        player_backend=None,
        logger=logging.getLogger("test-ui"),
    )
    app = SoundCloudApp(root, services=services)
    yield app, services
    try:
        if root.winfo_exists():
            app.close()
    except tk.TclError:
        pass


def candidates():
    return [
        TrackCandidate(
            "short",
            "爱很简单",
            "预览作者",
            30,
            "https://soundcloud.com/a/short",
            0.9,
            100,
        ),
        TrackCandidate(
            "full",
            "爱很简单",
            "完整作者",
            285,
            "https://soundcloud.com/a/full",
            0.8,
            10,
            downloadable=True,
        ),
        TrackCandidate(
            "drm",
            "DRM 歌曲",
            "作者",
            200,
            "https://soundcloud.com/a/drm",
            0.7,
            1000,
            is_drm=True,
        ),
    ]


def wait_until(root, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def select(app, track_id):
    app.tree.selection_set(track_id)
    app.tree.focus(track_id)
    app._on_selection()


def test_initial_state_and_short_filter(gui):
    app, services = gui
    app._show_search_results(0, "爱很简单", candidates())
    assert set(app.tree.get_children()) == {"full", "drm"}
    assert str(app.download_button["state"]) == "disabled"
    app.hide_short_var.set(False)
    app._on_preview_filter_change()
    assert "short" in app.tree.get_children()
    assert services.config_store.saved


def test_sorting_modes(gui):
    app, _services = gui
    app.hide_short_var.set(False)
    app.current_candidates = candidates()
    app.sort_var.set("匹配度优先")
    app._populate_results()
    assert app.tree.get_children()[0] == "short"
    app.sort_var.set("点赞数优先")
    app._populate_results()
    assert app.tree.get_children()[0] == "drm"
    app._sort_key(candidates()[1])


def test_search_worker_success_and_error(gui):
    app, services = gui
    services.search_service = FakeSearch(candidates())
    app.services.search_service = services.search_service
    app.search_var.set("爱很简单")
    app.start_search()
    assert wait_until(app.root, lambda: len(app.tree.get_children()) == 2)

    services.search_service = FakeSearch(error=RuntimeError("offline"))
    app.services.search_service = services.search_service
    app.search_var.set("another")
    app.start_search()
    assert wait_until(app.root, lambda: "操作失败" in app.status_var.get())


def test_search_focus_is_restored_on_repeated_search(gui):
    app, services = gui
    services.search_service = FakeSearch(candidates())
    app.services.search_service = services.search_service
    for index, query in enumerate(("成都", "成都 赵雷"), start=1):
        app.search_var.set(query)
        app.start_search()
        assert wait_until(
            app.root,
            lambda expected=index: app._current_search_generation() == expected
            and len(app.tree.get_children()) > 0,
        )
        assert app.search_entry.index("insert") == len(query)


def test_selection_rights_and_download_success(gui):
    app, services = gui
    app._show_search_results(0, "爱很简单", candidates())
    select(app, "full")
    assert str(app.download_button["state"]) == "disabled"
    app.rights_var.set(True)
    app._update_action_state()
    assert str(app.download_button["state"]) == "normal"
    app.start_download()
    assert wait_until(app.root, lambda: app.job_states.get("full") == "done")
    assert app.progress_var.get() == 100
    assert services.download_service.calls
    assert app.status_var.get().startswith("下载完成")


def test_download_error_and_cancel(gui):
    app, services = gui
    app._show_search_results(0, "爱很简单", candidates())
    select(app, "full")
    app.rights_var.set(True)
    app._update_action_state()
    services.download_service = FakeDownload(error=DrmProtectedError("DRM"))
    app.services.download_service = services.download_service
    app.start_download()
    assert wait_until(app.root, lambda: app.job_states.get("full") == "drm")
    assert app._state_text(app.candidates["full"]) == "DRM 受限"

    blocking = FakeDownload(block=True)
    services.download_service = blocking
    app.services.download_service = blocking
    app.job_states.pop("full", None)
    app.download_tokens.pop("full", None)
    select(app, "full")
    app.start_download()
    assert wait_until(app.root, lambda: "full" in app.download_tokens)
    app.cancel_selected_downloads()
    blocking.release.set()
    assert wait_until(app.root, lambda: app.job_states.get("full") == "cancelled")


def test_preview_playback_and_seek(gui):
    app, services = gui
    app._show_search_results(0, "爱很简单", candidates())
    select(app, "full")
    app.preview_button.configure(state="normal")
    app.start_preview()
    assert wait_until(app.root, lambda: app.services.player.loaded is not None)
    assert str(app.play_button["state"]) == "normal"
    app.toggle_playback()
    assert not app.services.player.playing
    app.player_position_var.set(120)
    app._on_seek_start()
    app._on_seek_end()
    assert app.services.player.position == 120
    app.stop_playback()
    assert app.services.player.position == 0


def test_preview_error(gui, monkeypatch):
    app, services = gui
    app._show_search_results(0, "爱很简单", candidates())
    select(app, "full")
    errors = []
    monkeypatch.setattr(
        "music_app.ui.messagebox.showerror",
        lambda title, message: errors.append((title, message)),
    )

    class BrokenPreview:
        def prepare(self, *_args, **_kwargs):
            raise RuntimeError("bad preview")

    services.preview_service = BrokenPreview()
    app.services.preview_service = services.preview_service
    app.start_preview()
    assert wait_until(app.root, lambda: bool(errors))


def test_close_saves_and_releases(gui):
    app, services = gui
    app.download_tokens["x"] = CancellationToken()
    app.preview_token = CancellationToken()
    app.close()
    assert services.config_store.saved
    assert app.download_tokens["x"].is_cancelled()
    assert app.preview_token.is_cancelled()


def test_empty_search_and_preview_filter_without_results(gui, monkeypatch):
    app, _services = gui
    infos = []
    monkeypatch.setattr(
        "music_app.ui.messagebox.showinfo",
        lambda title, message: infos.append((title, message)),
    )
    app.start_search()
    assert infos
    app.current_candidates = [
        TrackCandidate(
            "short",
            "短",
            "作者",
            30,
            "https://soundcloud.com/a/short",
            1,
        )
    ]
    app.hide_short_var.set(True)
    app._on_preview_filter_change()
    assert "取消“隐藏短预览”" in app.status_var.get()


def test_download_requires_selection_rights_and_confirmation(gui, monkeypatch):
    app, services = gui
    messages = []
    monkeypatch.setattr(
        "music_app.ui.messagebox.showinfo",
        lambda title, message: messages.append((title, message)),
    )
    app.start_download()
    assert messages

    app.hide_short_var.set(False)
    app.current_candidates = candidates()
    app._populate_results()
    select(app, "short")
    app.rights_var.set(False)
    app._update_action_state()
    assert str(app.download_button["state"]) == "disabled"
    app.rights_var.set(True)
    app._update_action_state()
    monkeypatch.setattr("music_app.ui.messagebox.askyesno", lambda *_args: False)
    app.start_download()
    assert services.download_service.calls == []


def test_download_error_summary_and_cancel_without_jobs(gui, monkeypatch):
    app, _services = gui
    app._show_search_results(0, "x", candidates())
    select(app, "full")
    errors = []
    monkeypatch.setattr(
        "music_app.ui.messagebox.showerror",
        lambda title, message: errors.append((title, message)),
    )
    track = app.candidates["full"]
    app._download_failed("full", track, OSError("No space left on device"))
    assert app.job_states["full"] == "error"
    assert errors and "磁盘空间不足" in errors[0][1]
    app.cancel_selected_downloads()
    assert "没有正在下载" in app.status_var.get()


def test_state_labels(gui):
    app, _services = gui
    app.current_candidates = candidates()
    app._populate_results()
    track = app.candidates["full"]
    app.job_states["full"] = "queued"
    assert app._state_text(track) == "排队中"
    app.job_states["full"] = "downloading"
    app.job_progress["full"] = 42
    assert app._state_text(track) == "下载 42%"
    app.job_states["full"] = "done"
    assert app._state_text(track) == "完成"
    app.job_states["full"] = "error"
    assert app._state_text(track) == "失败"
    app.job_states["full"] = "drm"
    assert app._state_text(track) == "DRM 受限"
    app.job_states["full"] = "cancelled"
    assert app._state_text(track) == "已取消"
    assert app._state_text(app.candidates["drm"]) == "DRM 受限"
    unavailable = replace(track, downloadable=False, is_drm=None)
    assert app._state_text(unavailable) == "不可下载"


def test_player_unavailable_and_errors(gui, monkeypatch):
    app, services = gui
    errors = []
    monkeypatch.setattr(
        "music_app.ui.messagebox.showerror",
        lambda title, message: errors.append((title, message)),
    )
    app._show_search_results(0, "x", candidates())
    select(app, "full")
    services.player = None
    app.services.player = None
    app.start_preview()
    assert errors

    class BrokenPlayer(FakePlayer):
        def toggle(self):
            raise RuntimeError("bad player")

    app.services.player = BrokenPlayer()
    app.toggle_playback()
    assert len(errors) >= 2


def test_player_update_and_open_actions(gui, monkeypatch):
    app, _services = gui
    opened = []
    monkeypatch.setattr("music_app.ui.webbrowser.open", opened.append)
    app._show_search_results(0, "x", candidates())
    select(app, "full")
    app._open_selected_page()
    assert opened

    class FinishedPlayer(FakePlayer):
        def tick(self):
            return PlayerSnapshot(True, False, 30, 30, True)

    app.services.player = FinishedPlayer()
    app._update_player_display()
    assert app.play_button["text"] == "重新播放"
    app._on_seek_start()
    app._on_seek_end()


def test_choose_directory_and_open_directory(gui, monkeypatch, tmp_path):
    app, _services = gui
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    monkeypatch.setattr(
        "music_app.ui.filedialog.askdirectory",
        lambda **_kwargs: str(chosen),
    )
    popen = []
    monkeypatch.setattr(
        "music_app.ui.subprocess.Popen",
        lambda args: popen.append(args),
    )
    app.choose_directory()
    assert app.download_dir == chosen
    assert app.services.config_store.saved
    app.open_download_directory()
    assert popen


def test_double_click_previews_without_downloading(gui, monkeypatch, tmp_path):
    app, services = gui
    app._show_search_results(0, "x", candidates())
    monkeypatch.setattr(app.tree, "identify_row", lambda _y: "full")
    preview = tmp_path / "double-click.mp3"
    preview.write_bytes(b"audio")
    services.preview_service.path = preview
    event = type(
        "Event",
        (),
        {"y": 1},
    )()
    app._on_double_click(event)
    assert wait_until(
        app.root,
        lambda: app.services.player.loaded == preview,
    )
    assert services.download_service.calls == []


def test_stale_search_result_and_preview_generation(gui):
    app, _services = gui
    app._search_generation = 2
    app._show_search_results(1, "old", candidates())
    assert not app.current_candidates
    app._preview_ready(99, candidates()[1], Path("/tmp/missing.mp3"))


def test_create_default_services_and_window(monkeypatch, tmp_path):
    monkeypatch.setattr("music_app.ui.PROJECT_DIR", tmp_path)
    monkeypatch.setattr("music_app.ui.DATA_DIR", tmp_path)
    monkeypatch.setattr("music_app.ui.WORK_DIR", tmp_path / "work")
    services = create_default_services()
    assert services.player is not None
    assert services.streaming_enabled is True
    assert services.config_store.path == tmp_path / "work" / "config.json"
    root = tk.Tk()
    root.withdraw()
    app = SoundCloudApp(root, services=services)
    assert app.max_parallel == 3
    app.close()


def test_switch_preview_cancels_previous_network(gui, tmp_path):
    app, _services = gui

    class SwitchingPreview:
        def __init__(self):
            self.started = threading.Event()
            self.tokens = []
            self.results = []

        def prepare(self, track, token, preserve=None):
            self.tokens.append(token)
            try:
                if track.track_id == "a":
                    self.started.set()
                    token.wait(2)
                    token.raise_if_cancelled()
                path = tmp_path / f"{track.track_id}.mp3"
                path.write_bytes(b"audio")
                self.results.append((track.track_id, path))
                return path
            except BaseException as exc:
                self.results.append((track.track_id, exc))
                raise

    preview = SwitchingPreview()
    app.services.preview_service = preview
    tracks = [
        TrackCandidate(
            "a",
            "歌曲 A",
            "作者",
            200,
            "https://soundcloud.com/a",
            1,
            downloadable=True,
        ),
        TrackCandidate(
            "b",
            "歌曲 B",
            "作者",
            210,
            "https://soundcloud.com/b",
            1,
            downloadable=True,
        ),
    ]
    app.current_candidates = tracks
    app._populate_results()
    select(app, "a")
    app.start_preview()
    assert preview.started.wait(1)
    assert str(app.preview_button["state"]) == "normal"
    assert app.preview_button["text"] == "切换试听"
    select(app, "b")
    app.start_preview()
    assert wait_until(
        app.root,
        lambda: len(preview.tokens) == 2,
        timeout=2,
    ), (len(preview.tokens), app.tree.selection(), app.preview_generation)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        app.root.update()
        while not app.events.empty():
            app._handle_event(app.events.get_nowait())
        if app.services.player.loaded == tmp_path / "b.mp3":
            break
        time.sleep(0.01)
    assert app.services.player.loaded == tmp_path / "b.mp3", preview.results
    assert preview.tokens[0].is_cancelled()
    assert not preview.tokens[1].is_cancelled()


def test_stop_during_preview_loading_cancels_transfer(gui, tmp_path):
    app, _services = gui
    token_holder = []

    class BlockingPreview:
        def prepare(self, track, token, preserve=None):
            token_holder.append(token)
            token.wait(2)
            token.raise_if_cancelled()
            return tmp_path / "never.mp3"

    app.services.preview_service = BlockingPreview()
    app._show_search_results(0, "x", candidates())
    select(app, "full")
    app.start_preview()
    assert wait_until(app.root, lambda: bool(token_holder))
    assert str(app.stop_button["state"]) == "normal"
    app.stop_playback()
    assert token_holder[0].is_cancelled()
    assert not app.preview_loading
    assert str(app.preview_button["state"]) == "normal"


def test_streaming_preview_uses_resolved_media(gui):
    app, services = gui

    class StreamingPreview(FakePreview):
        def resolve_media(self, track, token):
            token.raise_if_cancelled()
            return ResolvedMedia(
                "https://media.test/audio.m3u8",
                {"Referer": "https://soundcloud.com"},
                track.duration,
            )

    services.streaming_enabled = True
    services.preview_service = StreamingPreview(Path("/unused"))
    app._show_search_results(0, "x", candidates())
    select(app, "full")
    app.start_preview()
    assert wait_until(
        app.root,
        lambda: isinstance(app.services.player.loaded, ResolvedMedia),
    )
    assert app.services.player.loaded.url.endswith("audio.m3u8")


def test_playback_backend_error_classification(gui, monkeypatch):
    app, services = gui
    errors = []
    monkeypatch.setattr(
        "music_app.ui.messagebox.showerror",
        lambda title, message: errors.append((title, message)),
    )
    cases = [
        (
            "SSL: UNEXPECTED_EOF_WHILE_READING",
            "音频流网络失败",
        ),
        (
            "音频流首包超时",
            "音频流加载失败",
        ),
        (
            "Error querying device -1",
            "音频输出失败",
        ),
    ]
    for message, expected_title in cases:
        services.player_backend = type(
            "Backend",
            (),
            {"last_error": message},
        )()
        app._reported_audio_error = None
        app._update_player_display()
        assert errors[-1][0] == expected_title
    assert "音频输出失败" in app.status_var.get()


def test_player_displays_buffering_state(gui):
    app, _services = gui

    class BufferingPlayer(FakePlayer):
        def __init__(self):
            super().__init__()
            self.buffering = True

        def tick(self):
            return PlayerSnapshot(
                loaded=True,
                playing=True,
                position=0,
                duration=120,
                finished=False,
                buffering=self.buffering,
            )

    app.services.player = BufferingPlayer()
    app._active_track_title = "缓冲测试"
    app._update_player_display()
    assert "正在缓冲音频流" in app.status_var.get()
    app.services.player.buffering = False
    app._update_player_display()
    assert "正在流式试听整首" in app.status_var.get()
