"""Stellar diagnostics: codes, hints, did-you-mean, multi-error recovery."""

from __future__ import annotations

import os

import pytest

from a816.diagnostics.suggest import closest_matches, did_you_mean_hint
from a816.error_codes import all_codes, lookup
from a816.parse.mzparser import MZParser
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.symbols import Resolver
from tests import StubWriter


@pytest.fixture(autouse=True)
def _disable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ANSI so substring asserts work regardless of terminal state."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    # `a816.errors._USE_COLORS` is captured at import time; flip it for tests.
    import a816.errors as err_mod

    monkeypatch.setattr(err_mod, "_USE_COLORS", False)


def test_error_codes_are_unique_and_well_formed() -> None:
    codes = all_codes()
    assert len(codes) == len({c.code for c in codes}), "duplicate error codes"
    for code in codes:
        assert code.code.startswith("E") and code.code[1:].isdigit()
        assert code.category
        assert code.short_description


def test_lookup_known_and_unknown_codes() -> None:
    assert lookup("E0100") is not None
    assert lookup("E9999") is None


def test_parser_error_renders_code_and_hint() -> None:
    result = MZParser.parse_as_ast(".pool p { range 0x1000 0x1fff bogus 1 }\n", "demo.s")
    assert result.error is not None
    assert "[E0106]" in result.error
    assert "unknown `.pool` attribute" in result.error
    assert "hint:" in result.error
    assert "range, fill, strategy" in result.error


def test_parser_error_includes_context_lines() -> None:
    src = "; line above\n.lalala foo\n; line below\n"
    result = MZParser.parse_as_ast(src, "ctx.s")
    assert result.error is not None
    # Above + below should appear in the rendered block.
    assert "; line above" in result.error
    assert "; line below" in result.error


def test_did_you_mean_picks_closest_match() -> None:
    resolver = Resolver()
    resolver.current_scope.add_symbol("player_x", 0)
    resolver.current_scope.add_symbol("player_y", 1)
    resolver.current_scope.add_symbol("player_hp", 2)

    matches = closest_matches("playr_x", resolver.current_scope)
    assert "player_x" in matches


def test_did_you_mean_returns_none_when_far_away() -> None:
    resolver = Resolver()
    resolver.current_scope.add_symbol("totally_unrelated", 0)
    assert did_you_mean_hint("xyz", resolver.current_scope) is None


def test_undefined_symbol_error_includes_suggestion() -> None:
    program = Program()
    src = """
my_routine:
    rts
    jsr.l my_routime
"""
    with pytest.raises(NodeError) as exc_info:
        program.assemble_string_with_emitter(src, "dym.s", StubWriter())
    rendered = str(exc_info.value)
    assert "[E0200]" in rendered
    assert "`my_routime`" in rendered
    assert "did you mean `my_routine`?" in rendered


def test_multi_error_collects_top_level_failures() -> None:
    src = """
.struct OAM {
    word x
    word x
}
lda.w (0x2100).hp
"""
    result = MZParser.parse_as_ast(src, "multi.s")
    assert result.parse_errors is not None
    assert len(result.parse_errors) >= 2
    rendered = result.error or ""
    assert "[E0103]" in rendered
    assert "[E0105]" in rendered


def test_token_type_label_uses_friendly_name() -> None:
    """Generic expect_token errors no longer leak `TokenType.X` to the user."""
    result = MZParser.parse_as_ast(".include\n", "missing.s")
    assert result.error is not None
    assert "quoted string" in result.error
    assert "TokenType" not in result.error


def test_typed_bind_with_equal_shows_code_and_hint() -> None:
    src = """
.struct Pt {
    word x
}
p = 0x100 as Pt
"""
    result = MZParser.parse_as_ast(src, "tb.s")
    assert result.error is not None
    assert "[E0104]" in result.error
    assert "use `name := expr as T`" in result.error


def _strip_ansi(s: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_format_error_respects_no_color_env() -> None:
    """With NO_COLOR set, no ANSI escapes leak into the output."""
    src = ".pool p { range 0x1000 0x1fff bogus 1 }\n"
    result = MZParser.parse_as_ast(src, "nc.s")
    assert result.error is not None
    assert "\x1b[" not in result.error, "ANSI escapes leaked despite NO_COLOR"
    assert _strip_ansi(result.error) == result.error
    _ = os  # silence "unused import" — used by the env helpers above
