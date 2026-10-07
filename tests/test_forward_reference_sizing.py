"""An unsized operand naming a label further down the same block sizes like a backward one.

Size inference only needs the operand's class (byte, word, 24-bit). A label
later in the same block sits at or after the PC, in the same bank, so on the
label passes the PC stands in for it; emit then checks the guess held. Before,
a bare forward `jsr fwd` was E0200 while `jsr.w fwd` built (Feda, BL).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import ModuleBuilder
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


def _bytes(source: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter(source, "fwd.s", writer)
    return b"".join(writer.data)


def test_a_forward_jsr_and_jmp_in_bank_zero_are_absolute() -> None:
    code = _bytes("*= 0x008000\n    jsr fwd\n    jmp fwd\n    rts\nfwd:\n    rts\n")

    assert code == b"\x20\x07\x80\x4c\x07\x80\x60\x60"


def test_a_forward_reference_sizes_like_a_backward_one() -> None:
    """In bank $01 a backward `jsr` to a same-bank label is a JSL today; forward must agree."""
    backward = _bytes("*= 0x018000\nback:\n    rtl\n    jsr back\n")
    forward = _bytes("*= 0x018000\n    jsr fwd\nfwd:\n    rtl\n")

    assert (forward[0], len(forward)) == (backward[1], len(backward))


def test_a_forward_data_operand_in_bank_zero_is_absolute() -> None:
    assert _bytes("*= 0x008000\n    lda fwd\nfwd:\n    .db 0x42\n") == b"\xad\x03\x80\x42"


def test_a_target_outside_the_block_asks_for_the_size() -> None:
    """The PC guessed bank $00; the label landed in bank $01 and needs a long form."""
    source = "*= 0x008000\n    jsr far\n    rts\n*= 0x018000\nfar:\n    rtl\n"

    with pytest.raises(NodeError, match="sized as 3 bytes before its target was placed"):
        _bytes(source)


def test_a_name_never_defined_is_still_undefined() -> None:
    with pytest.raises(NodeError, match="nowhere"):
        _bytes("*= 0x008000\n    jsr nowhere\n    rts\n")


def test_a_forward_jsr_inside_an_alloc_body(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text(".alloc code at 0x008000 {\n    jsr draw\n    rts\ndraw:\n    rts\n}\n", encoding="utf-8")

    obj = ModuleBuilder(module_paths=[tmp_path], include_paths=[tmp_path], output_dir=tmp_path / "obj").build(main)

    assert obj.sections[0].code == b"\x20\x04\x80\x60\x60"
