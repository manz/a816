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
        assert node.ranges == [(0x028000, 0x028FFF)]
        assert node.fill == 0x00
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
        assert node.ranges == [(0x028000, 0x028FFF), (0x02A100, 0x02A4C0)]
        assert node.fill == 0xEA
        assert node.strategy == "order"

    def test_empty_pool_is_error(self) -> None:
        result = MZParser.parse_as_ast(".pool empty {}", filename="t.s")
        assert result.parse_error is not None
        assert "no ranges" in result.parse_error.message

    def test_fill_out_of_byte_range_is_error(self) -> None:
        result = MZParser.parse_as_ast(
            ".pool p { range 0x028000 0x028fff fill 0x100 }",
            filename="t.s",
        )
        assert result.parse_error is not None
        assert "out of byte range" in result.parse_error.message

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
            .relocate fn_old into bank02_slack {
                pha
                rts
            }
            """,
            RelocateAstNode,
        )
        assert node.symbol == "fn_old"
        assert node.pool_name == "bank02_slack"
        assert isinstance(node.body, BlockAstNode)
        opcodes = [n for n in node.body.body if isinstance(n, OpcodeAstNode)]
        assert len(opcodes) == 2

    def test_missing_into_keyword_is_error(self) -> None:
        result = MZParser.parse_as_ast(
            ".relocate fn bank02 { rts }",
            filename="t.s",
        )
        assert result.parse_error is not None
        assert "'into'" in result.parse_error.message


class TestReclaimDirective:
    def test_basic(self) -> None:
        node = _first_of(
            ".reclaim bank02_slack 0x02c000 0x02c17f",
            ReclaimAstNode,
        )
        assert node.pool_name == "bank02_slack"
        assert node.start == 0x02C000
        assert node.end == 0x02C17F

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

            .relocate fn_old into bank02_slack {
                rts
            }

            .reclaim bank02_slack 0x02c000 0x02c17f
            """,
        )
        kinds = [type(n).__name__ for n in nodes if not type(n).__name__.startswith(("Comment", "Docstring"))]
        assert "PoolAstNode" in kinds
        assert "AllocAstNode" in kinds
        assert "RelocateAstNode" in kinds
        assert "ReclaimAstNode" in kinds


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
