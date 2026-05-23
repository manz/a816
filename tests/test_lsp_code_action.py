"""LSP `textDocument/codeAction` handler — surfaces fluff fixes."""

from __future__ import annotations

from lsprotocol.types import (
    CodeActionContext,
    CodeActionKind,
    CodeActionParams,
    Position,
    Range,
    TextDocumentIdentifier,
)

from a816.lsp.document import A816Document
from a816.lsp.server import A816LanguageServer


def _params_for(uri: str, line: int) -> CodeActionParams:
    rng = Range(start=Position(line=line, character=0), end=Position(line=line, character=1))
    return CodeActionParams(
        text_document=TextDocumentIdentifier(uri=uri),
        range=rng,
        context=CodeActionContext(diagnostics=[]),
    )


def test_code_action_returns_quickfix_for_orphan_docstring() -> None:
    src = '"""m"""\nmain:\n    rts\n    """orphan"""\n    nop\n'
    server = A816LanguageServer()
    doc = A816Document("file:///mem.s", src)
    server.documents[doc.uri] = doc
    actions = server._handle_code_action(_params_for(doc.uri, 3))
    assert actions is not None
    titles = [a.title for a in actions]
    assert any(t.startswith("DOC004:") for t in titles), titles


def test_code_action_marks_unsafe_fix_unsafe_in_title() -> None:
    src = '"""m"""\n; banner one\n; banner two\n.macro setup() {\n    ldx.w #0\n}\n'
    server = A816LanguageServer()
    doc = A816Document("file:///mem.s", src)
    server.documents[doc.uri] = doc
    # DOC005's anchor is the first comment line of the run.
    actions = server._handle_code_action(_params_for(doc.uri, 1))
    assert actions is not None
    titles = [a.title for a in actions]
    assert any(t.startswith("DOC005:") and t.endswith("(unsafe)") for t in titles), titles


def test_code_action_returns_none_when_no_hits_in_range() -> None:
    src = '"""m"""\nmain:\n    rts\n'
    server = A816LanguageServer()
    doc = A816Document("file:///mem.s", src)
    server.documents[doc.uri] = doc
    actions = server._handle_code_action(_params_for(doc.uri, 1))
    assert actions is None


def test_code_action_quickfix_kind_is_correct() -> None:
    src = '"""m"""\nmain:\n    rts\n    """orphan"""\n    nop\n'
    server = A816LanguageServer()
    doc = A816Document("file:///mem.s", src)
    server.documents[doc.uri] = doc
    actions = server._handle_code_action(_params_for(doc.uri, 3))
    assert actions is not None
    assert all(a.kind == CodeActionKind.QuickFix for a in actions)
    # Safe fix should be marked preferred so editors default to it.
    safe = next(a for a in actions if not a.title.endswith("(unsafe)"))
    assert safe.is_preferred is True
