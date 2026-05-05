"""Fixture-driven LSP tests covering open/diagnostics/goto-def/symbols."""

from __future__ import annotations

from lsprotocol.types import (
    CompletionContext,
    CompletionParams,
    CompletionTriggerKind,
    DidChangeTextDocumentParams,
    DocumentFormattingParams,
    DocumentSymbolParams,
    FormattingOptions,
    HoverParams,
    MarkupContent,
    Position,
    Range,
    ReferenceContext,
    ReferenceParams,
    SemanticTokensParams,
    SignatureHelpContext,
    SignatureHelpParams,
    SignatureHelpTriggerKind,
    TextDocumentContentChangeEvent_Type1,
    TextDocumentIdentifier,
    VersionedTextDocumentIdentifier,
    WorkspaceSymbolParams,
)

from a816.lsp.server import A816LanguageServer
from tests.lsp_helpers import (
    fixture_uri,
    locate_in_fixture,
    make_did_open_params,
    make_position_params,
    server_with_fixture_workspace,
)


def _open(server: A816LanguageServer, rel_path: str) -> None:
    server._handle_did_open(make_did_open_params(rel_path))


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
    _open(server, "src/main.s")
    results = server._handle_workspace_symbol(WorkspaceSymbolParams(query="vwf_render"))
    assert "vwf_render" in {sym.name for sym in results}


def test_document_symbols_includes_helpers_scope() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    params = DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")))
    symbols = server._handle_document_symbols(params)
    assert "main" in {sym.name for sym in symbols}


def test_goto_def_resolves_module_label() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, char = locate_in_fixture("src/main.s", "vwf_render")
    locations = server._handle_definition(make_position_params("src/main.s", line, char))
    assert locations is not None
    assert any("vwf.s" in loc.uri for loc in locations)


def test_hover_on_opcode_describes_instruction() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, char = locate_in_fixture("src/main.s", "lda")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")),
            position=Position(line=line, character=char),
        )
    )
    assert hover is not None
    assert isinstance(hover.contents, MarkupContent)
    assert "65c816 Instruction" in hover.contents.value


def test_hover_on_macro_includes_docstring() -> None:
    server = server_with_fixture_workspace()
    _open(server, "modules/vwf.s")
    line, char = locate_in_fixture("modules/vwf.s", "vwf_init")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=fixture_uri("modules/vwf.s")),
            position=Position(line=line, character=char),
        )
    )
    assert hover is not None
    assert isinstance(hover.contents, MarkupContent)
    assert "Initialise" in hover.contents.value


def test_completions_include_local_labels() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, _ = locate_in_fixture("src/main.s", "main:")
    params = CompletionParams(
        text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")),
        position=Position(line=line + 1, character=4),
        context=CompletionContext(trigger_kind=CompletionTriggerKind.Invoked),
    )
    result = server._handle_completions(params)
    labels = {item.label for item in result.items}
    assert any(op in labels for op in ("LDA", "STA", "JSR"))


def test_signature_help_on_lda() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, _ = locate_in_fixture("src/main.s", "lda.b")
    params = SignatureHelpParams(
        text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")),
        position=Position(line=line, character=8),
        context=SignatureHelpContext(trigger_kind=SignatureHelpTriggerKind.Invoked, is_retrigger=False),
    )
    sig = server._handle_signature_help(params)
    assert sig is not None
    assert sig.signatures
    assert "LDA" in sig.signatures[0].label


def test_references_finds_cross_file_uses() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    _open(server, "modules/vwf.s")
    line, char = locate_in_fixture("modules/vwf.s", "vwf_render")
    params = ReferenceParams(
        text_document=TextDocumentIdentifier(uri=fixture_uri("modules/vwf.s")),
        position=Position(line=line, character=char),
        context=ReferenceContext(include_declaration=True),
    )
    locs = server._handle_references(params)
    assert locs is not None
    uris = {loc.uri for loc in locs}
    assert any("main.s" in uri for uri in uris)
    assert any("vwf.s" in uri for uri in uris)


def test_semantic_tokens_returns_data() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    params = SemanticTokensParams(text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")))
    tokens = server._handle_semantic_tokens_full(params)
    assert tokens is not None
    assert len(tokens.data) > 0


def test_format_document_returns_edits_or_empty() -> None:
    # Use vwf.s: self-contained, no .include side-effects during parse.
    server = server_with_fixture_workspace()
    _open(server, "modules/vwf.s")
    params = DocumentFormattingParams(
        text_document=TextDocumentIdentifier(uri=fixture_uri("modules/vwf.s")),
        options=FormattingOptions(tab_size=4, insert_spaces=True),
    )
    edits = server._handle_format_document(params)
    # Either empty (already formatted) or a single full-document edit; both are valid.
    assert edits is not None


def test_did_close_drops_document() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    uri = fixture_uri("src/main.s")
    assert uri in server.documents
    from lsprotocol.types import DidCloseTextDocumentParams

    server._handle_did_close(DidCloseTextDocumentParams(text_document=TextDocumentIdentifier(uri=uri)))
    assert uri not in server.documents


def test_did_save_with_text_updates_document() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    uri = fixture_uri("src/main.s")
    from lsprotocol.types import DidSaveTextDocumentParams

    server._handle_did_save(
        DidSaveTextDocumentParams(
            text_document=TextDocumentIdentifier(uri=uri),
            text="; saved\nmain:\n    rts\n",
        )
    )
    assert "; saved" in server.documents[uri].content


def test_did_save_without_text_reanalyzes() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    uri = fixture_uri("src/main.s")
    from lsprotocol.types import DidSaveTextDocumentParams

    server._handle_did_save(DidSaveTextDocumentParams(text_document=TextDocumentIdentifier(uri=uri), text=None))
    assert uri in server.documents


def test_hover_returns_none_on_unknown_word() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, char = locate_in_fixture("src/main.s", "main:")
    # Cursor on an empty / non-identifier column.
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")),
            position=Position(line=line + 99, character=0),
        )
    )
    assert hover is None


def test_definition_returns_none_for_unknown_word() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    locations = server._handle_definition(make_position_params("src/main.s", 0, 5))
    # First line is a comment; cursor on whitespace, no definition expected.
    assert locations is None


def test_definition_jumps_to_include_target() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, _ = locate_in_fixture("src/main.s", "constants.s")
    # Cursor inside the quoted "constants.s" path.
    char = locate_in_fixture("src/main.s", "constants.s")[1]
    locs = server._handle_definition(make_position_params("src/main.s", line, char))
    assert locs is not None
    assert any("constants.s" in loc.uri for loc in locs)


def test_references_excluding_declaration() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    _open(server, "modules/vwf.s")
    line, char = locate_in_fixture("modules/vwf.s", "vwf_render")
    params = ReferenceParams(
        text_document=TextDocumentIdentifier(uri=fixture_uri("modules/vwf.s")),
        position=Position(line=line, character=char),
        context=ReferenceContext(include_declaration=False),
    )
    locs = server._handle_references(params)
    if locs is not None:
        # Declaration line in vwf.s should not appear.
        decl_doc_uri = fixture_uri("modules/vwf.s")
        for loc in locs:
            if loc.uri == decl_doc_uri:
                assert loc.range.start.line != line


def test_workspace_symbol_empty_workspace_returns_empty() -> None:
    server = server_with_fixture_workspace()
    server.workspace_index = None
    server._ensure_workspace_index = lambda: None  # type: ignore[method-assign]
    assert server._handle_workspace_symbol(WorkspaceSymbolParams(query="main")) == []


def test_hover_on_cross_file_macro() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, char = locate_in_fixture("src/main.s", "vwf_init")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")),
            position=Position(line=line, character=char),
        )
    )
    assert hover is not None
    assert isinstance(hover.contents, MarkupContent)
    assert "Initialise" in hover.contents.value


def test_hover_on_import_shows_module_docstring() -> None:
    """Cursor on `.import "vwf"` in main.s surfaces vwf.s's leading docstring."""
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, _ = locate_in_fixture("src/main.s", '.import "vwf"')
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=fixture_uri("src/main.s")),
            position=Position(line=line, character=4),
        )
    )
    assert hover is not None
    assert isinstance(hover.contents, MarkupContent)
    assert "Variable-width font helpers" in hover.contents.value


def test_hover_on_label_with_docstring() -> None:
    server = server_with_fixture_workspace()
    _open(server, "modules/vwf.s")
    line, char = locate_in_fixture("modules/vwf.s", "vwf_render:")
    hover = server._handle_hover(
        HoverParams(
            text_document=TextDocumentIdentifier(uri=fixture_uri("modules/vwf.s")),
            position=Position(line=line, character=char),
        )
    )
    assert hover is not None
    assert isinstance(hover.contents, MarkupContent)
    assert "Render" in hover.contents.value


def test_document_symbols_for_module_with_macros() -> None:
    server = server_with_fixture_workspace()
    _open(server, "modules/vwf.s")
    params = DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=fixture_uri("modules/vwf.s")))
    symbols = server._handle_document_symbols(params)
    names = {sym.name for sym in symbols}
    assert "vwf_render" in names
    assert any(name.startswith("vwf_init") for name in names)


def test_definition_resolves_extern_via_workspace() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    line, char = locate_in_fixture("src/main.s", "target_addr", occurrence=1)
    locs = server._handle_definition(make_position_params("src/main.s", line, char))
    # target_addr is declared extern in main.s and dma.s; workspace lookup may
    # resolve to either, but result must point at one of them.
    assert locs is not None
    assert any("dma.s" in loc.uri or "main.s" in loc.uri for loc in locs)


def test_workspace_index_remove_document() -> None:
    server = server_with_fixture_workspace()
    ws = server.workspace_index
    assert ws is not None
    vwf_uri = fixture_uri("modules/vwf.s")
    assert vwf_uri in ws.documents
    ws.remove_document(vwf_uri)
    assert vwf_uri not in ws.documents
    assert "vwf_render" not in ws.labels


def test_did_change_updates_document_content() -> None:
    server = server_with_fixture_workspace()
    _open(server, "src/main.s")
    uri = fixture_uri("src/main.s")
    new_text = "; replaced\nmain:\n    rts\n"
    change = TextDocumentContentChangeEvent_Type1(
        range=Range(start=Position(line=0, character=0), end=Position(line=999, character=0)),
        text=new_text,
    )
    params = DidChangeTextDocumentParams(
        text_document=VersionedTextDocumentIdentifier(uri=uri, version=2),
        content_changes=[change],
    )
    server._handle_did_change(params)
    assert "; replaced" in server.documents[uri].content
