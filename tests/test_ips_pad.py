"""IPS output pads the patched ROM to `rom_size`.

ff4 (rom_size 0x200000): the IPS applied to the 1 MiB base gave 0x18A3E7
bytes, and the Analogue Pocket SNES core read `size & 0x200` as a copier
header, skipped 512 bytes and broke the game.
"""

from __future__ import annotations

import io
from pathlib import Path

from a816.module_builder import build_with_imports
from a816.writers import IPSWriter


def _records(patch: bytes) -> list[tuple[int, bytes]]:
    out, at = [], 5
    while patch[at : at + 3] != b"EOF":
        offset, size = int.from_bytes(patch[at : at + 3], "big"), int.from_bytes(patch[at + 3 : at + 5], "big")
        out.append((offset, patch[at + 5 : at + 5 + size]))
        at += 5 + size
    return out


def _apply(patch: bytes, base: bytes) -> bytes:
    rom = bytearray(base)
    for offset, data in _records(patch):
        rom += bytes(max(0, offset + len(data) - len(rom)))
        rom[offset : offset + len(data)] = data
    return bytes(rom)


def _patch(blocks: list[tuple[int, bytes]], pad_to: int = 0, copier_header: bool = False) -> bytes:
    buffer = io.BytesIO()
    writer = IPSWriter(buffer, copier_header, pad_to=pad_to)
    writer.begin()
    for address, data in blocks:
        writer.write_block(data, address)
    writer.end()
    return buffer.getvalue()


def test_the_patched_rom_is_rom_size_long() -> None:
    patch = _patch([(0x10, b"\x01")], pad_to=0x200)
    assert _apply(patch, bytes(0x100)) == bytes(0x10) + b"\x01" + bytes(0x200 - 0x11)


def test_the_pad_is_one_zero_byte_at_the_last_offset() -> None:
    assert _records(_patch([(0x10, b"\x01")], pad_to=0x200))[-1] == (0x1FF, b"\x00")


def test_no_pad_when_a_block_reaches_the_end() -> None:
    assert _records(_patch([(0x1F0, b"\x01" * 0x10)], pad_to=0x200)) == [(0x1F0, b"\x01" * 0x10)]


def test_no_pad_without_rom_size() -> None:
    assert _records(_patch([(0x10, b"\x01")])) == [(0x10, b"\x01")]


def test_the_pad_shifts_with_a_copier_header() -> None:
    assert _records(_patch([(0x10, b"\x01")], pad_to=0x200, copier_header=True))[-1] == (0x3FF, b"\x00")


def test_a_build_with_rom_size_pads(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text(
        'rom_size = 0x100000\n[map.1]\naddress = "00-7d,80-ff:8000-ffff"\nmask = 0x8000\n', encoding="utf-8"
    )
    (tmp_path / "main.s").write_text('"""M."""\n.alloc at 0x008000 {\n    rts\n}\n', encoding="utf-8")
    result = build_with_imports(tmp_path / "main.s", tmp_path / "out.ips", output_dir=tmp_path / "obj")
    assert result.exit_code == 0, result.diagnostics
    assert len(_apply((tmp_path / "out.ips").read_bytes(), bytes(0x80000))) == 0x100000
