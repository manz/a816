"""Shared utilities used across multiple a816 subsystems."""

from pathlib import Path
from urllib.parse import unquote, urlparse


def uri_to_path(uri: str) -> Path:
    """Convert a file:// URI (or a plain path) to a Path."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(uri)
