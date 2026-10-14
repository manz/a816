"""Hover (nvim `K`) shows a label's docstring and a macro's parameters.

Three gaps answered "No information available" or the wrong text:
a label inside an `.alloc` body (where code lives since 1.1.0) had its
docstring dropped, a top-level label took the module docstring instead of
its own, and a macro's parameters were never read.
"""

from __future__ import annotations

from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

from a816.lsp.server import A816Document, A816LanguageServer

_URI = "file:///tmp/hover.s"
_SRC = '''"""Module."""
.macro push(size, count) {
    """Push size bytes."""
    .dw size
}
.alloc code at 0x008000 {
draw:
"""Draws the window."""
    rts
loop:
    jsr.w draw
    push(4, 1)
}
'''


def _hover(line: int, word: str, source: str = _SRC) -> str:
    server = A816LanguageServer()
    server.documents[_URI] = A816Document(_URI, source)
    column = source.splitlines()[line].index(word) + 1
    result = server._handle_hover(HoverParams(TextDocumentIdentifier(_URI), Position(line, column)))
    return "" if result is None else str(getattr(result.contents, "value", result.contents))


def test_an_alloc_body_label_shows_its_docstring() -> None:
    assert "Draws the window." in _hover(6, "draw")


def test_a_reference_to_it_shows_the_docstring_too() -> None:
    assert "Draws the window." in _hover(10, "draw")


def test_a_top_level_label_keeps_its_own_docstring() -> None:
    source = '"""Module."""\n*=0x8000\ndraw:\n"""Draws."""\n    rts\n'

    assert A816Document(_URI, source).label_docstrings == {"draw": "Draws."}


def test_the_docstring_below_a_label_wins_over_one_above() -> None:
    source = '"""Module."""\n*=0x8000\n"""Above."""\ndraw:\n"""Below."""\n    rts\n'

    assert A816Document(_URI, source).label_docstrings == {"draw": "Below."}


def test_a_docstring_above_still_documents_a_label_without_one_below() -> None:
    source = '"""Module."""\n*=0x8000\n"""Above."""\ndraw:\n    rts\n'

    assert A816Document(_URI, source).label_docstrings == {"draw": "Above."}


def test_a_macro_shows_its_parameters() -> None:
    assert _hover(11, "push").startswith("**push(size, count)**")
