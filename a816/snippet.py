"""Assemble a source string to bytes, as `a816 build` would.

For tools and tests that need the bytes of a few lines of assembly (a stub
to drop in an emulator's RAM, a patch fragment to compare) without a
project on disk: the source compiles to an object and links, the same path
every build takes.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from a816.exceptions import A816Error, AssemblyError
from a816.section import ANONYMOUS_ALLOC_PREFIX


@dataclass(frozen=True)
class Assembled:
    """What `assemble_source` built."""

    blocks: tuple[tuple[int, bytes], ...] = ()
    """Each placed run of bytes at its SNES address (`*= 0x7E0000` puts
    one at 0x7E0000), in address order."""
    symbols: dict[str, int] = field(default_factory=dict)
    """Every linked label's address."""

    @property
    def code(self) -> bytes:
        """All the blocks' bytes, back to back."""
        return b"".join(block for _, block in self.blocks)


def assemble_source(
    source: str,
    *,
    files: Mapping[str, str] | None = None,
    symbols: Mapping[str, int | str] | None = None,
    include_paths: list[Path] | None = None,
    experimental: list[str] | None = None,
) -> Assembled:
    """Assemble `source` through compile and link. The source places its
    own code (`*=`, `.alloc`), as any module of a build does.

    Args:
        source: the main translation unit's text, named `main.s` in diagnostics.
        files: other sources by path relative to the main one, for its
            `.import` and `.include`.
        symbols: names bound before the source compiles, as `-D` binds them.
        include_paths: extra directories for `.include` and `.incbin`.
        experimental: experimental features to turn on (`--experimental`).

    Raises:
        AssemblyError: the build failed; its message holds the diagnostics.
    """
    from a816.module_builder import ModuleBuilder, ModuleCompileError
    from a816.program import Program

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        for name, content in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        builder = ModuleBuilder(
            module_paths=[root],
            output_dir=root / "obj",
            symbols=dict(symbols) if symbols is not None else None,
            include_paths=include_paths,
            experimental=experimental,
            use_cache=False,
        )
        program = Program()
        try:
            blocks = program.placed_blocks(builder.build(root / "main.s", main_text=source))
        except ModuleCompileError as e:
            raise AssemblyError(_relative_to(root, "\n".join(e.diagnostics) or str(e))) from e
        except A816Error as e:
            raise AssemblyError(_relative_to(root, e.format() if hasattr(e, "format") else str(e))) from e
    labels = dict(program.resolver.get_all_labels())
    labels.update(program.resolver.get_all_absolute_labels())
    # An unnamed `.alloc at` gets an internal name; it is no label of the source.
    named = {name: address for name, address in labels.items() if not name.startswith(ANONYMOUS_ALLOC_PREFIX)}
    return Assembled(tuple(blocks), named)


def _relative_to(root: Path, message: str) -> str:
    """`message` with the temporary directory's paths made relative (`main.s:3`), so it reads the same every run."""
    for prefix in {str(root), str(root.resolve())}:
        message = message.replace(prefix + os.sep, "")
    return message
