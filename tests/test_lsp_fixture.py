"""Fixture-driven LSP tests covering open/diagnostics/goto-def/symbols."""

from __future__ import annotations

from lsprotocol.types import DocumentSymbolParams, TextDocumentIdentifier, WorkspaceSymbolParams

from tests.lsp_helpers import (
    fixture_uri,
    make_did_open_params,
    make_position_params,
    server_with_fixture_workspace,
)


def test_main_opens_with_workspace_indexed() -> None:
    server = server_with_fixture_workspace()
    params = make_did_open_params("src/main.s")
    server._handle_did_open(params)

    assert params.text_document.uri in server.documents
    doc = server.documents[params.text_document.uri]
    assert "main" in doc.labels
    assert "DMA_REG" not in doc.labels  # comes via .include, not local label


def test_workspace_symbol_finds_module_label() -> None:
    server = server_with_fixture_workspace()
    server._handle_did_open(make_did_open_params("src/main.s"))

    results = server._handle_workspace_symbol(WorkspaceSymbolParams(query="vwf_render"))
    names = {sym.name for sym in results}
    assert "vwf_render" in names


def test_document_symbols_includes_helpers_scope() -> None:
    server = server_with_fixture_workspace()
    server._handle_did_open(make_did_open_params("src/main.s"))

    params = DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")))
    symbols = server._handle_document_symbols(params)
    names = {sym.name for sym in symbols}
    assert "main" in names


def test_goto_def_resolves_module_label() -> None:
    server = server_with_fixture_workspace()
    server._handle_did_open(make_did_open_params("src/main.s"))

    # main.s line where "vwf_render" appears in `jsr.l vwf_render`.
    text = (server.documents[fixture_uri("src/main.s")].content).splitlines()
    line_no = next(i for i, line in enumerate(text) if "vwf_render" in line)
    char = text[line_no].index("vwf_render") + 2  # cursor inside the word

    params = make_position_params("src/main.s", line_no, char)
    locations = server._handle_definition(params)

    assert locations is not None
    assert any("vwf.s" in loc.uri for loc in locations)
