"""Helpers for LSP fixture-based tests."""

from __future__ import annotations

from pathlib import Path

from lsprotocol.types import (
    DidOpenTextDocumentParams,
    Position,
    TextDocumentIdentifier,
    TextDocumentItem,
    TextDocumentPositionParams,
)

from a816.lsp.server import A816LanguageServer, WorkspaceIndex

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "lsp_project"


def fixture_uri(rel_path: str) -> str:
    return (FIXTURE_ROOT / rel_path).as_uri()


def fixture_text(rel_path: str) -> str:
    return (FIXTURE_ROOT / rel_path).read_text(encoding="utf-8")


def make_did_open_params(rel_path: str) -> DidOpenTextDocumentParams:
    text = fixture_text(rel_path)
    return DidOpenTextDocumentParams(
        text_document=TextDocumentItem(uri=fixture_uri(rel_path), language_id="a816", version=1, text=text)
    )


def make_position_params(rel_path: str, line: int, character: int) -> TextDocumentPositionParams:
    return TextDocumentPositionParams(
        text_document=TextDocumentIdentifier(uri=fixture_uri(rel_path)),
        position=Position(line=line, character=character),
    )


def locate_in_fixture(rel_path: str, needle: str, occurrence: int = 0) -> tuple[int, int]:
    """Return (line, mid-character) of the n-th occurrence of needle in fixture file."""
    seen = -1
    for i, line in enumerate(fixture_text(rel_path).splitlines()):
        idx = line.find(needle)
        while idx >= 0:
            seen += 1
            if seen == occurrence:
                return i, idx + max(1, len(needle) // 2)
            idx = line.find(needle, idx + 1)
    raise ValueError(f"needle {needle!r} not found in {rel_path}")


def server_with_fixture_workspace() -> A816LanguageServer:
    """Bootstrap a server with the fixture project as its workspace.

    Pin _ensure_workspace_index to the fixture so re-indexing during
    didOpen/didChange doesn't rebase on the test runner's cwd.
    """
    server = A816LanguageServer()
    server.workspace_index = WorkspaceIndex(FIXTURE_ROOT)
    server.workspace_index.rebuild()
    server._ensure_workspace_index = lambda: server.workspace_index  # type: ignore[method-assign]
    return server
