"""An unsized operand naming a label further down the same block sizes like a backward one.

Size inference only needs the operand's class (byte, word, 24-bit). A label
later in the same block sits at or after the PC, in the same bank, so on the
label passes the PC stands in for it; emit then checks the guess held. Before,
a bare forward `jsr fwd` was E0200 while `jsr.w fwd` built (Feda, BL).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import ModuleBuilder
from tests import link_source


def _bytes(source: str) -> bytes:
    linked = link_source(source)
    assert linked.exit_code == 0
    return b"".join(linked.data)


def _error(source: str, caplog: pytest.LogCaptureFixture) -> str:
    """The diagnostics of a build that fails on `source`."""
    with caplog.at_level(logging.ERROR, logger="a816"):
        linked = link_source(source)
    assert linked.exit_code != 0
    return caplog.text


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


def test_a_target_in_another_bank_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """The PC guessed bank $00; the label landed in bank $01: a bare jsr can't reach it."""
    source = "*= 0x008000\n    jsr far\n    rts\n*= 0x018000\nfar:\n    rtl\n"

    assert "E0346" in _error(source, caplog)


def test_a_target_outside_the_block_with_another_size_asks_for_the_size(caplog: pytest.LogCaptureFixture) -> None:
    """Same bank, different class: guessed absolute (PC >= $0100), landed in direct page."""
    source = "*= 0x008000\n    lda fwd\n    rts\n*= 0x000010\nfwd:\n    .db 0\n"

    message = "given a 16-bit operand before its value was known, and the value needs 8-bit"
    assert message in _error(source, caplog)


def test_a_name_never_defined_is_still_undefined(caplog: pytest.LogCaptureFixture) -> None:
    assert "nowhere" in _error("*= 0x008000\n    jsr nowhere\n    rts\n", caplog)


def test_a_forward_jsr_inside_an_alloc_body(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text(".alloc code at 0x008000 {\n    jsr draw\n    rts\ndraw:\n    rts\n}\n", encoding="utf-8")

    obj = ModuleBuilder(module_paths=[tmp_path], include_paths=[tmp_path], output_dir=tmp_path / "obj").build(main)

    assert obj.sections[0].code == b"\x20\x04\x80\x60\x60"
