"""Paths a build looked for and did not find, recorded for the build cache.

`.include`, `.incbin` and `.table` names resolve against a search order (the
including file's directory or the working directory, then the include paths);
the first existing candidate wins. A file appearing later at an earlier
candidate would win instead, so every candidate tried before the hit is a
negative dependency: the cached object is stale once one of them exists.

Resolution happens deep in the parser and in nodes that have no handle on the
build, so misses go to whichever recorder is active in the current context.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_MISSES: ContextVar[set[str] | None] = ContextVar("a816_build_misses", default=None)


def record_miss(path: str | Path) -> None:
    """Note that `path` was tried and did not exist (no-op outside a recording)."""
    misses = _MISSES.get()
    if misses is not None:
        misses.add(os.path.abspath(str(path)))


def replay_misses(paths: set[str]) -> None:
    """Re-record misses captured earlier (a parse served from a cache)."""
    for path in paths:
        record_miss(path)


@contextmanager
def recording_misses() -> Iterator[set[str]]:
    """Collect the misses recorded while the block runs; an enclosing
    recording sees none of them unless the caller replays them."""
    misses: set[str] = set()
    token = _MISSES.set(misses)
    try:
        yield misses
    finally:
        _MISSES.reset(token)
