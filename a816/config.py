"""Shared loader for `a816.toml` project configuration.

Both the LSP and `a816 fluff` need to find the project root, the
include search paths, and the prelude file. Centralised here so the
schema lives in one place.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "a816.toml"


@dataclass(frozen=True)
class A816Config:
    """Resolved view of a project's `a816.toml` settings."""

    config_path: Path
    entrypoint: Path | None = None
    include_paths: list[Path] = field(default_factory=list)
    module_paths: list[Path] = field(default_factory=list)
    prelude_file: Path | None = None

    @property
    def root(self) -> Path:
        return self.config_path.parent


def find_a816_toml(start: Path) -> Path | None:
    """Walk upwards from `start` looking for `a816.toml`. Return its path or None."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def _resolve_paths(root: Path, raw: list[str]) -> list[Path]:
    return [(root / item).resolve() for item in raw]


def load_a816_toml(config_path: Path) -> A816Config | None:
    """Parse the project config. Return None on read / decode errors."""
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    root = config_path.parent
    entry = data.get("entrypoint")
    entry_path = (root / entry).resolve() if isinstance(entry, str) else None
    prelude = data.get("prelude")
    prelude_path = (root / prelude).resolve() if isinstance(prelude, str) else None
    return A816Config(
        config_path=config_path,
        entrypoint=entry_path,
        include_paths=_resolve_paths(root, data.get("include-paths", []) or []),
        module_paths=_resolve_paths(root, data.get("module-paths", []) or []),
        prelude_file=prelude_path,
    )


def discover_a816_config(start: Path) -> A816Config | None:
    """Find + load the nearest `a816.toml` above `start`, or return None."""
    found = find_a816_toml(start)
    if found is None:
        return None
    return load_a816_toml(found)
