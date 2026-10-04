"""`.istruct` canonical form and formatter round-trips."""

from __future__ import annotations

import pytest

from a816.formatter import A816Formatter
from a816.parse.ast.nodes import StructInstanceAstNode
from a816.parse.mzparser import A816Parser

_DECLS = """.struct Pt {
    byte x
    byte y
}
.struct A {
    byte[4] name
    Pt pos
    Pt[2] pts
}
"""


def _format(src: str) -> str:
    return A816Formatter().format_text(src)


@pytest.mark.parametrize(
    "src",
    [
        ".istruct A {}\n",
        '.istruct A {\n    name = "AB"\n    pos = { x = 1, y = 2 }\n    pts = [{ x = 1 }, {}]\n}\n',
        ".istruct A {\n    name = [1, 2 + 3]\n}\n",
        "\nhdr:\n    .istruct A {\n        pos = { y = 2 }\n    }\n",
    ],
)
def test_formatter_round_trips_canonical_instances(src: str) -> None:
    assert _format(_DECLS + src) == _DECLS + src


def test_formatter_splits_one_line_instance_into_fields() -> None:
    formatted = _format(_DECLS + ".istruct A { name = 'AB', pos = {x=1} }\n")
    assert formatted.endswith(".istruct A {\n    name = 'AB'\n    pos = { x = 1 }\n}\n")


def test_formatter_keeps_comments_inside_instance() -> None:
    src = '.istruct A {\n    ; leading\n    name = "AB"  ; trailing\n}\n'
    assert _format(_DECLS + src) == _DECLS + src


def test_formatter_expands_values_holding_comments() -> None:
    src = ".istruct A {\n    pts = [\n        { x = 1 },  ; first\n        {}\n    ]\n    pos = {\n        x = 1  ; px\n    }\n}\n"
    assert _format(_DECLS + src) == _DECLS + src


def test_formatter_keeps_relative_indent_inside_alloc() -> None:
    src = '.alloc t in code {\n    .istruct A {\n        name = "AB"\n    }\n    nop\n}\n'
    assert _format(_DECLS + src) == _DECLS + src


def test_canonical_of_empty_list_and_nested_struct() -> None:
    node = A816Parser.parse_as_ast(".istruct A { name = [], pos = {} }\n", "t.s").nodes[0]
    assert node.to_canonical() == ".istruct A {\n    name = []\n    pos = {}\n}"


def test_representation_lists_field_values() -> None:
    node = A816Parser.parse_as_ast('.istruct A { name = "AB", pts = [{ x = 1 }] }\n', "t.s").nodes[0]
    assert node.to_representation() == (
        "istruct",
        "A",
        (
            "struct_init",
            [
                ("field_init", "name", ("string_init", "AB")),
                ("field_init", "pts", ("list_init", [("struct_init", [("field_init", "x", ("1",))])])),
            ],
        ),
    )


def _instance(src: str) -> StructInstanceAstNode:
    node = A816Parser.parse_as_ast(src, "t.s").nodes[0]
    assert isinstance(node, StructInstanceAstNode)
    return node


def test_field_init_canonical() -> None:
    node = _instance(".istruct A { pos = { x = 1 } }\n")
    assert node.init.fields[0].to_canonical() == "pos = { x = 1 }"


def test_list_init_canonical() -> None:
    node = _instance(".istruct A { name = [1, 2] }\n")
    assert node.init.fields[0].value.to_canonical() == "[1, 2]"


def test_struct_init_canonical() -> None:
    node = _instance(".istruct A { pos = { x = 1 } }\n")
    assert node.init.to_canonical() == "{ pos = { x = 1 } }"
