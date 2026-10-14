"""Goto-definition on a struct member lands on the field's own line.

It found nothing: the handler looked up the bare word under the cursor
(`ptr` of `Entry.ptr`) while members are indexed dotted, and the dotted
entries pointed at the `.struct` line anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lsprotocol.types import Position, TextDocumentIdentifier, TextDocumentPositionParams

from a816.lsp.server import A816Document, A816LanguageServer
from a816.lsp.workspace import WorkspaceIndex

_URI = "file:///tmp/members.s"
_SRC = '''"""Module."""
.struct Entry {
    byte id
    word ptr
}
.struct Outer {
    Entry head
}
.pool st { range 0x7e2000 0x7e2fff bss }
.reserve thing as Entry in st
view := (0x7e3000 as Entry)
.scope sc {
    """S."""
    fd = 1
}
.alloc code at 0x008000 {
    lda.w #Entry.ptr
    lda.w thing.ptr
    lda.w view.ptr
    lda.w (0x7e0000 as Entry).ptr
    lda.w #Outer.head.ptr
    .istruct Entry {
        id = 1
        ptr = 2
    }
    lda.w #sc.fd
    lda.w #Entry
    lda.w nothing.ptr
}
'''
_PTR_LINE = 3


def _definition(line: int, word: str) -> int | None:
    server = A816LanguageServer()
    server.documents[_URI] = A816Document(_URI, _SRC)
    column = server.documents[_URI].lines[line].rindex(word) + 1
    result = server._handle_definition(TextDocumentPositionParams(TextDocumentIdentifier(_URI), Position(line, column)))
    return result[0].range.start.line if result else None


@pytest.mark.parametrize(
    ("line", "spelling"),
    [
        (16, "Type.field"),
        (17, "reserve.field"),
        (18, "typed bind.field"),
        (19, "cast.field"),
        (20, "nested Type.a.b"),
        (23, ".istruct field"),
    ],
)
def test_a_member_lands_on_its_field(line: int, spelling: str) -> None:
    assert _definition(line, "ptr") == _PTR_LINE, spelling


def test_the_type_name_still_lands_on_the_struct() -> None:
    assert _definition(16, "Entry") == 1


def test_a_scope_member_lands_on_its_definition() -> None:
    assert _definition(25, "fd") == 13


def test_a_path_whose_head_holds_no_struct_finds_nothing() -> None:
    assert _definition(27, "ptr") is None


def test_a_member_of_a_struct_in_another_file(tmp_path: Path) -> None:
    (tmp_path / "types.s").write_text('"""Types."""\n.struct Entry {\n    byte id\n    word ptr\n}\n')
    main = tmp_path / "main.s"
    main.write_text('"""Main."""\n.import "types"\n.alloc code at 0x008000 {\n    lda.w #Entry.ptr\n}\n')
    server = A816LanguageServer()
    server.workspace_index = WorkspaceIndex(tmp_path)
    server.workspace_index.rebuild()
    server._ensure_workspace_index = lambda: server.workspace_index  # type: ignore[method-assign]
    uri = main.as_uri()
    server.documents[uri] = A816Document(uri, main.read_text())
    column = server.documents[uri].lines[3].rindex("ptr") + 1

    result = server._handle_definition(TextDocumentPositionParams(TextDocumentIdentifier(uri), Position(3, column)))

    assert result is not None
    assert (Path(result[0].uri.removeprefix("file://")).name, result[0].range.start.line) == ("types.s", 3)
