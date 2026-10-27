"""Bundled `@std/...` modules resolve through the import machinery and
produce the expected SNES register offsets.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from a816.program import Program
from tests import link_source


def _resolve_symbols(src: str) -> dict[str, int]:
    """The integer symbols `src` defines once compiled to an object: its
    compile-time `.import`s inline their structs and constants."""
    program = Program()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "main.s").write_text(src)
        assert program.assemble_as_object(str(tmp / "main.s"), tmp / "main.o") == 0
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
    linked = link_source(src)
    assert linked.exit_code == 0
    # Base 0x2100 + OAMDATA offset 0x04 = $2104 → LDA $2104 → AD 04 21
    assert linked.data == [b"\xad\x04\x21"]


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


def test_unknown_stdlib_module_falls_through_to_user_paths(caplog: pytest.LogCaptureFixture) -> None:
    """`@std/...` that doesn't exist in the bundle should error like any
    other missing module rather than silently dropping the import."""
    with caplog.at_level(logging.ERROR, logger="a816"):
        linked = link_source('.import "@std/snes/nonexistent"\n')
    assert linked.exit_code != 0
    assert "Module not found" in caplog.text


@pytest.mark.parametrize(
    ("name", "offset"),
    [
        ("SnesHeader.maker_code", 0x00),
        ("SnesHeader.game_code", 0x02),
        ("SnesHeader.reserved", 0x06),
        ("SnesHeader.expansion_flash_size", 0x0C),
        ("SnesHeader.expansion_ram_size", 0x0D),
        ("SnesHeader.special_version", 0x0E),
        ("SnesHeader.cartridge_subtype", 0x0F),
        ("SnesHeader.title", 0x10),
        ("SnesHeader.title.__size", 21),
        ("SnesHeader.map_mode", 0x25),
        ("SnesHeader.cartridge_type", 0x26),
        ("SnesHeader.rom_size", 0x27),
        ("SnesHeader.sram_size", 0x28),
        ("SnesHeader.destination", 0x29),
        ("SnesHeader.old_maker_code", 0x2A),
        ("SnesHeader.version", 0x2B),
        ("SnesHeader.checksum_complement", 0x2C),
        ("SnesHeader.checksum", 0x2E),
        ("SnesHeader.__size", 0x30),
        ("SnesNativeVectors.coprocessor", 0x04),
        ("SnesNativeVectors.break", 0x06),
        ("SnesNativeVectors.abort", 0x08),
        ("SnesNativeVectors.nmi", 0x0A),
        ("SnesNativeVectors.irq", 0x0E),
        ("SnesEmulationVectors.coprocessor", 0x04),
        ("SnesEmulationVectors.abort", 0x08),
        ("SnesEmulationVectors.nmi", 0x0A),
        ("SnesEmulationVectors.reset", 0x0C),
        ("SnesEmulationVectors.irq_brk", 0x0E),
        ("SnesVectors.native.nmi", 0x0A),
        ("SnesVectors.emulation.reset", 0x1C),
        ("SnesVectors.__size", 0x20),
    ],
)
def test_snes_header_layout(name: str, offset: int) -> None:
    assert _resolve_symbols('.import "@std/snes/header"\n')[name] == offset


@pytest.mark.parametrize(
    ("expr", "address"),
    [
        ("SNES_HEADER_BASE + SnesHeader.title", 0xFFC0),
        ("SNES_HEADER_BASE + SnesHeader.map_mode", 0xFFD5),
        ("SNES_HEADER_BASE + SnesHeader.checksum", 0xFFDE),
        ("SNES_VECTORS_BASE", 0xFFE0),
        ("SNES_VECTORS_BASE + SnesVectors.emulation.reset", 0xFFFC),
    ],
)
def test_snes_header_lands_at_hardware_addresses(expr: str, address: int) -> None:
    assert _resolve_symbols(f'.import "@std/snes/header"\nprobe = {expr}\n')["probe"] == address


def test_snes_header_istruct_emits_48_bytes() -> None:
    src = (
        '.import "@std/snes/header"\n*=0x008000\n'
        '.istruct SnesHeader { title = "A816", map_mode = 0x20, old_maker_code = 0x33 }\n'
    )
    linked = link_source(src)
    assert linked.exit_code == 0
    data = b"".join(linked.data)
    assert data == bytes(16) + b"A816" + bytes(17) + b"\x20" + bytes(4) + b"\x33" + bytes(5)


@pytest.mark.parametrize(
    ("expr", "address"),
    [
        ("SDD1_BASE + SDD1.DMA_ENABLE", 0x4800),
        ("SDD1_BASE + SDD1.DMA_TRIGGER", 0x4801),
        ("SDD1_BASE + SDD1.BANK_C0", 0x4804),
        ("SDD1_BASE + SDD1.BANK_F0", 0x4807),
    ],
)
def test_sdd1_registers(expr: str, address: int) -> None:
    assert _resolve_symbols(f'.import "@std/snes/sdd1"\nprobe = {expr}\n')["probe"] == address


@pytest.mark.parametrize(
    ("expr", "address"),
    [
        ("SPC7110_DCU_BASE + SPC7110_DCU.DATA", 0x4800),
        ("SPC7110_DCU_BASE + SPC7110_DCU.INDEX", 0x4804),
        ("SPC7110_DCU_BASE + SPC7110_DCU.OFFSET_H", 0x4806),
        ("SPC7110_DCU_BASE + SPC7110_DCU.COUNT_L", 0x4809),
        ("SPC7110_DCU_BASE + SPC7110_DCU.MODE", 0x480B),
        ("SPC7110_DCU_BASE + SPC7110_DCU.STATUS", 0x480C),
        ("SPC7110_DATA_BASE + SPC7110_DATA.DATA", 0x4810),
        ("SPC7110_DATA_BASE + SPC7110_DATA.OFFSET_B", 0x4813),
        ("SPC7110_DATA_BASE + SPC7110_DATA.MODE", 0x4818),
        ("SPC7110_DATA_BASE + SPC7110_DATA.ADJUST_STROBE", 0x481A),
        ("SPC7110_ALU_BASE + SPC7110_ALU.MUL_H", 0x4825),
        ("SPC7110_ALU_BASE + SPC7110_ALU.DIV_H", 0x4827),
        ("SPC7110_ALU_BASE + SPC7110_ALU.REMAINDER_L", 0x482C),
        ("SPC7110_ALU_BASE + SPC7110_ALU.STATUS", 0x482F),
        ("SPC7110_MCU_BASE + SPC7110_MCU.CONTROL", 0x4830),
        ("SPC7110_MCU_BASE + SPC7110_MCU.BANK_F0", 0x4833),
        ("SPC7110_MCU_BASE + SPC7110_MCU.ROM_SIZE", 0x4834),
        ("EPSON_RTC_BASE + EPSON_RTC.CHIP_SELECT", 0x4840),
        ("EPSON_RTC_BASE + EPSON_RTC.STATUS", 0x4842),
    ],
)
def test_spc7110_registers(expr: str, address: int) -> None:
    assert _resolve_symbols(f'.import "@std/snes/spc7110"\nprobe = {expr}\n')["probe"] == address


def _emit(module: str, body: str) -> str:
    linked = link_source(f'.import "@std/snes/{module}"\n*=0x008000\n{body}')
    assert linked.exit_code == 0
    return b"".join(linked.data).hex(" ")


@pytest.mark.parametrize(
    ("module", "call", "code"),
    [
        ("sdd1", "sdd1_decompress(0x03)", "a9 03 8f 00 48 00 8f 01 48 00 8f 0b 42 00 a9 00 8f 00 48 00"),
        ("sdd1", "sdd1_banks(4, 5, 6, 7)", "a9 04 8f 04 48 00 a9 05 8f 05 48 00 a9 06 8f 06 48 00 a9 07 8f 07 48 00"),
        (
            "spc7110",
            "spc7110_dcu_start(0x123456, 9, 0x0201)",
            "a9 56 8f 01 48 00 a9 34 8f 02 48 00 a9 12 8f 03 48 00 a9 09 8f 04 48 00 a9 01 8f 05 48 00 a9 02 8f 06 48 00",
        ),
        ("spc7110", "spc7110_dcu_wait()", "af 0c 48 00 10 fa"),
        ("spc7110", "spc7110_data_seek(0x0A0B0C)", "a9 0c 8f 11 48 00 a9 0b 8f 12 48 00 a9 0a 8f 13 48 00"),
        ("spc7110", "spc7110_banks(1, 2, 3)", "a9 01 8f 31 48 00 a9 02 8f 32 48 00 a9 03 8f 33 48 00"),
    ],
)
def test_coprocessor_helpers_emit(module: str, call: str, code: str) -> None:
    assert _emit(module, f"    {call}\n") == code


def test_spc7110_dcu_wait_expands_twice() -> None:
    assert _emit("spc7110", "    spc7110_dcu_wait()\n    spc7110_dcu_wait()\n") == "af 0c 48 00 10 fa af 0c 48 00 10 fa"
