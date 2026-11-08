from __future__ import annotations

import soundcloud_downloader
import soundcloud_gui


def test_gui_wrapper_exports():
    assert soundcloud_gui.SoundCloudApp
    assert soundcloud_gui.TrackCandidate
    assert soundcloud_gui.format_duration(65) == "1:05"


def test_cli_wrapper_exports():
    assert soundcloud_downloader.build_parser
    assert soundcloud_downloader.resolve_tracks
