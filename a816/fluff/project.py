"""The sources a project's `a816.toml` describes, for fluff runs given no paths."""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404: runs git, resolved to a full path, with fixed arguments
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

from a816.config import load_a816_toml

SOURCE_SUFFIXES = frozenset({".s", ".i"})
# Generated or third-party trees: never lint roots (dot-dirs are skipped too).
SKIPPED_DIRS = frozenset({"build", "obj", ".venv", "venv", "node_modules", ".git"})


def project_sources(toml: Path) -> list[Path]:
    """The `.s` / `.i` files under the entrypoint's directory and every
    `module-paths` directory (the toml's directory without an entrypoint),
    generated directories left out, sorted and deduplicated.

    In a git work tree only the files git does not ignore count, and the
    toml's `exclude` patterns leave out files and directories (scratch
    sources that are untracked but not ignored).

    `include-paths` are not roots: they hold generated and asset files.
    """
    config = load_a816_toml(toml)
    root = toml.parent.resolve()
    roots = [root]
    exclude: list[str] = []
    if config is not None:
        exclude = config.exclude
        if config.entrypoint is not None:
            roots = [config.entrypoint.parent, *config.module_paths]
    found = {source for directory in roots for source in _visible_sources(directory)}
    return sorted(source for source in found if not matches_any(source, root, exclude))


def _visible_sources(directory: Path) -> Iterator[Path]:
    """`walk_sources` of `directory`, without what its git work tree ignores."""
    visible = _git_visible(directory)
    for source in walk_sources(directory, recursive=True):
        if visible is None or source in visible:
            yield source


def _git_visible(directory: Path) -> set[Path] | None:
    """The files under `directory` git tracks or would track (not ignored),
    or None outside a work tree or without git."""
    git = shutil.which("git")
    if git is None or not directory.is_dir():
        return None
    listing = subprocess.run(  # nosec B603: fixed arguments, no shell
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    if listing.returncode != 0:
        return None
    names = listing.stdout.decode("utf-8", "surrogateescape").split("\0")
    return {(directory / name).resolve() for name in names if name}


def matches_any(source: Path, root: Path, patterns: Sequence[str]) -> bool:
    """`source`, or a directory above it up to `root`, matches one of the
    glob `patterns` (relative to `root`): `exclude`, per-file ignores.
    `{a,b}` alternatives expand as in ruff and editorconfig."""
    if not patterns or not source.is_relative_to(root):
        return False
    relative = PurePosixPath(source.relative_to(root).as_posix())
    candidates = [relative, *list(relative.parents)[:-1]]
    globs = [glob for pattern in patterns for glob in expand_braces(pattern)]
    return any(candidate.full_match(glob) for candidate in candidates for glob in globs)


_BRACES = re.compile(r"\{([^{}]*)\}")


def expand_braces(pattern: str) -> list[str]:
    """`src/{menu,miss}.s` as `src/menu.s` and `src/miss.s`; a pattern without braces as itself."""
    found = _BRACES.search(pattern)
    if found is None:
        return [pattern]
    head, tail = pattern[: found.start()], pattern[found.end() :]
    return [glob for option in found.group(1).split(",") for glob in expand_braces(head + option + tail)]


def walk_sources(root: Path, recursive: bool) -> Iterator[Path]:
    """Source files under `root`, skipping generated and dot directories."""
    for source in root.rglob("*") if recursive else root.glob("*"):
        if source.suffix not in SOURCE_SUFFIXES or not source.is_file():
            continue
        parts = source.relative_to(root).parts[:-1]
        if any(part in SKIPPED_DIRS or part.startswith(".") for part in parts):
            continue
        yield source.resolve()
