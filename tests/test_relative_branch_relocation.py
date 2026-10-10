"""A relative branch whose target moves at link gets its offset at link.

The offset was computed at compile, from provisional addresses, and nothing
corrected it once the linker placed the sections (found probing BL's rc3
`mvn` bug):
- to an `.extern` label, the target read as 0: `bra target` emitted `FE`,
  a branch to itself, and `brl` / `per` emitted `FFFD`;
- between two floating allocs of one module that the pool placed in another
  order than the source, every branch landed off by the reorder.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

LIB = ".alloc lib at 0x008123 {\ntarget:\n    rts\n}\n"


def _build(tmp_path: Path, files: dict[str, str | bytes]) -> tuple[int, dict[str, int], bytes]:
    for name, content in files.items():
        (tmp_path / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    result = build_with_imports(
        tmp_path / "main.s",
        tmp_path / "out.sfc",
        module_paths=[tmp_path],
        include_paths=[tmp_path],
        output_dir=tmp_path / "obj",
        output_format="sfc",
    )
    rom = (tmp_path / "out.sfc").read_bytes() if result.exit_code == 0 else b""
    return result.exit_code, dict(result.symbol_map), rom


def _lands(rom: bytes, at: int, size: int) -> int:
    delta = int.from_bytes(rom[(at & 0x7FFF) + 1 : (at & 0x7FFF) + size], "little", signed=True)
    return at + size + delta


@pytest.mark.parametrize(("body", "size"), [("brl target", 3), ("per target", 3)])
def test_a_long_branch_to_an_extern_lands_on_it(tmp_path: Path, body: str, size: int) -> None:
    main = f'.import "lib"\n.extern target\n.alloc code at 0x008000 {{\n    {body}\n}}\n'
    code, symbols, rom = _build(tmp_path, {"lib.s": LIB, "main.s": main})

    assert (code, _lands(rom, 0x008000, size)) == (0, symbols["target"])


def test_a_short_branch_to_an_extern_in_range_lands_on_it(tmp_path: Path) -> None:
    lib = ".alloc lib at 0x008040 {\ntarget:\n    rts\n}\n"
    main = '.import "lib"\n.extern target\n.alloc code at 0x008000 {\n    bra target\n}\n'
    code, symbols, rom = _build(tmp_path, {"lib.s": lib, "main.s": main})

    assert (code, _lands(rom, 0x008000, 2)) == (0, symbols["target"])


def test_a_short_branch_to_an_extern_out_of_range_fails_at_link(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`$8123` is 0x121 bytes away: it emitted `FE`, a branch to itself."""
    main = '.import "lib"\n.extern target\n.alloc code at 0x008000 {\n    bra target\n}\n'
    with caplog.at_level(logging.ERROR):
        code, _, _ = _build(tmp_path, {"lib.s": LIB, "main.s": main})

    assert code != 0
    assert "signed 8-bit" in caplog.text


REORDERED = (
    ".pool p {{ range 0x008000 0x00ffff  strategy pack }}\n"
    ".alloc first in p {{\n    {body}\n}}\n"
    '.alloc second in p {{\n    .incbin "big.bin"\nother:\n    rts\n}}\n'
)


@pytest.mark.parametrize(("body", "size"), [("bra other", 2), ("brl other", 3), ("per other", 3)])
def test_a_branch_between_reordered_floating_allocs_lands_on_its_target(tmp_path: Path, body: str, size: int) -> None:
    """`pack` places `second` (bigger) first; the compile-time offset assumed source order."""
    files: dict[str, str | bytes] = {"big.bin": b"\xbb" * 0x40, "main.s": REORDERED.format(body=body)}
    code, symbols, rom = _build(tmp_path, files)

    assert (code, _lands(rom, symbols["first"], size)) == (0, symbols["other"])


def test_a_branch_inside_one_alloc_is_unchanged(tmp_path: Path) -> None:
    main = ".alloc code at 0x008000 {\nloop:\n    dex\n    bne loop\n    brl loop\n}\n"
    code, _, rom = _build(tmp_path, {"main.s": main})

    assert (code, rom[:6]) == (0, bytes([0xCA, 0xD0, 0xFD, 0x82, 0xFA, 0xFF]))
