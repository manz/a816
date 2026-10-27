"""Assemble a source string to bytes, as `a816 build` would.

For tools and tests that need the bytes of a few lines of assembly (a stub
to drop in an emulator's RAM, a patch fragment to compare) without a
project on disk: the source compiles to an object and links, the same path
every build takes.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from a816.exceptions import AssemblyError


@dataclass(frozen=True)
class Assembled:
    """What `assemble_source` built."""

    blocks: list[tuple[int, bytes]] = field(default_factory=list)
    """Each run of bytes at its ROM file offset, in patch order."""
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
        source: the main translation unit's text.
        files: other sources by path relative to the main one, for its
            `.import` and `.include`.
        symbols: names bound before the source compiles, as `-D` binds them.
        include_paths: extra directories for `.include` and `.incbin`.
        experimental: experimental features to turn on (`--experimental`).

    Raises:
        AssemblyError: the build failed; its message holds the diagnostics.
    """
    from a816 import ips
    from a816.module_builder import build_with_imports

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        main = root / "main.s"
        main.write_text(source, encoding="utf-8")
        for name, content in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        out = root / "out.ips"
        result = build_with_imports(
            main,
            out,
            output_dir=root / "obj",
            include_paths=include_paths,
            symbols=dict(symbols) if symbols is not None else None,
            experimental=experimental,
            use_a816_toml=False,
            use_cache=False,
        )
        if result.exit_code != 0:
            raise AssemblyError("\n".join(result.diagnostics) or f"build failed with exit code {result.exit_code}")
        records = ips.parse(out.read_bytes()).records
    return Assembled([(record.offset, record.data) for record in records], dict(result.symbol_map))
