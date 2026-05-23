"""`OP001` — redundant `.w` / `.l` opcode-size suffix.

Fires only on literal numeric operands whose value already forces the
same width through the assembler's value-driven size inference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff.runner import apply_fixes, lint_text


def _hits(src: str, code: str = "OP001") -> list[str]:
    return [d.message for d in lint_text(src, Path("x.s")) if d.code == code]


class TestRedundantSuffixFires:
    def test_w_on_16_bit_literal_immediate(self) -> None:
        # value 0x1234 > 0xFF -> .w is what inference picks anyway
        assert _hits('"""m."""\nlda.w #0x1234\n')

    def test_l_on_24_bit_literal_immediate(self) -> None:
        # value 0x012345 > 0xFFFF -> .l is what inference picks anyway
        assert _hits('"""m."""\nlda.l #0x012345\n')

    def test_w_on_decimal_literal(self) -> None:
        # 1000 == 0x3E8 > 0xFF
        assert _hits('"""m."""\nlda.w #1000\n')


class TestSuffixIsNotRedundant:
    def test_w_on_8_bit_literal_under_a16(self) -> None:
        # value 0x42 fits in a byte; .w *forces* widening (only way
        # to emit `A9 42 00` when A is 8-bit). NOT redundant.
        assert _hits('"""m."""\nlda.w #0x42\n') == []

    def test_b_suffix_is_never_flagged(self) -> None:
        # .b semantics depend on M/X state — leave alone.
        assert _hits('"""m."""\nlda.b #0x1234\n') == []
        assert _hits('"""m."""\nlda.b #0x42\n') == []

    def test_l_when_value_fits_in_word(self) -> None:
        # .l forces long addressing when value fits in 2 bytes — that's
        # the user picking the addressing form, not redundancy.
        assert _hits('"""m."""\nlda.l #0x1234\n') == []

    def test_symbolic_operand_not_flagged(self) -> None:
        # Symbol may resolve to any width — can't conclude statically.
        assert _hits('"""m."""\nlda.w #FOO\n') == []

    def test_expression_operand_not_flagged(self) -> None:
        assert _hits('"""m."""\nlda.w #(FOO + 1)\n') == []

    def test_bare_opcode_without_suffix(self) -> None:
        assert _hits('"""m."""\nlda #0x1234\n') == []


class TestAutofix:
    def test_strips_w_suffix(self) -> None:
        src = '"""m."""\nlda.w #0x1234\n'
        diags = lint_text(src, Path("x.s"))
        fixed, _ = apply_fixes(src, diags)
        assert "lda.w" not in fixed
        assert "lda #0x1234" in fixed

    def test_strips_l_suffix(self) -> None:
        src = '"""m."""\nlda.l #0x012345\n'
        diags = lint_text(src, Path("x.s"))
        fixed, _ = apply_fixes(src, diags)
        assert "lda.l" not in fixed
        assert "lda #0x012345" in fixed

    def test_noqa_suppresses(self) -> None:
        src = '"""m."""\nlda.w #0x1234  ; noqa: OP001\n'
        assert _hits(src) == []


@pytest.mark.parametrize("opcode", ["lda", "sta", "ldx", "ora", "adc", "cmp"])
def test_op001_fires_across_common_opcodes(opcode: str) -> None:
    src = f'"""m."""\n{opcode}.w #0x1234\n'
    assert any("redundant" in m for m in _hits(src))
