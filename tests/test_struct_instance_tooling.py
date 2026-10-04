"""`.istruct` in fluff and the language server."""

from __future__ import annotations

from pathlib import Path

from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

from a816.fluff import lint_file
from a816.lsp.server import A816Document, A816LanguageServer

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


def test_s001_flags_istruct_of_unknown_type(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text('"""Module."""\n.istruct Nope {}\n', encoding="utf-8")
    assert "S001" in [d.code for d in lint_file(src)]


def test_s001_accepts_istruct_of_declared_type(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text('"""Module."""\n' + _DECLS + ".istruct A {}\n", encoding="utf-8")
    assert "S001" not in [d.code for d in lint_file(src)]


def test_s001_sees_casts_inside_initializers(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text(
        '"""Module."""\n' + _DECLS + ".istruct A { pos = { x = (0 as Nope) } }\n",
        encoding="utf-8",
    )
    assert "S001" in [d.code for d in lint_file(src)]


def _tokens(content: str) -> list[dict[str, int]]:
    server = A816LanguageServer()
    doc = A816Document("file:///inst.s", content)
    return server._extract_semantic_tokens_from_ast(doc)


def _token_types_on_line(content: str, line: int) -> list[int]:
    return [t["type"] for t in sorted(_tokens(content), key=lambda t: t["char"]) if t["line"] == line]


def test_semantic_tokens_cover_istruct_line() -> None:
    content = _DECLS + '.istruct A { name = "AB", pos = { x = 1 } }\n'
    # directive, type, field, string, field, field, number
    assert _token_types_on_line(content, 9) == [7, 8, 6, 4, 6, 6, 3]


def _hover(content: str, line_index: int, needle: str) -> str:
    server = A816LanguageServer()
    doc = A816Document("file:///inst_hover.s", content)
    server.documents[doc.uri] = doc
    col = doc.lines[line_index].find(needle)
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=doc.uri),
            position=Position(line=line_index, character=col),
        )
    )
    if hover is None:
        return ""
    return hover.contents.value if hasattr(hover.contents, "value") else str(hover.contents)


def test_hover_on_initializer_field_shows_its_type() -> None:
    body = _hover(_DECLS + ".istruct A {\n    name = 'AB'\n}\n", 10, "name")
    assert "byte[4]" in body


def test_hover_on_nested_initializer_field_resolves_nested_struct() -> None:
    body = _hover(_DECLS + ".istruct A {\n    pts = [{ y = 1 }]\n}\n", 10, "y =")
    assert "Pt.y" in body


def test_hover_on_unknown_initializer_field_falls_through() -> None:
    assert _hover(_DECLS + ".istruct A {\n    zz = 1\n}\n", 10, "zz") == ""
