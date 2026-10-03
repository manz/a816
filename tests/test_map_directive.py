"""`.map` directive parsing and propagation."""

from __future__ import annotations

from a816.parse.ast.nodes import AssignAstNode, MapAstNode
from a816.parse.mzparser import A816Parser

_MAP_LINE = ".map identifier=1 bank_range=0x00, 0x3f addr_range=0x8000, 0xffff mask=0x8000\n"


def test_map_stops_at_end_of_line_before_assign() -> None:
    result = A816Parser.parse_as_ast(_MAP_LINE + "ppu := 0x2100\n")
    assert result.error is None


def test_map_keeps_its_attributes_when_next_line_is_identifier() -> None:
    result = A816Parser.parse_as_ast(_MAP_LINE + "ppu := 0x2100\n")
    map_node = result.nodes[0]
    assert isinstance(map_node, MapAstNode) and map_node.args["mask"] == 0x8000


def test_map_next_line_assign_is_its_own_node() -> None:
    result = A816Parser.parse_as_ast(_MAP_LINE + "\nppu := 0x2100\n")
    assert isinstance(result.nodes[1], AssignAstNode)


def test_map_still_rejects_unknown_attribute_on_same_line() -> None:
    result = A816Parser.parse_as_ast(".map identifier=1 bogus=2\n")
    assert result.error is not None and "bogus" in result.error
