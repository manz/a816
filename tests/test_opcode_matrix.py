"""Full 65c816 opcode-matrix coverage.

Single source of truth: the assembler's `snes_opcode_table`. The disassembler's
`OPCODE_TABLE` is derived from it. For every defined opcode byte this test:

1. synthesizes an a816 source line for the `(mnemonic, addressing-mode)` the
   decoder reports for that byte, assembles it, and asserts the emitted opcode
   byte matches, catching the ORA-to-LDA class of hand-table typo; and
2. disassembles the assembled bytes and asserts the mnemonic + mode round-trip.

If the two tables ever drift, this fails.
"""

from __future__ import annotations

import pytest

from a816.cpu.disassembler import OPCODE_TABLE, AddrMode, Disassembler
from a816.parse.nodes.opcode import OpcodeNode
from a816.program import Program

# Per addressing mode, the operand text appended after the mnemonic. Values are
# chosen to force the exact mode/size slot (dp = 1-byte, abs = 2-byte, long =
# 3-byte). Relative targets sit just past the instruction so the offset is tiny.
_OPERAND_SRC: dict[AddrMode, str] = {
    AddrMode.IMPLIED: "",
    AddrMode.IMMEDIATE_8: " #0x10",
    AddrMode.IMMEDIATE_M: " #0x10",
    AddrMode.IMMEDIATE_X: " #0x10",
    AddrMode.DIRECT: ".b 0x10",
    AddrMode.DIRECT_X: ".b 0x10,x",
    AddrMode.DIRECT_Y: ".b 0x10,y",
    AddrMode.DIRECT_IND: " (0x10)",
    AddrMode.DIRECT_IND_X: " (0x10,x)",
    AddrMode.DIRECT_IND_Y: " (0x10),y",
    AddrMode.DIRECT_IND_LONG: " [0x10]",
    AddrMode.DIRECT_IND_LONG_Y: " [0x10],y",
    AddrMode.ABSOLUTE: ".w 0x1234",
    AddrMode.ABSOLUTE_X: ".w 0x1234,x",
    AddrMode.ABSOLUTE_Y: ".w 0x1234,y",
    AddrMode.ABSOLUTE_LONG: ".l 0x123456",
    AddrMode.ABSOLUTE_LONG_X: ".l 0x123456,x",
    AddrMode.ABSOLUTE_IND: " (0x1234)",
    AddrMode.ABSOLUTE_IND_X: " (0x1234,x)",
    AddrMode.ABSOLUTE_IND_LONG: " [0x1234]",
    AddrMode.STACK_REL: " 0x10,s",
    AddrMode.STACK_REL_IND_Y: " (0x10,s),y",
    AddrMode.RELATIVE: " 0x008002",
    AddrMode.RELATIVE_LONG: " 0x008003",
    AddrMode.BLOCK_MOVE: " 0x01, 0x02",
}


# Exact operand bytes each `_OPERAND_SRC` entry encodes (little-endian), under
# `.a8`/`.i8` with the instruction anchored at 0x008000. Relative targets are
# the instruction end, so the signed displacement is 0.
_OPERAND_BYTES: dict[AddrMode, bytes] = {
    AddrMode.IMPLIED: b"",
    AddrMode.IMMEDIATE_8: b"\x10",
    AddrMode.IMMEDIATE_M: b"\x10",
    AddrMode.IMMEDIATE_X: b"\x10",
    AddrMode.DIRECT: b"\x10",
    AddrMode.DIRECT_X: b"\x10",
    AddrMode.DIRECT_Y: b"\x10",
    AddrMode.DIRECT_IND: b"\x10",
    AddrMode.DIRECT_IND_X: b"\x10",
    AddrMode.DIRECT_IND_Y: b"\x10",
    AddrMode.DIRECT_IND_LONG: b"\x10",
    AddrMode.DIRECT_IND_LONG_Y: b"\x10",
    AddrMode.ABSOLUTE: b"\x34\x12",
    AddrMode.ABSOLUTE_X: b"\x34\x12",
    AddrMode.ABSOLUTE_Y: b"\x34\x12",
    AddrMode.ABSOLUTE_LONG: b"\x56\x34\x12",
    AddrMode.ABSOLUTE_LONG_X: b"\x56\x34\x12",
    AddrMode.ABSOLUTE_IND: b"\x34\x12",
    AddrMode.ABSOLUTE_IND_X: b"\x34\x12",
    AddrMode.ABSOLUTE_IND_LONG: b"\x34\x12",
    AddrMode.STACK_REL: b"\x10",
    AddrMode.STACK_REL_IND_Y: b"\x10",
    AddrMode.RELATIVE: b"\x00",
    AddrMode.RELATIVE_LONG: b"\x00\x00",
    AddrMode.BLOCK_MOVE: b"\x02\x01",  # opcode, destbank, srcbank
}


def _assemble(line: str) -> bytes:
    program = Program()
    # *= anchors relative-branch math; .a8/.i8 pin immediate width to 8-bit so
    # IMMEDIATE_M/X assemble + disassemble (default flags) at the same length.
    err, nodes = program.parser.parse(f"*=0x008000\n.a8\n.i8\n{line}")
    assert err is None, f"parse error for {line!r}: {err}"
    program.resolve_labels(nodes)
    out = b""
    for node in nodes:
        if isinstance(node, OpcodeNode):
            out += node.emit(program.resolver.reloc_address)
    return out


def test_table_covers_all_256_opcodes() -> None:
    assert sorted(OPCODE_TABLE) == list(range(256))


@pytest.mark.parametrize("opcode", sorted(OPCODE_TABLE))
def test_opcode_assembles_and_roundtrips(opcode: int) -> None:
    mnemonic, mode, _ = OPCODE_TABLE[opcode]
    line = f"{mnemonic}{_OPERAND_SRC[mode]}"

    assembled = _assemble(line)
    expected = bytes([opcode]) + _OPERAND_BYTES[mode]
    assert assembled == expected, f"{line!r} assembled to {assembled.hex()}, expected {expected.hex()}"

    inst = Disassembler().decode_instruction(assembled, 0x008000)
    assert inst is not None
    assert inst.mnemonic == mnemonic, f"0x{opcode:02X}: {inst.mnemonic!r} != {mnemonic!r}"
    assert inst.mode == mode, f"0x{opcode:02X}: {inst.mode} != {mode}"
    # Decoder must consume exactly what the assembler emitted: no trailing
    # bytes, no over-read.
    assert inst.length == len(assembled), f"0x{opcode:02X}: length {inst.length} != {len(assembled)}"
