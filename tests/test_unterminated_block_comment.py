"""An unterminated `/*` is a located error (E0004), not a hang; unary operators format against their operand."""

from __future__ import annotations

import threading

import pytest

from a816.formatter import A816Formatter
from a816.parse.mzparser import A816Parser


def _parse_within(source: str, seconds: float = 5.0) -> str:
    """Parse on a thread so a regression fails the test instead of hanging the suite."""
    result: list[str] = []
    worker = threading.Thread(target=lambda: result.append(A816Parser.parse_as_ast(source, "m.s").error or ""))
    worker.daemon = True
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "parsing an unterminated block comment hung"
    return result[0]


@pytest.mark.parametrize(
    "source",
    ["/* never closed", "nop\n/* never closed\nrts\n", "/*"],
    ids=["alone", "after code", "bare opener"],
)
def test_an_unterminated_block_comment_is_e0004(source: str) -> None:
    assert "[E0004]" in _parse_within(source)


def test_the_error_points_at_the_opening_comment() -> None:
    assert "unterminated block comment" in _parse_within("nop\n/* never closed\n")


def test_a_closed_block_comment_still_parses() -> None:
    assert _parse_within("/* fine */\nnop\n") == ""


@pytest.mark.parametrize(
    "line",
    ["X = ~3\n", "Y = -3 + -X\n", "Z = -( 1 + 2 )\n"],
)
def test_unary_operators_format_against_their_operand(line: str) -> None:
    assert A816Formatter().format_text(line) == line
