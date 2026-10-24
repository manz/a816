"""`OP001` — redundant `.w` / `.l` opcode-size suffix.

Fires only on literal address operands whose value already forces the
same width through the assembler's value-driven size inference.
Immediates are never flagged: their width follows M/X.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff.runner import apply_fixes, lint_text
from a816.program import Program
from tests import StubWriter


def _assemble(src: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter(src, "x.s", writer)
    return b"".join(writer.data)


def _hits(src: str, code: str = "OP001") -> list[str]:
    return [d.message for d in lint_text(src, Path("x.s")) if d.code == code]


class TestRedundantSuffixFires:
    def test_w_on_16_bit_literal_address(self) -> None:
        # value 0x1234 > 0xFF -> .w is what inference picks anyway
        assert _hits('"""m."""\nlda.w 0x1234\n')

    def test_l_on_24_bit_literal_address(self) -> None:
        # value 0x012345 > 0xFFFF -> .l is what inference picks anyway
        assert _hits('"""m."""\nlda.l 0x012345\n')

    def test_w_on_decimal_literal(self) -> None:
        # 1000 == 0x3E8 > 0xFF
        assert _hits('"""m."""\nlda.w 1000\n')

    def test_w_on_indexed_address(self) -> None:
        assert _hits('"""m."""\nlda.w 0x1234, x\n')


class TestSuffixIsNotRedundant:
    @pytest.mark.parametrize("line", ["lda.w #0x1234", "ldx.w #1000", "lda.l #0x012345", "cpy.w #0x0100"])
    def test_immediate_is_never_flagged(self, line: str) -> None:
        # The suffix states the M/X width the code expects (ff4, ff6,
        # dq6, thg): it documents the register, not the value.
        assert _hits(f'"""m."""\n{line}\n') == []

    def test_w_on_8_bit_literal_address(self) -> None:
        # value 0x42 fits in a byte; .w forces absolute over direct page.
        assert _hits('"""m."""\nlda.w 0x42\n') == []

    def test_b_suffix_is_never_flagged(self) -> None:
        # .b semantics depend on M/X state — leave alone.
        assert _hits('"""m."""\nlda.b #0x1234\n') == []
        assert _hits('"""m."""\nlda.b #0x42\n') == []

    def test_l_when_value_fits_in_word(self) -> None:
        # .l forces long addressing when value fits in 2 bytes — that's
        # the user picking the addressing form, not redundancy.
        assert _hits('"""m."""\nlda.l 0x1234\n') == []

    def test_symbolic_operand_not_flagged(self) -> None:
        # Symbol may resolve to any width — can't conclude statically.
        assert _hits('"""m."""\nlda.w FOO\n') == []

    def test_expression_operand_not_flagged(self) -> None:
        assert _hits('"""m."""\nlda.w (FOO + 1)\n') == []

    def test_bare_opcode_without_suffix(self) -> None:
        assert _hits('"""m."""\nlda 0x1234\n') == []

    def test_w_on_24_bit_literal_truncates(self) -> None:
        # ff6: `jsr.w 0xC006AC` is an in-bank call; bare, it is long.
        assert _hits('"""m."""\njsr.w 0xC006AC\n') == []

    @pytest.mark.parametrize("opcode", ["jmp", "jsr"])
    def test_l_on_transfer_to_24_bit_literal(self, opcode: str) -> None:
        # ff6 pol_8x8.s: a bare same-bank target is a 3-byte jmp / jsr.
        assert _hits(f'"""m."""\n{opcode}.l 0xC3FC00\n') == []


class TestSafeFixKeepsTheBytes:
    """A [*] fix must not change what assembles."""

    @pytest.mark.parametrize(
        "line",
        ["jsr.w 0xC006AC", "jmp.l 0xC3FC00", "jsr.l 0xC3FC00", "lda.w 0xC00000", "lda.w 0x1234", "lda.l 0xC00000"],
    )
    def test_fixed_source_assembles_the_same(self, line: str) -> None:
        src = f'"""m."""\n*= 0xC3F000\n    {line}\n'
        fixed, _ = apply_fixes(src, lint_text(src, Path("x.s")))

        assert _assemble(fixed) == _assemble(src)


class TestAutofix:
    def test_strips_w_suffix(self) -> None:
        src = '"""m."""\nlda.w 0x1234\n'
        diags = lint_text(src, Path("x.s"))
        fixed, _ = apply_fixes(src, diags)
        assert "lda.w" not in fixed
        assert "lda 0x1234" in fixed

    def test_strips_l_suffix(self) -> None:
        src = '"""m."""\nlda.l 0x012345\n'
        diags = lint_text(src, Path("x.s"))
        fixed, _ = apply_fixes(src, diags)
        assert "lda.l" not in fixed
        assert "lda 0x012345" in fixed

    def test_noqa_suppresses(self) -> None:
        src = '"""m."""\nlda.w 0x1234  ; noqa: OP001\n'
        assert _hits(src) == []


@pytest.mark.parametrize("opcode", ["lda", "sta", "ldx", "ora", "adc", "cmp"])
def test_op001_fires_across_common_opcodes(opcode: str) -> None:
    src = f'"""m."""\n{opcode}.w 0x1234\n'
    assert any("redundant" in m for m in _hits(src))


_POOL = ".pool code {\n    range 0x008000 0x00FFFF\n}\n"


class TestFiresInsidePlacementBodies:
    def test_inside_pooled_alloc(self) -> None:
        src = f'"""m."""\n{_POOL}.alloc main in code {{\n    lda.w 0x1234\n}}\n'
        assert _hits(src)

    def test_inside_pinned_alloc(self) -> None:
        assert _hits('"""m."""\n.alloc at 0x008000 {\n    lda.w 0x1234\n}\n')

    def test_inside_relocate(self) -> None:
        src = f'"""m."""\n{_POOL}.relocate old 0x009000 0x0090FF into code {{\n    lda.w 0x1234\n}}\n'
        assert _hits(src)

    def test_inside_scope_in_alloc(self) -> None:
        src = '"""m."""\n.alloc at 0x008000 {\n    .scope s {\n        lda.w 0x1234\n    }\n}\n'
        assert _hits(src)
