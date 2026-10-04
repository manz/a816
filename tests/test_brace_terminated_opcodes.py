"""Implied-operand opcodes before `}` on the same line end the statement."""

from __future__ import annotations

import pytest

from a816.formatter import A816Formatter
from a816.parse.mzparser import A816Parser
from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial
from a816.parse.tokens import TokenType
from a816.program import Program
from tests import StubWriter


@pytest.fixture(autouse=True)
def _disable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    import a816.errors as err_mod

    monkeypatch.setattr(err_mod, "_USE_COLORS", False)


def _token_types(src: str) -> list[TokenType]:
    tokens = Scanner(lex_initial).scan("t.s", src)
    return [t.type for t in tokens if t.type != TokenType.EOF]


def _emitted(src: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter(src, "t.s", writer)
    return b"".join(writer.data)


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("*=0x8000\n.scope x { rts }\n", b"\x60"),
        ("*=0x8000\n{ rts }\n", b"\x60"),
        ("*=0x8000\n{ nop nop }\n", b"\xea\xea"),
        ("*=0x8000\n{ inc }\n", b"\x1a"),
        ("*=0x8000\n{ asl ; shift\n}\n", b"\x0a"),
        ("*=0x8000\n{ lda #1 }\n", b"\xa9\x01"),
        ("*=0x8000\nC = 1\n.if C { rts }\n", b"\x60"),
        ("*=0x8000\nC = 0\n.if C { nop } .else { rts }\n", b"\x60"),
        ("*=0x8000\n.macro m() { rts }\nm()\n", b"\x60"),
        ("*=0x8000\n{ .a8 }\n.scope y { .i16 }\nrts\n", b"\x60"),
        (".pool slack { range 0x008000 0x0080ff }\n.alloc fn_a in slack { rts }\n", b"\x60"),
    ],
)
def test_same_line_brace_block_assembles(src: str, expected: bytes) -> None:
    assert _emitted(src) == expected


def test_implied_opcode_before_brace_scans_naked() -> None:
    assert _token_types("{ rts }") == [TokenType.LBRACE, TokenType.OPCODE_NAKED, TokenType.RBRACE]


def test_accumulator_opcode_before_brace_scans_naked() -> None:
    assert _token_types("{ inc }") == [TokenType.LBRACE, TokenType.OPCODE_NAKED, TokenType.RBRACE]


def test_accumulator_opcode_with_operand_keeps_operand() -> None:
    assert _token_types("inc 0x10") == [TokenType.OPCODE, TokenType.NUMBER]


def test_implied_only_opcode_before_mnemonic_scans_naked() -> None:
    assert _token_types("nop nop") == [TokenType.OPCODE_NAKED, TokenType.OPCODE_NAKED]


def test_implied_only_opcode_before_identifier_keeps_operand() -> None:
    assert _token_types("nop foo") == [TokenType.OPCODE, TokenType.IDENTIFIER]


def test_sized_implied_opcode_still_lexes_size() -> None:
    assert _token_types("rts.w") == [TokenType.OPCODE, TokenType.OPCODE_SIZE]


@pytest.mark.parametrize("src", ["{ lda }\n", "lda", "{ lda # }\n"])
def test_missing_operand_reports_e0115(src: str) -> None:
    result = A816Parser.parse_as_ast(src, "missing.s")
    assert "[E0115]" in (result.error or "")


def test_missing_operand_caret_under_opcode() -> None:
    result = A816Parser.parse_as_ast("{ lda }\n", "missing.s")
    assert "  |   ^^^\n" in (result.error or "")


def test_missing_operand_names_opcode() -> None:
    result = A816Parser.parse_as_ast("{ lda }\n", "missing.s")
    assert "`lda` needs an operand" in (result.error or "")


def test_formatter_expands_implied_one_liner() -> None:
    formatted = A816Formatter().format_text(".scope x { rts }\n")
    assert formatted == ".scope x {\n    rts\n}\n"


def test_formatter_expands_double_implied_one_liner() -> None:
    formatted = A816Formatter().format_text("{ nop nop }\n")
    assert formatted == "{\n    nop\n    nop\n}\n"
