"""A bare `jsr` / `jmp` takes its form from the target's bank against the caller's.

It used to be sized by value like data: a 24-bit label in the caller's own
bank (every label in HiROM) emitted JSL / JML, so a same-bank call became a
long one whose callee must return with `rtl` (Feda; ff4 shipped a same-bank
JML since a44). A 16-bit value is an address in the current bank, as before.
"""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


def _bytes(source: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter(source, "transfer.s", writer)
    return b"".join(writer.data)


@pytest.mark.parametrize(("opcode", "absolute"), [("jsr", 0x20), ("jmp", 0x4C)])
def test_a_same_bank_label_above_bank_zero_is_absolute(opcode: str, absolute: int) -> None:
    code = _bytes(f"*= 0x018000\nback:\n    rts\n    {opcode} back\n")

    assert code == bytes([0x60, absolute, 0x00, 0x80])


def test_a_forward_same_bank_label_is_absolute() -> None:
    assert _bytes("*= 0x018000\n    jsr fwd\nfwd:\n    rts\n") == b"\x20\x03\x80\x60"


def test_a_label_in_another_bank_is_an_error() -> None:
    source = "*= 0x028000\nfar:\n    rtl\n*= 0x018000\n    jsr far\n"

    with pytest.raises(NodeError, match="E0346"):
        _bytes(source)


def test_the_error_names_the_long_form() -> None:
    source = "*= 0x028000\nfar:\n    rtl\n*= 0x018000\n    jmp far\n"

    with pytest.raises(NodeError, match="write `jml`"):
        _bytes(source)


def test_a_16_bit_value_is_an_address_in_the_current_bank() -> None:
    assert _bytes("*= 0x018000\n    jsr 0x8000\n") == b"\x20\x00\x80"


@pytest.mark.parametrize("line", ["jsr.l far", "jsl far"])
def test_an_explicit_long_form_still_wins(line: str) -> None:
    code = _bytes(f"*= 0x028000\nfar:\n    rtl\n*= 0x018000\n    {line}\n")

    assert code.endswith(b"\x22\x00\x80\x02")
