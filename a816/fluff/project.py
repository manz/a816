"""The sources a project's `a816.toml` describes, for fluff runs given no paths."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from a816.config import load_a816_toml

SOURCE_SUFFIXES = frozenset({".s", ".i"})
# Generated or third-party trees: never lint roots (dot-dirs are skipped too).
SKIPPED_DIRS = frozenset({"build", "obj", ".venv", "venv", "node_modules", ".git"})


def project_sources(toml: Path) -> list[Path]:
    """The `.s` / `.i` files under the entrypoint's directory and every
    `module-paths` directory (the toml's directory without an entrypoint),
    generated directories left out, sorted and deduplicated.

    `include-paths` are not roots: they hold generated and asset files.
    """
    config = load_a816_toml(toml)
    root = toml.parent.resolve()
    roots = [root]
    if config is not None and config.entrypoint is not None:
        roots = [config.entrypoint.parent, *config.module_paths]
    found = {source for directory in roots for source in walk_sources(directory, recursive=True)}
    return sorted(found)


def walk_sources(root: Path, recursive: bool) -> Iterator[Path]:
    """Source files under `root`, skipping generated and dot directories."""
    for source in root.rglob("*") if recursive else root.glob("*"):
        if source.suffix not in SOURCE_SUFFIXES or not source.is_file():
            continue
        parts = source.relative_to(root).parts[:-1]
        if any(part in SKIPPED_DIRS or part.startswith(".") for part in parts):
            continue
        yield source.resolve()
