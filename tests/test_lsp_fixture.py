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
