"""The CPU a source is assembled for: its suffix (`driver.spc.s`), else the build's `--arch`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

Arch = Literal["65c816", "spc700"]
ARCHES: tuple[Arch, ...] = ("65c816", "spc700")

ARAM_END = 0xFFFF
"""Last ARAM address: the SPC700 sees one flat 64 KB bank."""

current_arch: ContextVar[Arch] = ContextVar("current_arch", default="65c816")


def is_spc700() -> bool:
    return current_arch.get() == "spc700"


@contextmanager
def using_arch(arch: Arch) -> Iterator[None]:
    token = current_arch.set(arch)
    try:
        yield
    finally:
        current_arch.reset(token)


# An SPC700 source names itself: `driver.spc.s`, `dsp.spc.i`.
SPC700_SUFFIXES = (".spc.s", ".spc.i")


def arch_for_path(path: str | Path) -> Arch:
    """The CPU a source file is written for: SPC700 by its suffix, else the build's."""
    return "spc700" if str(path).lower().endswith(SPC700_SUFFIXES) else current_arch.get()
