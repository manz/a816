"""An opcode form with one operand size widens a narrower unsized operand, never narrows.

`pea 0x0000` was sized from its value as a byte and rejected (E0306), so
a816 refused xdds's own disassembly. Widening is safe; narrowing would drop
bytes, so a value too wide for the only form still fails.
"""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


def _bytes(line: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter(f"*= 0x008000\n    {line}\n", "single.s", writer)
    return b"".join(writer.data)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("pea 0x0000", b"\xf4\x00\x00"),
        ("pea 0x00FF", b"\xf4\xff\x00"),
        ("lda 0x12,y", b"\xb9\x12\x00"),  # no dp,y form: absolute,y
        ("jmp (0x12)", b"\x6c\x12\x00"),
    ],
)
def test_a_narrower_operand_widens_to_the_only_form(line: str, expected: bytes) -> None:
    assert _bytes(line) == expected


@pytest.mark.parametrize("line", ["lda (0x1234)", "rep #0x1234"])
def test_a_wider_operand_is_not_narrowed(line: str) -> None:
    with pytest.raises(NodeError):
        _bytes(line)
