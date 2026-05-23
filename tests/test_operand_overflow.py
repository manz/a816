"""Operand value must fit in the chosen opcode width.

`lda.b #0xDEAD` used to silently truncate to `A9 AD` because of a
`value & 0xFF` mask in the emitter. The user only noticed when the
ROM crashed at runtime. The mask is the bug — let the emitter raise
when the value can't fit, mirroring how `RelativeJumpOpcode` already
catches branches out of signed-8-bit range.
"""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


def _emit(src: str) -> bytes:
    program = Program()
    writer = StubWriter()
    program.assemble_string_with_emitter(src, "x.s", writer)
    return b"".join(writer.data)


class TestExplicitSuffixOverflow:
    def test_byte_suffix_rejects_16bit_value(self) -> None:
        # `lda.b #0xDEAD` cannot fit in one byte. Currently truncates
        # to `A9 AD` silently — the bug this suite exists to catch.
        with pytest.raises((NodeError, ValueError), match="0xDEAD"):
            _emit("*=0x008000\n.a8\nlda.b #0xDEAD\n")

    def test_word_suffix_rejects_24bit_value(self) -> None:
        with pytest.raises((NodeError, ValueError), match="0x123456"):
            _emit("*=0x008000\nlda.w #0x123456\n")

    def test_long_suffix_rejects_32bit_value(self) -> None:
        with pytest.raises((NodeError, ValueError), match="0x12345678"):
            _emit("*=0x008000\nlda.l #0x12345678\n")

    def test_byte_suffix_accepts_8bit_value(self) -> None:
        # Sanity: in-range still works.
        assert _emit("*=0x008000\n.a8\nlda.b #0x42\n") == b"\xa9\x42"

    def test_word_suffix_accepts_16bit_value(self) -> None:
        assert _emit("*=0x008000\nlda.w #0x1234\n") == b"\xa9\x34\x12"

    def test_long_suffix_accepts_24bit_value(self) -> None:
        # `lda.l` has no immediate variant; use `.db` instead — but here
        # `lda` has long-absolute form; literal-immediate `.l` exercise:
        # use `.dl` to confirm long writes don't overflow at 0xFFFFFF.
        assert _emit("*=0x008000\n.dl 0xFFFFFF\n") == b"\xff\xff\xff"


class TestInferredSizeStillTruncatesByValue:
    """When the user did NOT pass an explicit suffix, the assembler
    picks width from the value itself, so overflow can't happen."""

    def test_no_suffix_picks_word_for_16bit_value(self) -> None:
        # Value drives the width; emit is `A9 AD DE`.
        assert _emit("*=0x008000\nlda #0xDEAD\n") == b"\xa9\xad\xde"
