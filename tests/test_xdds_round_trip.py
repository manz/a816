"""Everything xdds --asm prints reassembles to the same bytes.

Every opcode byte, both M/X widths, edge operand values. xdds printed
`AD 0C 00` as `lda 0x000C`, which a816 sizes by value into direct page
`A5 0C`: one byte short, every later address shifted (Feda VWF recovery).
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import product

import pytest

from a816.cpu.disassembler import OPCODE_TABLE, Disassembler, format_disassembly_block
from a816.program import Program
from tests import StubWriter

ORG = 0x008000
EDGES = {
    1: (0x00, 0x7F, 0xFF),
    2: (0x0000, 0x00FF, 0x0100, 0xFFFF),
    3: (0x000000, 0x0000FF, 0x00FFFF, 0x010000, 0xFFFFFF),
}


def _cases() -> Iterator[tuple[bytes, bool, bool]]:
    for opcode, (_mnemonic, _mode, base_size) in sorted(OPCODE_TABLE.items()):
        for m8, x8 in product((True, False), repeat=2):
            size = Disassembler(m8, x8).get_operand_size(base_size)
            for value in EDGES.get(size, (0,)):
                yield bytes([opcode]) + value.to_bytes(size, "little"), m8, x8


def _round_trip(raw: bytes, m8: bool, x8: bool) -> str | None:
    """The a816 line xdds printed, when it reassembles differently; else None."""
    inst = Disassembler(m8, x8).decode_instruction(raw, ORG)
    assert inst is not None
    line = inst.format_a816()
    writer = StubWriter()
    try:
        Program().assemble_string_with_emitter(f"*= 0x{ORG:06x}\n    {line}\n", "rt.s", writer)
    except Exception as exc:  # noqa: BLE001  (any failure is a broken round trip)
        return f"{raw.hex()} -> {line!r}: {type(exc).__name__}"
    rebuilt = b"".join(writer.data)
    return None if rebuilt == raw else f"{raw.hex()} -> {line!r} -> {rebuilt.hex()}"


def test_every_printed_instruction_reassembles_to_its_bytes() -> None:
    broken = sorted({failure for case in _cases() if (failure := _round_trip(*case))})

    assert broken == []


@pytest.mark.parametrize(
    ("raw", "line"),
    [
        (bytes.fromhex("ad0c00"), "lda.w 0x000C"),
        (bytes.fromhex("ae0c00"), "ldx.w 0x000C"),
        (bytes.fromhex("a50c"), "lda 0x0C"),
    ],
)
def test_an_absolute_operand_that_fits_a_byte_keeps_its_suffix(raw: bytes, line: str) -> None:
    inst = Disassembler().decode_instruction(raw, ORG)
    assert inst is not None

    assert inst.format_a816() == line


def test_a_block_move_prints_source_then_destination() -> None:
    """`44 FF 00` is `mvp` from bank $00 to bank $FF: destination is encoded first."""
    inst = Disassembler().decode_instruction(bytes.fromhex("44ff00"), ORG)
    assert inst is not None

    assert inst.format_a816() == "mvp 0x00,0xFF"


def test_a_labelled_block_outside_bank_zero_reassembles_to_its_bytes() -> None:
    """xdds names targets with 24-bit labels (`jsr _018006`). In a bank above
    $00 a bare `jsr` to one used to reassemble as JSL (Feda, HiROM)."""
    org = 0x018000
    raw = bytes.fromhex("2006804c06806060")  # jsr $8006 / jmp $8006 / rts / target: rts
    lines = format_disassembly_block(Disassembler().disassemble(raw, org), show_bytes=False, a816_syntax=True)
    writer = StubWriter()

    Program().assemble_string_with_emitter(f"*= 0x{org:06x}\n" + "\n".join(lines) + "\n", "rt.s", writer)

    assert b"".join(writer.data) == raw
