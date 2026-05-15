from __future__ import annotations

import pytest

from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    BlockAstNode,
    OpcodeAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
)
from a816.parse.mzparser import MZParser


def _parse(src: str) -> list[AstNode]:
    result = MZParser.parse_as_ast(src, filename="t.s")
    assert result.parse_error is None, result.parse_error and result.parse_error.format()
    return result.nodes


def _first_of[T: AstNode](src: str, kind: type[T]) -> T:
    nodes = _parse(src)
    matches = [n for n in nodes if isinstance(n, kind)]
    assert matches, f"no {kind.__name__} in AST"
    return matches[0]


class TestPoolDirective:
    def test_single_range(self) -> None:
        node = _first_of(
            """
            .pool bank02_slack {
                range 0x028000 0x028fff
            }
            """,
            PoolAstNode,
        )
        assert node.pool_name == "bank02_slack"
        assert len(node.ranges) == 1
        assert node.strategy == "pack"

    def test_multi_range_with_options(self) -> None:
        node = _first_of(
            """
            .pool bank02_slack {
                range 0x028000 0x028fff
                range 0x02a100 0x02a4c0
                fill 0xea
                strategy order
            }
            """,
            PoolAstNode,
        )
        assert len(node.ranges) == 2
        assert node.strategy == "order"

    def test_empty_pool_is_error(self) -> None:
        result = MZParser.parse_as_ast(".pool empty {}", filename="t.s")
        assert result.parse_error is not None
        assert "no ranges" in result.parse_error.message

    def test_fill_out_of_byte_range_is_error(self) -> None:
        # Now caught at codegen, not parse. Drive through assemble.
        from a816.parse.codegen import code_gen
        from a816.symbols import Resolver

        resolver = Resolver()
        result = MZParser.parse_as_ast(
            ".pool p { range 0x028000 0x028fff fill 0x100 }",
            filename="t.s",
        )
        assert result.parse_error is None
        with pytest.raises(Exception, match="out of byte range"):
            code_gen(result.nodes, resolver)

    def test_unknown_strategy_is_error(self) -> None:
        result = MZParser.parse_as_ast(
            ".pool p { range 0x028000 0x028fff strategy bogus }",
            filename="t.s",
        )
        assert result.parse_error is not None
        assert "unknown pool strategy" in result.parse_error.message

    def test_unknown_attribute_is_error(self) -> None:
        result = MZParser.parse_as_ast(
            ".pool p { range 0x028000 0x028fff bogus 1 }",
            filename="t.s",
        )
        assert result.parse_error is not None
        assert "unknown pool attribute" in result.parse_error.message

    def test_to_canonical_round_trip(self) -> None:
        node = _first_of(
            ".pool p { range 0x028000 0x0280ff fill 0xea }",
            PoolAstNode,
        )
        canon = node.to_canonical()
        assert ".pool p" in canon
        assert "0x028000" in canon
        assert "fill 0xea" in canon


class TestAllocDirective:
    def test_basic(self) -> None:
        node = _first_of(
            """
            .alloc helper_fn in bank20_main {
                rts
            }
            """,
            AllocAstNode,
        )
        assert node.name == "helper_fn"
        assert node.pool_name == "bank20_main"
        assert isinstance(node.body, BlockAstNode)
        opcodes = [n for n in node.body.body if isinstance(n, OpcodeAstNode)]
        assert len(opcodes) == 1

    def test_missing_in_keyword_is_error(self) -> None:
        result = MZParser.parse_as_ast(
            ".alloc helper bank20 { rts }",
            filename="t.s",
        )
        assert result.parse_error is not None
        assert "'in'" in result.parse_error.message


class TestRelocateDirective:
    def test_basic(self) -> None:
        node = _first_of(
            """
            .relocate fn_old 0x02c000 0x02c17f into bank02_slack {
                pha
                rts
            }
            """,
            RelocateAstNode,
        )
        assert node.symbol == "fn_old"
        # old_start / old_end are now ExpressionAstNodes (evaluated at codegen).
        assert node.old_start.to_canonical() == "0x02c000"
        assert node.old_end.to_canonical() == "0x02c17f"
        assert node.pool_name == "bank02_slack"
        assert isinstance(node.body, BlockAstNode)
        opcodes = [n for n in node.body.body if isinstance(n, OpcodeAstNode)]
        assert len(opcodes) == 2

    def test_missing_into_keyword_is_error(self) -> None:
        result = MZParser.parse_as_ast(
            ".relocate fn 0x02c000 0x02c17f bank02 { rts }",
            filename="t.s",
        )
        assert result.parse_error is not None
        assert "'into'" in result.parse_error.message

    def test_to_canonical_includes_body(self) -> None:
        node = _first_of(
            """
            .relocate fn 0x02c000 0x02c17f into p {
                rts
            }
            """,
            RelocateAstNode,
        )
        canon = node.to_canonical()
        assert "rts" in canon
        assert "0x02c000" in canon
        assert "0x02c17f" in canon


class TestReclaimDirective:
    def test_basic(self) -> None:
        node = _first_of(
            ".reclaim bank02_slack 0x02c000 0x02c17f",
            ReclaimAstNode,
        )
        assert node.pool_name == "bank02_slack"
        assert node.start.to_canonical() == "0x02c000"
        assert node.end.to_canonical() == "0x02c17f"

    def test_to_canonical(self) -> None:
        node = _first_of(
            ".reclaim p 0x028000 0x0280ff",
            ReclaimAstNode,
        )
        assert node.to_canonical() == ".reclaim p 0x028000 0x0280ff"


class TestIntegration:
    def test_pool_then_alloc_then_relocate_then_reclaim(self) -> None:
        nodes = _parse(
            """
            .pool bank02_slack {
                range 0x028000 0x028fff
                fill 0xea
            }

            .alloc helper in bank02_slack {
                rts
            }

            .relocate fn_old 0x02c000 0x02c17f into bank02_slack {
                rts
            }

            .reclaim bank02_slack 0x02d000 0x02d17f
            """,
        )
        kinds = [type(n).__name__ for n in nodes if not type(n).__name__.startswith(("Comment", "Docstring"))]
        assert "PoolAstNode" in kinds
        assert "AllocAstNode" in kinds
        assert "RelocateAstNode" in kinds
        assert "ReclaimAstNode" in kinds


class TestAstToRepresentation:
    """Exercise to_representation on each pool AST node — used by AST
    inspection tools and round-trip tests."""

    def test_pool_representation(self) -> None:
        node = _first_of(
            ".pool p { range 0x028000 0x0280ff fill 0xea strategy order }",
            PoolAstNode,
        )
        kind, name, range_count, strategy = node.to_representation()
        assert kind == "pool"
        assert name == "p"
        assert range_count == 1
        assert strategy == "order"

    def test_alloc_representation(self) -> None:
        node = _first_of(
            """
            .alloc fn in p {
                rts
            }
            """,
            AllocAstNode,
        )
        kind, name, pool_name, body = node.to_representation()
        assert kind == "alloc"
        assert name == "fn"
        assert pool_name == "p"
        # body is the first slot of BlockAstNode.to_representation() —
        # the string kind "block" (the list is index 1).
        assert body == "block"

    def test_relocate_representation(self) -> None:
        node = _first_of(
            """
            .relocate fn 0x02c000 0x02c17f into p {
                rts
            }
            """,
            RelocateAstNode,
        )
        kind, symbol, pool_name, body = node.to_representation()
        assert kind == "relocate"
        assert symbol == "fn"
        assert pool_name == "p"
        assert body == "block"

    def test_reclaim_representation(self) -> None:
        node = _first_of(
            ".reclaim p 0x02c000 0x02c17f",
            ReclaimAstNode,
        )
        assert node.to_representation() == ("reclaim", "p")


class TestFluffFormat:
    def test_pool_alloc_relocate_reclaim_round_trip(self) -> None:
        from a816.formatter import A816Formatter

        src = """.pool p {
    range 0x028000 0x0280ff
    fill 0xea
    strategy pack
}
.alloc fn in p {
    rts
}
.relocate moved 0x02c000 0x02c17f into p {
    pha
    rts
}
.reclaim p 0x02d000 0x02d0ff
"""
        out = A816Formatter().format_text(src)
        # Stable across two passes — fluff format converges.
        assert A816Formatter().format_text(out) == out
        # Body opcodes appear inside the brace pair.
        for keyword in (".pool p", ".alloc fn in p", ".relocate moved", ".reclaim p"):
            assert keyword in out
        for opcode in ("rts", "pha"):
            assert opcode in out


class TestFluffFormatNestedIf:
    """Regression: ff4 reported the formatter mangled `.if {}` inside `.alloc {}`:
    flattened braces to `.endif`, glued closing brace to preceding instruction,
    stripped immediate `#` prefix from operands."""

    def test_alloc_with_nested_if_round_trips(self) -> None:
        from a816.formatter import A816Formatter

        src = """
.pool demo_pool {
    range 0x208000 0x20FFFF
    strategy order
}

.alloc demo in demo_pool {
.if FLAG_A {
    lda #0x00
    rtl
}

label_after:
    lda 0x43
    rtl

.if FLAG_B {
    .import "some_module"
}
}
"""
        out = A816Formatter().format_text(src)
        # No .endif (a816 uses braces, not keyword endif).
        assert ".endif" not in out
        # No glued braces (rtl.endif / "....endif).
        assert "rtl}" not in out
        # Immediate `#` prefix preserved.
        assert "lda #0x00" in out
        # Stable across two passes.
        assert A816Formatter().format_text(out) == out


class TestFluffFormatAllocBodyDetails:
    """Regression: ff4 Q#11 — three residual formatter bugs after Q#10:
    inline trailing comments split onto own line, `fill 0` injected
    unprompted into .pool body, blank lines stripped inside .alloc body."""

    def test_alloc_body_keeps_inline_comments_blank_lines_and_no_default_fill(self) -> None:
        from a816.formatter import A816Formatter

        src = """.pool demo {
    range 0x208000 0x20FFFF
    strategy order
}

.alloc body in demo {
    adc 0x43  ; trailing comment

foo:
    rtl

bar:
    rtl
}
"""
        out = A816Formatter().format_text(src)
        # 1. Inline trailing comment stays on instruction line.
        assert "adc 0x43" in out
        assert "; trailing comment" in out
        # No standalone-comment line for what was inline.
        comment_line_idx = next((i for i, line in enumerate(out.splitlines()) if "trailing comment" in line), -1)
        assert "adc" in out.splitlines()[comment_line_idx]

        # 2. `fill 0` not injected.
        assert "fill 0" not in out

        # 3. Blank lines between top-level labels preserved.
        lines = out.splitlines()
        foo_idx = next(i for i, line in enumerate(lines) if line.strip() == "foo:")
        bar_idx = next(i for i, line in enumerate(lines) if line.strip() == "bar:")
        # At least one blank line between foo: block and bar: block.
        between = lines[foo_idx + 1 : bar_idx]
        assert any(line.strip() == "" for line in between)

        # Round-trip converges.
        assert A816Formatter().format_text(out) == out


class TestLspKeywordCompletions:
    def test_pool_keywords_advertised(self) -> None:
        from a816.parse.scanner_states import KEYWORDS

        for kw in ("pool", "alloc", "relocate", "reclaim"):
            assert kw in KEYWORDS, f"{kw!r} not in scanner KEYWORDS — LSP completion will not surface it"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
