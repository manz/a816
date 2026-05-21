"""Bundled assembly modules importable via the `@std/...` prefix.

The contents of this directory (and its sub-directories) are addressable
from assembly source as:

    .import "@std/snes/ppu"
    .import "@std/snes/cpu"

`resolve_stdlib_module` maps that prefix to a path inside this package
so every consumer (codegen, LSP, lint, formatter) routes `@std/...`
the same way. Anything dropped under `a816/stdlib/.../foo.s` becomes
importable as `@std/.../foo` once it ships in the wheel.
"""

from __future__ import annotations

from pathlib import Path

STDLIB_PREFIX = "@std/"


def stdlib_root() -> Path:
    """Filesystem directory holding bundled stdlib modules.

    Resolves to `<wheel>/a816/stdlib`. Computed off this package's
    `__file__` so it works whether the package was installed normally
    or executed from a source checkout.
    """
    return Path(__file__).resolve().parent


def is_stdlib_module(module_name: str) -> bool:
    """True when `module_name` starts with the `@std/` virtual prefix."""
    return module_name.startswith(STDLIB_PREFIX)


def resolve_stdlib_module(module_name: str, extension: str = ".s") -> Path | None:
    """Map `@std/snes/ppu` → `<wheel>/a816/stdlib/snes/ppu.s` (or `.o`).

    Returns `None` for non-`@std/` module names and for `@std/` names
    that don't resolve to a real file (so callers can fall back to
    user-configured paths or report an error themselves).
    """
    if not is_stdlib_module(module_name):
        return None
    relative = module_name[len(STDLIB_PREFIX) :]
    candidate = stdlib_root() / f"{relative}{extension}"
    return candidate if candidate.exists() else None
