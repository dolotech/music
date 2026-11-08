from __future__ import annotations

import pytest

from music_app.errors import InvalidSoundCloudUrl, TaskCancelled
from music_app.security import (
    CancellationToken,
    normalize_soundcloud_url,
    redact_text,
    redact_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://soundcloud.com/a/b",
        "https://on.soundcloud.com/abc",
        "http://m.soundcloud.com/a",
    ],
)
def test_normalize_soundcloud_url_accepts(url):
    assert normalize_soundcloud_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://soundcloud.com.evil.test/a",
        "ftp://soundcloud.com/a",
        "javascript:alert(1)",
    ],
)
def test_normalize_soundcloud_url_rejects(url):
    with pytest.raises(InvalidSoundCloudUrl):
        normalize_soundcloud_url(url)


def test_redaction():
    assert (
        redact_url("https://soundcloud.com/a/b?si=secret&utm_source=x")
        == "https://soundcloud.com/a/b?<redacted>"
    )
    assert redact_url("not a url") == "<invalid-url>"
    text = redact_text(
        "https://media.test/a?token=secret&expires=1 Cookie=abc"
    )
    assert "secret" not in text
    assert "media.test" in text


def test_cancellation_token():
    token = CancellationToken()
    assert not token.is_cancelled()
    token.raise_if_cancelled()
    token.cancel("stop")
    assert token.wait(0)
    assert token.is_cancelled()
    with pytest.raises(TaskCancelled):
        token.raise_if_cancelled()
