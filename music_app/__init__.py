"""Core services for the SoundCloud desktop client."""

import os

from .models import TrackCandidate
from .version import __version__

try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

__all__ = ["TrackCandidate", "__version__"]
