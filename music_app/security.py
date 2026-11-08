from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .errors import InvalidSoundCloudUrl, TaskCancelled

SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(si|token|signature|policy|key-pair-id|expires)=([^&\s]+)"
)


def normalize_soundcloud_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    is_soundcloud = host == "soundcloud.com" or host.endswith(".soundcloud.com")
    if parsed.scheme not in {"http", "https"} or not is_soundcloud:
        raise InvalidSoundCloudUrl(value)
    return value


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    query = "<redacted>" if parsed.query else ""
    return urlunsplit(
        SplitResult(parsed.scheme, parsed.netloc, parsed.path, query, "")
    )


def redact_text(value: object) -> str:
    text = str(value)
    text = SENSITIVE_QUERY_RE.sub(r"\1=<redacted>", text)
    text = re.sub(
        r"https?://[^\s]+",
        lambda match: redact_url(match.group(0)),
        text,
    )
    return text


@dataclass
class CancellationToken:
    _event: threading.Event = field(default_factory=threading.Event)
    _reason: str = "任务已取消"

    def cancel(self, reason: str = "任务已取消") -> None:
        self._reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise TaskCancelled(self._reason)
