"""Bundled `@std/...` modules resolve through the import machinery and
produce the expected SNES register offsets.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.context import AssemblyMode
from a816.program import Program


def _resolve_symbols(src: str) -> dict[str, int]:
    program = Program()
    # `.import` only inlines source under DIRECT mode; the default
    # (parse-only) path produces ExternNodes which intentionally don't
    # publish the struct symbols.
    program.resolver.context.mode = AssemblyMode.DIRECT
    program.assemble_string_with_emitter(src, "main.s", _NoopEmitter())
    return {
        name: value
        for scope in program.resolver.scopes
        for name, value in scope.symbols.items()
        if isinstance(value, int)
    }


def test_ppu_struct_loads_canonical_offsets() -> None:
    symbols = _resolve_symbols('.import "@std/snes/ppu"\n')
    assert symbols["PPU.INIDISP"] == 0x00
    assert symbols["PPU.OAMADDL"] == 0x02
    assert symbols["PPU.OAMDATA"] == 0x04
    assert symbols["PPU.VMADDL"] == 0x16
    assert symbols["PPU.VMDATAL"] == 0x18
    assert symbols["PPU.CGADD"] == 0x21
    assert symbols["PPU.STAT78"] == 0x3F
    assert symbols["PPU.__size"] == 0x40


def test_cpu_struct_loads_canonical_offsets() -> None:
    symbols = _resolve_symbols('.import "@std/snes/cpu"\n')
    assert symbols["CPU_REGS.NMITIMEN"] == 0x00
    assert symbols["CPU_REGS.MDMAEN"] == 0x0B
    assert symbols["CPU_REGS.HDMAEN"] == 0x0C
    assert symbols["CPU_REGS.RDNMI"] == 0x10
    assert symbols["CPU_REGS.HVBJOY"] == 0x12
    assert symbols["CPU_REGS.PAD1L"] == 0x18
    assert symbols["CPU_REGS.PAD4H"] == 0x1F


def test_dma_channel_struct_lays_out_16_bytes() -> None:
    symbols = _resolve_symbols('.import "@std/snes/dma"\n')
    assert symbols["DMAChannel.DMAP"] == 0
    assert symbols["DMAChannel.BBAD"] == 1
    assert symbols["DMAChannel.A1TL"] == 2
    assert symbols["DMAChannel.NTRL"] == 0x0A
    assert symbols["DMAChannel.__size"] == 0x10


def test_apu_struct_is_four_ports() -> None:
    symbols = _resolve_symbols('.import "@std/snes/apu"\n')
    assert symbols["APU.APUIO0"] == 0
    assert symbols["APU.APUIO3"] == 3
    assert symbols["APU.__size"] == 4


def test_wram_struct() -> None:
    symbols = _resolve_symbols('.import "@std/snes/wram"\n')
    assert symbols["WRAM.WMDATA"] == 0
    assert symbols["WRAM.WMADDB"] == 3
    assert symbols["WRAM.__size"] == 4


def test_joypad_struct() -> None:
    symbols = _resolve_symbols('.import "@std/snes/joypad"\n')
    assert symbols["JOYPAD_SERIAL.JOYSER0"] == 0
    assert symbols["JOYPAD_SERIAL.JOYSER1"] == 1


def test_typed_bind_against_ppu_emits_lda_w() -> None:
    """End-to-end: import + typed bind + auto-sized opcode emit."""
    src = """
.import "@std/snes/ppu"
.a8
*=0x008000
ppu := (PPU_BASE as PPU)
    lda ppu.OAMDATA
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asm = root / "main.s"
        asm.write_text(src, encoding="utf-8")
        ips = root / "out.ips"
        program = Program()
        assert program.assemble_as_patch(str(asm), ips) == 0
        data = ips.read_bytes()
        # Base 0x2100 + OAMDATA offset 0x04 = $2104 → LDA $2104 → AD 04 21
        assert b"\xad\x04\x21" in data


def test_ppu_inidisp_bitfield_struct() -> None:
    symbols = _resolve_symbols('.import "@std/snes/ppu"\n')
    assert symbols["INIDISP.__size"] == 1
    assert symbols["INIDISP.brightness.mask"] == 0x0F
    assert symbols["INIDISP.brightness.shift"] == 0
    assert symbols["INIDISP.force_blank.mask"] == 0x80
    assert symbols["INIDISP.force_blank.shift"] == 7


def test_cpu_nmitimen_bitfield_struct() -> None:
    symbols = _resolve_symbols('.import "@std/snes/cpu"\n')
    assert symbols["NMITIMEN.joypad_enable.mask"] == 0x01
    assert symbols["NMITIMEN.h_irq_enable.mask"] == 0x10
    assert symbols["NMITIMEN.v_irq_enable.mask"] == 0x20
    assert symbols["NMITIMEN.nmi_enable.mask"] == 0x80
    assert symbols["NMITIMEN.__size"] == 1


def test_dma_dmap_bitfield_struct() -> None:
    symbols = _resolve_symbols('.import "@std/snes/dma"\n')
    assert symbols["DMAP.transfer_pattern.mask"] == 0x07
    assert symbols["DMAP.increment.mask"] == 0x18
    assert symbols["DMAP.transfer_direction.mask"] == 0x80
    assert symbols["DMAP.__size"] == 1


def test_byte_and_bitfield_structs_coexist() -> None:
    """Existing `PPU.INIDISP` byte offset still works alongside the new
    `INIDISP` bit-field struct."""
    symbols = _resolve_symbols('.import "@std/snes/ppu"\n')
    assert symbols["PPU.INIDISP"] == 0x00  # byte offset in monolithic struct
    assert symbols["INIDISP.force_blank.mask"] == 0x80  # bit-field absolute mask


def test_unknown_stdlib_module_falls_through_to_user_paths() -> None:
    """`@std/...` that doesn't exist in the bundle should error like any
    other missing module rather than silently dropping the import."""
    import pytest

    from a816.parse.nodes import NodeError

    program = Program()
    with pytest.raises(NodeError, match="Module not found"):
        program.assemble_string_with_emitter('.import "@std/snes/nonexistent"\n', "x.s", _NoopEmitter())


class _NoopEmitter:
    def begin(self) -> None: ...

    def end(self) -> None: ...

    def write_block_header(self, *_: object, **__: object) -> None: ...

    def write_block(self, *_: object, **__: object) -> None: ...
