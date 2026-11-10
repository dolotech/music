from __future__ import annotations

import pytest

from music_app.errors import (
    AppError,
    AuthenticationRequired,
    DrmProtectedError,
    InvalidSoundCloudUrl,
    NetworkError,
    RateLimitedError,
    RightsNotAcknowledged,
    TaskCancelled,
    map_exception,
    strip_ansi,
    user_error_text,
)


def test_strip_ansi():
    assert strip_ansi("\x1b[0;31mERROR:\x1b[0m bad") == "ERROR: bad"


@pytest.mark.parametrize(
    ("text", "error_type"),
    [
        ("This video is DRM protected", DrmProtectedError),
        ("HTTP Error 429: Too Many Requests", RateLimitedError),
        ("HTTP Error 401: Unauthorized", AuthenticationRequired),
        ("temporary failure in name resolution", NetworkError),
        ("connection reset by peer", NetworkError),
        ("SSL: UNEXPECTED_EOF_WHILE_READING", NetworkError),
        ("SSL_ERROR_SYSCALL", NetworkError),
        ("SSL_connect: Connection closed abruptly", NetworkError),
        ("operation timed out", NetworkError),
    ],
)
def test_map_exception(text, error_type):
    assert isinstance(map_exception(RuntimeError(text)), error_type)


def test_map_known_and_generic_errors():
    known = AppError("known", "message")
    assert map_exception(known) is known
    disk = map_exception(OSError("No space left on device"))
    assert disk.code == "disk_full"
    generic = map_exception(ValueError("unexpected"))
    assert generic.code == "operation_failed"


def test_user_error_text_includes_detail():
    text = user_error_text(RuntimeError("HTTP Error 403: Forbidden"))
    assert "登录或有效权限" in text
    assert "HTTP Error 403" in text
    assert (
        user_error_text(RuntimeError("x"), include_detail=False)
        == "操作失败，请查看诊断信息。"
    )


def test_error_construction():
    assert TaskCancelled().code == "cancelled"
    assert RightsNotAcknowledged().code == "rights_not_acknowledged"
    assert InvalidSoundCloudUrl("x").code == "invalid_url"
