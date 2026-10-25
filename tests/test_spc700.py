"""SPC700 assembly with `--arch spc700`: encodings, operand forms, ARAM images.

`samples/spc700/all_opcodes.bin` is wla-spc700's output for `all_opcodes.s`
(`$` for `0x`, `.ORG $0400`, linked with `wlalink -b -bS 0x400`): every one
of the 256 opcodes, assembled by another assembler.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from a816.cpu.arch import using_arch
from a816.cpu.spc700 import SPC700_TABLE
from a816.exceptions import AssemblyError
from a816.parse.mzparser import A816Parser
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.writers import AramWriter

SAMPLES = Path(__file__).parent / "samples" / "spc700"
_ARAM_MAP = ".map identifier=1 bank_range=0x00, 0x00 addr_range=0x0000, 0xffff mask=0x10000\n"


def _image(source: str) -> bytes:
    writer_output = io.BytesIO()
    writer = AramWriter(writer_output)
    with using_arch("spc700"):
        Program().assemble_string_with_emitter(_ARAM_MAP + "*= 0x0400\n" + source, "t.s", writer)
    writer.end()
    return writer_output.getvalue()


def test_every_opcode_matches_wla_spc700() -> None:
    source = (SAMPLES / "all_opcodes.s").read_text(encoding="utf-8")
    assert _image(source.split("*= 0x0400\n", 1)[1]) == (SAMPLES / "all_opcodes.bin").read_bytes()


def test_the_table_covers_all_256_opcodes() -> None:
    opcodes: set[int] = set()
    for encodings in SPC700_TABLE.values():
        for encoding in encodings:
            if "dp.bit" in encoding.patterns:
                opcodes.update(encoding.opcode | bit << 5 for bit in range(8))
            elif "vector" in encoding.patterns:
                opcodes.update(encoding.opcode | vector << 4 for vector in range(16))
            else:
                opcodes.add(encoding.opcode)
    assert opcodes == set(range(256))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("mov a, 0x12", "e4 12"),
        ("mov a, 0x1234", "e5 34 12"),
        ("mov a, !0x12", "e5 12 00"),
        ("mov a, 0x12+x", "f4 12"),
        ("mov a, 0x1234+y", "f6 34 12"),
        ("mov 0x10, #0x55", "8f 55 10"),
        ("mov 0x10, 0x11", "fa 11 10"),
        ("mov a, (x)+", "bf"),
        ("mov a, [0x20]+y", "f7 20"),
        ("mov a, [0x20+x]", "e7 20"),
        ("jmp [!0x1234+x]", "1f 34 12"),
        ("set1 0x30.7", "e2 30"),
        ("mov1 c, 0x1234.5", "aa 34 b2"),
        ("or1 c, /0x1234.5", "2a 34 b2"),
        ("tcall 3", "31"),
        ("mov a, #(0x1234 >> 8)", "e8 12"),
        ("nop", "00"),
    ],
)
def test_operand_forms(source: str, expected: str) -> None:
    assert _image(f"    {source}\n").hex(" ") == expected


def test_branches_count_from_the_end_of_the_instruction() -> None:
    assert _image("back:\n    cbne 0x20, back\n    bne back\n").hex(" ") == "2e 20 fd d0 fb"


def test_a_forward_label_takes_the_absolute_form() -> None:
    assert _image("    jmp later\nlater:\n    ret\n").hex(" ") == "5f 03 04 6f"


def test_macro_arguments_reach_the_operands() -> None:
    source = ".macro dspw(reg, value) {\n    mov a, #value\n    mov 0xf2, a\n}\n    dspw(0x6c, 0x20)\n"
    assert _image(source).hex(" ") == "e8 20 c4 f2"


def test_a_branch_out_of_range_is_an_error() -> None:
    with pytest.raises(NodeError, match="bytes away"):
        _image("    bra far\n    .db " + ", ".join(["0"] * 200) + "\nfar:\n")


def test_an_operand_form_the_cpu_lacks_is_an_error() -> None:
    with pytest.raises(NodeError, match="no SPC700 encoding"):
        _image("    mov a, (y)\n")


def test_a_bad_indirect_is_an_error() -> None:
    with pytest.raises(Exception, match=r"\[dp\+x\]"):
        _image("    mov a, [0x20]\n")


def test_an_unknown_character_in_an_operand_is_an_error() -> None:
    with pytest.raises(AssemblyError, match="SPC700 operand"):
        _image("    mov a, @0x20\n")


def test_spc700_mnemonics_are_identifiers_on_the_65c816() -> None:
    program = Program()
    program.assemble_string_with_emitter("or = 3\n*= 0x8000\n    lda #or\n", "t.s", AramWriter(io.BytesIO()))
    assert program.resolver.current_scope.value_for("or") == 3


def test_the_image_starts_at_its_lowest_address() -> None:
    assert _image("    nop\n*= 0x0410\n    ret\n") == b"\x00" + bytes(15) + b"\x6f"


def test_an_empty_program_writes_an_empty_image() -> None:
    output = io.BytesIO()
    AramWriter(output).end()
    assert output.getvalue() == b""


def test_code_past_64k_is_an_error() -> None:
    with pytest.raises(ValueError, match="64 KB"):
        AramWriter(io.BytesIO()).write_block(b"\x00\x00", 0xFFFF)


@pytest.mark.parametrize(
    "line",
    [
        "nop",
        "mov a, #0x12",
        "mov a, 0x12+x",
        "mov a, 0x12+y",
        "mov a, !0x1234",
        "mov a, !0x1234+x",
        "mov a, !0x1234+y",
        "mov a, [0x12+x]",
        "mov a, [0x12]+y",
        "jmp [!0x1234+x]",
        "mov a, (x)+",
        "mov1 c, 0x1234.5",
        "or1 c, /0x1234.5",
    ],
)
def test_an_instruction_prints_back_as_written(line: str) -> None:
    with using_arch("spc700"):
        (node,) = A816Parser.parse_as_ast(line, filename="t.s").nodes
    assert node.to_canonical() == line


def test_an_instruction_represents_its_operands() -> None:
    with using_arch("spc700"):
        (node,) = A816Parser.parse_as_ast("mov a, 0x12+x", filename="t.s").nodes
    assert node.to_representation() == ("spc_opcode", "mov", ("a", "0x12+x"))
