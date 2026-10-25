"""The CPU a translation unit is assembled for: `--arch` picks it per build."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

Arch = Literal["65c816", "spc700"]
ARCHES: tuple[Arch, ...] = ("65c816", "spc700")

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
