"""Cover `is_placement_boundary` recursion: container nodes whose body
holds an inner placement directive must count as boundaries so UP001
and any future desugar pass don't swallow the inner alloc into the
outer `*=` body."""

from __future__ import annotations

from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    CodePositionAstNode,
    CompoundAstNode,
    IfAstNode,
    RelocateAstNode,
    ScopeAstNode,
)
from a816.parse.ast.placement import is_placement_boundary
from a816.parse.mzparser import A816Parser


def _parse(source: str) -> list[AstNode]:
    result = A816Parser.parse_as_ast(source, filename="t.s")
    assert result.parse_error is None, result.parse_error
    return list(result.nodes)


def _find[T: AstNode](nodes: list[AstNode], cls: type[T]) -> T:
    for node in nodes:
        if isinstance(node, cls):
            return node
    raise AssertionError(f"no {cls.__name__} in {nodes!r}")


class TestDirectBoundaries:
    def test_star_eq_is_boundary(self) -> None:
        nodes = _parse("*= 0x008000\n")
        assert is_placement_boundary(_find(nodes, CodePositionAstNode))

    def test_alloc_is_boundary(self) -> None:
        nodes = _parse(".alloc at 0x008000 {\n    .db 0xEA\n}\n")
        assert is_placement_boundary(_find(nodes, AllocAstNode))

    def test_relocate_is_boundary(self) -> None:
        source = ".pool p { range 0x008000 0x00ffff }\n.relocate moved 0x008100 0x008200 into p {\n    rts\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, RelocateAstNode))


class TestContainerBoundaries:
    def test_if_with_inner_star_eq_is_boundary(self) -> None:
        source = ".if 1 {\n    *= 0x009000\n    .db 0x01\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, IfAstNode))

    def test_if_with_inner_alloc_is_boundary(self) -> None:
        source = ".if 1 {\n    .alloc at 0x009000 {\n        .db 0x01\n    }\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, IfAstNode))

    def test_if_else_branch_inner_placement_is_boundary(self) -> None:
        source = ".if 0 {\n    .db 0xEA\n} else {\n    *= 0x00A000\n    .db 0x02\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, IfAstNode))

    def test_if_without_inner_placement_is_not_boundary(self) -> None:
        source = ".if 1 {\n    .db 0xEA\n    .db 0x01\n}\n"
        nodes = _parse(source)
        assert not is_placement_boundary(_find(nodes, IfAstNode))

    def test_scope_with_inner_star_eq_is_boundary(self) -> None:
        source = ".scope inner {\n    *= 0x009000\n    .db 0x01\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, ScopeAstNode))

    def test_scope_without_inner_placement_is_not_boundary(self) -> None:
        source = ".scope inner {\n    .db 0xEA\n}\n"
        nodes = _parse(source)
        assert not is_placement_boundary(_find(nodes, ScopeAstNode))

    def test_compound_with_inner_alloc_is_boundary(self) -> None:
        source = "{\n    .alloc at 0x009000 {\n        .db 0x01\n    }\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, CompoundAstNode))

    def test_compound_without_inner_placement_is_not_boundary(self) -> None:
        source = "{\n    .db 0xEA\n    .db 0x01\n}\n"
        nodes = _parse(source)
        assert not is_placement_boundary(_find(nodes, CompoundAstNode))

    def test_nested_container_deep_placement_is_boundary(self) -> None:
        source = ".if 1 {\n    .scope inner {\n        *= 0x009000\n        .db 0x01\n    }\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, IfAstNode))

    def test_scope_with_nested_compound_holding_placement(self) -> None:
        source = ".scope inner {\n    {\n        *= 0x009000\n        .db 0x01\n    }\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, ScopeAstNode))

    def test_scope_with_nested_if_holding_placement(self) -> None:
        source = ".scope inner {\n    .if 1 {\n        *= 0x009000\n        .db 0x01\n    }\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, ScopeAstNode))

    def test_scope_with_nested_if_else_holding_placement(self) -> None:
        source = ".scope inner {\n    .if 0 {\n        .db 0xEA\n    } else {\n        *= 0x00A000\n    }\n}\n"
        nodes = _parse(source)
        assert is_placement_boundary(_find(nodes, ScopeAstNode))
