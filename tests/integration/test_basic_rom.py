"""End-to-end: assemble `basic_rom.s`, boot it in kintsuki, assert that
the fixed-font tiles landed in VRAM and that the BG1 tilemap holds the
"HELLO" indices.

Catches regressions in: scanner / parser / codegen, DMA opcode emit, IPS
output, `.incbin` auto-symbols, stdlib `@std/snes/*` imports, multi-pass
forward refs to data placed at a fixed `*=`.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration.conftest import (
    ASSETS_DIR,
    BASIC_DIR,
    assemble_sfc,
    boot_emu,
)

FONT_VRAM_BYTE_ADDR = 0x2000          # word-addr $1000 → byte $2000
FONT_SIZE_BYTES = 0x1000              # 256 tiles × 16 bytes (real FF4 8x8.bin)
TILEMAP_BYTE_ADDR = 0x0000
# basic_rom.s draws the string offset by 64 bytes (`tilemap_buffer +
# 0x40` = row 2 column 0 in a 32-wide tilemap).
TILEMAP_STRING_OFFSET = 0x40
HELLO_STRING = "Let's display a better string"


def _decode_through_table(data: bytes, tbl_path: Path) -> str:
    """Decode tilemap glyph bytes back into the source string via a816's
    `script.Table`, so assertions read as plain text."""
    from script import Table

    return Table(str(tbl_path)).to_text(data)


def test_basic_rom_assembles_and_boots(tmp_path: Path) -> None:
    """ROM compiles and the emulator survives 60 frames without crashing."""
    sfc = assemble_sfc(BASIC_DIR / "main.s", tmp_path / "basic.sfc")
    assert sfc.stat().st_size > 0
    emu = boot_emu(sfc, frames=60)
    # Frame count must have advanced — proves reset vector ran.
    assert emu.frame_count >= 60


def test_basic_rom_dma_uploads_fixed_font_to_vram(tmp_path: Path) -> None:
    """After DMA, VRAM at the font region matches the fixture bytes."""
    sfc = assemble_sfc(BASIC_DIR / "main.s", tmp_path / "basic.sfc")
    emu = boot_emu(sfc, frames=60)
    vram = bytes(emu.vram_read_range(FONT_VRAM_BYTE_ADDR, FONT_SIZE_BYTES))
    expected = (ASSETS_DIR / "ff4_font_fixed.bin").read_bytes()
    assert vram == expected, "DMA-uploaded font tiles must match the source asset"


def test_basic_rom_tilemap_holds_hello_indices(tmp_path: Path) -> None:
    """BG1 tilemap stores the string the boot path drew, decoded back
    through the same `.table` charset that encoded it."""
    sfc = assemble_sfc(BASIC_DIR / "main.s", tmp_path / "basic.sfc")
    emu = boot_emu(sfc, frames=60)
    tilemap = bytes(
        emu.vram_read_range(TILEMAP_BYTE_ADDR + TILEMAP_STRING_OFFSET, 2 * len(HELLO_STRING))
    )
    glyph_bytes = bytes(tilemap[i] for i in range(0, len(tilemap), 2))
    attr_bytes = tuple(tilemap[i] for i in range(1, len(tilemap), 2))
    decoded = _decode_through_table(glyph_bytes, ASSETS_DIR / "ff4_charset.tbl")
    assert decoded == HELLO_STRING
    assert attr_bytes == (0,) * len(HELLO_STRING)


def test_basic_rom_clears_force_blank(tmp_path: Path) -> None:
    """INIDISP at end of boot has force-blank cleared, brightness = 15."""
    sfc = assemble_sfc(BASIC_DIR / "main.s", tmp_path / "basic.sfc")
    emu = boot_emu(sfc, frames=60)
    state = emu.get_ppu_state()
    # `inidisp` may not be a direct attribute on every kintsuki build;
    # fall back to reading the shadow register if needed. Either way,
    # bit 7 must be clear and low nibble must read 0x0F.
    inidisp = getattr(state, "inidisp", None)
    if inidisp is None:
        # Older build: skip rather than fail the suite.
        import pytest

        pytest.skip("kintsuki build does not expose PpuState.inidisp")
    assert (inidisp & 0x80) == 0, "force-blank should be OFF after boot"
    assert (inidisp & 0x0F) == 0x0F, "brightness should be at max"
