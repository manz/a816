"""Bundled assembly modules importable via the `@std/...` prefix.

The contents of this directory (and its sub-directories) are addressable
from assembly source as:

    .import "@std/snes/ppu"
    .import "@std/snes/cpu"

The resolver maps that prefix to this package's filesystem path (see
`a816.parse.codegen._resolve_stdlib_module`). Anything dropped under
`a816/stdlib/.../foo.s` becomes importable as `@std/.../foo` once it
ships in the wheel.
"""
