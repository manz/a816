"""A floating `.alloc` isn't bus-checked at its provisional compile-time address.

Object mode lays a pool's floating allocs end to end from the first range's
start, a sandbox the linker rebases. ff4 (rc1): in a LoROM pool spanning
banks $2F and $30, the fourth alloc's sandbox start ran past $2F:FFFF to
$30:0260, outside the $8000-$FFFF window, and the compile failed with E0317
although every alloc fits a bank once placed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

TOML = 'entrypoint = "m.s"\nrom_size = 0x200000\n[map.1]\naddress = "00-6f,80-cf:8000-ffff"\nmask = 0x8000\n'
POOL = ".pool baked_text {\n    range 0x2F8000 0x2FFFFF\n    range 0x308000 0x30FFFF\n    strategy pack\n}\n"
BLOBS = {"spells.bin": 4208, "spells.tbl": 288, "items.bin": 28880, "items.tbl": 1024}


def _build(tmp_path: Path, source: str) -> int:
    (tmp_path / "a816.toml").write_text(TOML, encoding="utf-8")
    for name, size in BLOBS.items():
        (tmp_path / name).write_bytes(bytes([size % 251]) * size)
    (tmp_path / "m.s").write_text(source, encoding="utf-8")
    result = build_with_imports(
        tmp_path / "m.s",
        tmp_path / "o.sfc",
        include_paths=[tmp_path],
        output_dir=tmp_path / "obj",
        output_format="sfc",
    )
    return result.exit_code


def _floating() -> str:
    allocs = "".join(f'.alloc {name.replace(".", "_")} in baked_text {{\n    .incbin "{name}"\n}}\n' for name in BLOBS)
    return POOL + allocs


def test_floating_allocs_past_the_first_bank_build(tmp_path: Path) -> None:
    assert _build(tmp_path, _floating()) == 0


def test_each_blob_lands_inside_the_rom_window(tmp_path: Path) -> None:
    """LoROM: bank $2F starts at file offset $178000, bank $30 at $180000."""
    _build(tmp_path, _floating())
    rom = (tmp_path / "o.sfc").read_bytes()

    assert rom[0x178000 : 0x178000 + 28880] == bytes([28880 % 251]) * 28880


def test_a_pinned_alloc_outside_the_map_is_still_an_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A pinned address is real: outside the bus it must still fail."""
    source = '.alloc at 0x300260 {\n    .incbin "items.tbl"\n}\n'

    assert _build(tmp_path, source) != 0


CODE = "\n.alloc routine in baked_text {\nloop:\n    dex\n    bne loop\n    jmp.l loop\n}\n"


def test_code_past_the_first_bank_assembles_to_its_placed_address(tmp_path: Path) -> None:
    """The routine's sandbox base is off the bus; its branch and long jump
    must still come out right once the linker places it."""
    assert _build(tmp_path, _floating() + CODE) == 0
    symbols = build_with_imports(
        tmp_path / "m.s",
        tmp_path / "o.sfc",
        include_paths=[tmp_path],
        output_dir=tmp_path / "obj",
        output_format="sfc",
    ).symbol_map
    loop = symbols["routine"]
    offset = ((loop >> 16) & 0x7F) * 0x8000 + (loop & 0x7FFF)
    rom = (tmp_path / "o.sfc").read_bytes()

    assert rom[offset : offset + 7] == bytes([0xCA, 0xD0, 0xFD, 0x5C, loop & 0xFF, (loop >> 8) & 0xFF, loop >> 16])
