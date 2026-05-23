"""Shared fixtures + helpers for the integration suite.

Reuses `Program` to drive assembly end-to-end (same code path as the
CLI), then loads the artefact into `kintsuki.Emu` for state assertions.
Helpers favour pre-built byte-level inputs over hand-rolled emulator
control so tests stay readable.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from kintsuki import Emu  # type: ignore[import-untyped]

from a816.module_builder import build_with_imports

INTEGRATION_DIR = Path(__file__).parent
ASSETS_DIR = INTEGRATION_DIR / "assets"
BASIC_DIR = INTEGRATION_DIR / "basic"


@pytest.fixture
def assets_dir() -> Path:
    return ASSETS_DIR


@pytest.fixture
def basic_dir() -> Path:
    return BASIC_DIR


def _run_in_source_cwd[T](source: Path, fn: Callable[[], T]) -> T:
    """`.incbin` / `.table` paths in source modules resolve against
    cwd (those directives don't honour `include_paths` yet). Match
    the CLI invocation: cwd = source directory."""
    import os

    prev = Path.cwd()
    os.chdir(source.parent)
    try:
        return fn()
    finally:
        os.chdir(prev)


def _build_via_module_pipeline(source: Path, out: Path, output_format: str) -> Path:
    """Build `source` through the same `build_with_imports` pipeline the
    `a816` CLI uses (per-module `.o` compile + cross-module link). This
    is what end users actually run; the direct-mode `Program.assemble`
    path is a different code path that we don't ship by default."""

    def _do() -> int:
        result = build_with_imports(
            main_source=source,
            output_file=out,
            output_format=output_format,
            module_paths=[source.parent],
            include_paths=[INTEGRATION_DIR, source.parent],
            output_dir=source.parent / "build" / "obj",
            overlap_mode="error",
        )
        return result.exit_code

    rc = _run_in_source_cwd(source, _do)
    assert rc == 0, f"build_with_imports({source.name}) returned {rc}"
    return out


def assemble_sfc(source: Path, out: Path) -> Path:
    """Build `source` into an SFC file at `out`. Returns `out` on success."""
    return _build_via_module_pipeline(source, out, "sfc")


def assemble_ips(source: Path, out: Path) -> Path:
    """Build `source` into an IPS patch at `out`."""
    return _build_via_module_pipeline(source, out, "ips")


def apply_ips_to_sfc(base_sfc: Path, ips: Path, out: Path) -> Path:
    """Apply an IPS patch to `base_sfc`, write result to `out`.

    IPS format: "PATCH" header, then records of (24-bit offset, 16-bit
    length, length bytes) for plain records or (offset, 0, 16-bit rle
    length, 1 byte) for RLE, terminated by "EOF".
    """
    payload = base_sfc.read_bytes()
    rom = bytearray(payload)
    data = ips.read_bytes()
    assert data[:5] == b"PATCH", "missing IPS header"
    pos = 5
    while True:
        if data[pos : pos + 3] == b"EOF":
            break
        offset = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
        pos += 3
        length = (data[pos] << 8) | data[pos + 1]
        pos += 2
        if length == 0:
            rle_length = (data[pos] << 8) | data[pos + 1]
            byte_value = data[pos + 2]
            pos += 3
            _splice(rom, offset, bytes([byte_value]) * rle_length)
        else:
            _splice(rom, offset, data[pos : pos + length])
            pos += length
    out.write_bytes(bytes(rom))
    return out


def _splice(rom: bytearray, offset: int, blob: bytes) -> None:
    """Overwrite/extend the ROM with `blob` at `offset`, padding with 0x00
    when the patch writes past the current end-of-file."""
    end = offset + len(blob)
    if end > len(rom):
        rom.extend(b"\x00" * (end - len(rom)))
    rom[offset:end] = blob


def boot_emu(sfc: Path, *, frames: int = 60) -> Emu:
    """Load `sfc` into kintsuki, settle for `frames` frames, return Emu."""
    emu = Emu()
    emu.load_rom(str(sfc))
    emu.run_frames(frames)
    return emu


def pack_word(value: int) -> bytes:
    """Little-endian 16-bit pack — handy when building expected VRAM bytes."""
    return struct.pack("<H", value)
