"""Shared utilities used across multiple a816 subsystems."""

from pathlib import Path
from urllib.parse import unquote, urlparse


def uri_to_path(uri: str) -> Path:
    """Convert a file:// URI (or a plain path) to a Path."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(uri)


def resolve_asset_path(path: str, include_paths: list[Path]) -> str:
    """Resolve a `.incbin` / `.table` path against `include_paths`.

    Absolute paths and paths that exist relative to cwd are returned
    as-is (cwd preserves legacy behaviour). Otherwise the first hit
    across `include_paths` wins.

    Miss semantics: returns the original path unchanged. The caller's
    `open()` then raises with the source's actual literal so the
    error message points at the user-written string, not at the
    last include-paths candidate we happened to try. Do not raise
    here.
    """
    candidate = Path(path)
    if candidate.is_absolute() or candidate.exists():
        return path
    for base in include_paths:
        hit = base / path
        if hit.exists():
            return str(hit)
    return path
