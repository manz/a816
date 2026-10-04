"""LSP support for `TYPE[N]` struct array fields: symbol index + hover."""

from __future__ import annotations

from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

from a816.lsp.server import A816Document, A816LanguageServer

_HEADER = """
.struct Hdr {
    byte tag
    byte[21] title
    word version
}
"""


def test_lsp_indexes_array_size_symbol() -> None:
    doc = A816Document("file:///arrays.s", _HEADER)
    assert "Hdr.title.__size" in doc.symbols


def test_lsp_skips_size_symbol_for_scalars() -> None:
    doc = A816Document("file:///arrays.s", _HEADER)
    assert "Hdr.version.__size" not in doc.symbols


def _hover_body(content: str, needle: str) -> str:
    server = A816LanguageServer()
    doc = A816Document("file:///arrays_hover.s", content)
    server.documents[doc.uri] = doc
    line_index = next(i for i, line in enumerate(doc.lines) if needle in line and "lda" in line)
    col = doc.lines[line_index].find(needle)
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=doc.uri),
            position=Position(line=line_index, character=col + 1),
        )
    )
    assert hover is not None
    return hover.contents.value if hasattr(hover.contents, "value") else str(hover.contents)


def test_hover_on_array_field_shows_type() -> None:
    body = _hover_body(_HEADER + "    lda #Hdr.title\n", "Hdr.title")
    assert "byte[21]" in body


def test_hover_on_array_size_symbol_explains_it() -> None:
    body = _hover_body(_HEADER + "    lda #Hdr.title.__size\n", "Hdr.title.__size")
    assert "byte size" in body
