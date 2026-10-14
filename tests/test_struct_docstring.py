"""A `.struct` documents itself with a docstring as its first statement,
as a `.scope` or `.macro` does.

It was a parse error (E0101 "expected identifier, found docstring"), so
Bahamut Lagoon's new table structs had nowhere to say what they hold.
"""

from __future__ import annotations

from pathlib import Path

from a816.formatter import A816Formatter
from a816.lsp.document import A816Document
from a816.parse.ast.nodes import StructAstNode
from a816.parse.mzparser import A816Parser
from a816.program import Program

_ONE_LINE = '.struct Entry {\n    """One baked entry."""\n    byte id\n    word ptr\n}\n'
_MULTI_LINE = '.struct Entry {\n    """\n    One baked entry.\n\n    Read by small_vwf.\n    """\n\n    byte id\n    word ptr\n}\n'


def _struct(source: str) -> StructAstNode:
    result = A816Parser.parse_as_ast(source, filename="t.s")
    assert result.parse_error is None, result.parse_error
    (node,) = [node for node in result.nodes if isinstance(node, StructAstNode)]
    return node


def test_the_docstring_documents_the_struct() -> None:
    assert _struct(_ONE_LINE).docstring == "One baked entry."


def test_the_docstring_is_not_a_field() -> None:
    assert _struct(_ONE_LINE).fields == (("id", "byte"), ("ptr", "word"))


def test_a_struct_without_one_has_none() -> None:
    assert _struct(".struct Entry {\n    byte id\n}\n").docstring is None


def test_a_one_line_docstring_formats_unchanged() -> None:
    assert A816Formatter().format_text(_ONE_LINE) == _ONE_LINE


def test_a_multi_line_docstring_and_its_gap_format_unchanged() -> None:
    assert A816Formatter().format_text(_MULTI_LINE) == _MULTI_LINE


def test_hover_on_the_struct_name_shows_it() -> None:
    doc = A816Document("file://t.s", _MULTI_LINE)

    assert doc.scope_docstrings["entry"] == "One baked entry.\n\nRead by small_vwf."


def test_a_documented_struct_builds(tmp_path: Path) -> None:
    (tmp_path / "m.s").write_text(_ONE_LINE + ".alloc code at 0x008000 {\n    .db sizeof(Entry)\n}\n")

    assert Program().assemble_as_object(str(tmp_path / "m.s"), tmp_path / "m.o") == 0
