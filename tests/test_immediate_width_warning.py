"""Warn when an immediate's emitted width disagrees with the known M/X size.

A stray `lda.b #1` under 16-bit A (or `lda #0x1234` under 8-bit A) shifts
every following byte at runtime: the CPU reads the operand at the width its
M/X flag says, not the width the assembler picked. The assembler still emits
what the source asked for (explicit suffix wins, value drives otherwise) but
says so whenever the register size is actually known.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.cpu.mapping import Address
from a816.cpu.types import AddressingMode
from a816.exceptions import SymbolNotDefined
from a816.parse.nodes import OpcodeNode
from a816.parse.nodes.errors import node_source_location
from a816.parse.tokens import File, Position, Token, TokenType
from a816.program import Program
from a816.protocols import ValueNodeProtocol
from a816.symbols import Resolver
from tests import StubWriter

_WARNING_FRAGMENT = "immediate width"


def _assemble(src: str, caplog: pytest.LogCaptureFixture, *, track: bool = False) -> bytes:
    program = Program()
    program.resolver.track_register_size = track
    writer = StubWriter()
    with caplog.at_level(logging.WARNING, logger="a816"):
        program.assemble_string_with_emitter(src, "width.s", writer)
    return b"".join(writer.data)


def _width_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if _WARNING_FRAGMENT in r.getMessage()]


class TestMismatchWarns:
    def test_a8_with_wide_value_warns_and_still_emits_word(self, caplog: pytest.LogCaptureFixture) -> None:
        assert _assemble("*=0x008000\n.a8\nlda #0x1234\n", caplog) == b"\xa9\x34\x12"
        assert len(_width_warnings(caplog)) == 1

    def test_a16_with_byte_suffix_warns_and_still_emits_byte(self, caplog: pytest.LogCaptureFixture) -> None:
        assert _assemble("*=0x008000\n.a16\nlda.b #1\n", caplog) == b"\xa9\x01"
        assert len(_width_warnings(caplog)) == 1

    def test_i8_with_word_suffix_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        _assemble("*=0x008000\n.i8\nldx.w #1\n", caplog)
        assert len(_width_warnings(caplog)) == 1

    def test_warning_carries_location_and_register(self, caplog: pytest.LogCaptureFixture) -> None:
        _assemble("*=0x008000\n.a16\nlda.b #1\n", caplog)
        message = _width_warnings(caplog)[0]
        assert "width.s:3" in message
        assert "A is 16-bit" in message

    def test_tracked_rep_makes_state_known(self, caplog: pytest.LogCaptureFixture) -> None:
        _assemble("*=0x008000\nrep #0x20\nlda.b #1\n", caplog, track=True)
        assert len(_width_warnings(caplog)) == 1

    def test_object_mode_warns(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        src = tmp_path / "obj.s"
        src.write_text("*=0x008000\n.a16\nlda.b #1\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="a816"):
            assert Program().assemble_as_object(str(src), tmp_path / "obj.o") == 0
        assert len(_width_warnings(caplog)) == 1


class TestNoWarningWhenConsistentOrUnknown:
    @pytest.mark.parametrize(
        "src",
        [
            "lda #0x1234\n",  # unknown state: value drives width
            "lda.b #1\n",  # unknown state: suffix wins
            ".a16\nlda #1\n",  # known 16, inferred w
            ".a8\nlda #1\n",  # known 8, value fits
            ".a8\nlda 0x1234\n",  # absolute, not immediate
            ".a8\nldx #0x1234\n",  # A known, X unknown
            ".a8\nrep #0x20\nlda.w #1\n",  # untracked rep forgets A
            ".a16\nplp\nlda.b #1\n",  # plp restores unknown flags
            ".a8\nrts\n_other:\nlda.w #1\n",  # code after rts is reached from elsewhere
            ".a8\nbra _other\n_other:\nlda.w #1\n",  # likewise after an unconditional branch
            ".a16\n*=0x009000\nlda.b #1\n",  # new placement block resets state
        ],
    )
    def test_silent(self, src: str, caplog: pytest.LogCaptureFixture) -> None:
        _assemble("*=0x008000\n" + src, caplog)
        assert _width_warnings(caplog) == []

    def test_sep_after_tracked_rep_agrees(self, caplog: pytest.LogCaptureFixture) -> None:
        _assemble("*=0x008000\nrep #0x30\nlda #0x1234\nsep #0x20\nlda #1\n", caplog, track=True)
        assert _width_warnings(caplog) == []


class _UnresolvedValue(ValueNodeProtocol):
    def get_value(self) -> int:
        raise SymbolNotDefined("LATER")

    def get_value_string_len(self) -> int:
        return 2


class TestUnresolvedRepSep:
    def test_unresolved_rep_leaves_state_alone(self) -> None:
        resolver = Resolver()
        resolver.track_register_size = True
        resolver.a_size_known = True
        node = OpcodeNode(
            "rep",
            addressing_mode=AddressingMode.immediate,
            value_node=_UnresolvedValue(),
            file_info=Token(TokenType.IDENTIFIER, "rep"),
            resolver=resolver,
        )
        node.pc_after(Address(resolver.get_bus(), 0x8000))
        assert (resolver.a_size, resolver.a_size_known) == (8, True)


def test_source_location_tolerates_missing_line() -> None:
    token = Token(TokenType.IDENTIFIER, "lda", Position(3, 0, File("gone.s")))
    location = node_source_location(token)
    assert location is not None
    assert location.source_line == ""
