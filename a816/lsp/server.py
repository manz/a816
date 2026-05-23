import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from lsprotocol.types import (
    CompletionList,
    CompletionParams,
    Diagnostic,
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentFormattingParams,
    DocumentRangeFormattingParams,
    DocumentSymbol,
    DocumentSymbolParams,
    Hover,
    HoverParams,
    Location,
    MessageType,
    Position,
    PrepareRenameParams,
    Range,
    ReferenceParams,
    RenameParams,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
    ShowMessageParams,
    SignatureHelp,
    SignatureHelpParams,
    SignatureInformation,
    SymbolInformation,
    SymbolKind,
    TextDocumentContentChangePartial,
    TextDocumentContentChangeWholeDocument,
    TextDocumentPositionParams,
    TextEdit,
    WorkspaceEdit,
    WorkspaceSymbolParams,
)
from lsprotocol.types import (
    FormattingOptions as LSPFormattingOptions,
)
from pygls.lsp.server import LanguageServer

from a816.cpu.cpu_65c816 import AddressingMode, snes_opcode_table
from a816.exceptions import FormattingError
from a816.formatter import A816Formatter, FormattingOptions
from a816.lsp.document import A816Document
from a816.lsp.handlers.completions import CompletionsMixin
from a816.lsp.handlers.hover import HoverMixin
from a816.lsp.handlers.tokens import TokensMixin
from a816.lsp.mask import build_code_mask
from a816.lsp.workspace import WorkspaceIndex
from a816.util import uri_to_path

logger = logging.getLogger(__name__)

FILE_URI_PREFIX = "file://"

__all__ = ["A816Document", "A816LanguageServer", "WorkspaceIndex", "lsp_main"]


class A816LanguageServer(CompletionsMixin, HoverMixin, TokensMixin):
    """Enhanced LSP server for a816 assembly language"""

    def __init__(self) -> None:
        self.server = LanguageServer("a816-language-server", "v1.0")
        try:
            self.server.server_capabilities.workspace_symbol_provider = True
        except AttributeError:
            pass
        self.documents: dict[str, A816Document] = {}
        self.formatter = A816Formatter()
        self.workspace_index: WorkspaceIndex | None = None
        self._setup_handlers()

        # Cache instruction completions
        self._opcode_completions = self._build_opcode_completions()
        self._keyword_completions = self._build_keyword_completions()
        self._register_completions = self._build_register_completions()

    def _setup_handlers(self) -> None:
        """Register all LSP handlers; thin wrappers delegate to _handle_* methods."""

        @self.server.feature("textDocument/didOpen")
        async def did_open(ls: LanguageServer, params: DidOpenTextDocumentParams) -> None:
            await self._handle_did_open(params)

        @self.server.feature("textDocument/didChange")
        async def did_change(ls: LanguageServer, params: DidChangeTextDocumentParams) -> None:
            await self._handle_did_change(params)

        @self.server.feature("textDocument/didClose")
        async def did_close(ls: LanguageServer, params: DidCloseTextDocumentParams) -> None:
            self._handle_did_close(params)

        @self.server.feature("textDocument/didSave")
        async def did_save(ls: LanguageServer, params: DidSaveTextDocumentParams) -> None:
            await self._handle_did_save(params)

        @self.server.feature("textDocument/completion")
        async def completions(ls: LanguageServer, params: CompletionParams) -> CompletionList:
            return self._handle_completions(params)

        @self.server.feature("textDocument/hover")
        async def hover(ls: LanguageServer, params: HoverParams) -> Hover | None:
            return self._handle_hover(params)

        @self.server.feature("textDocument/documentSymbol")
        async def document_symbols(ls: LanguageServer, params: DocumentSymbolParams) -> list[DocumentSymbol]:
            return self._handle_document_symbols(params)

        @self.server.feature("textDocument/definition")
        async def go_to_definition(ls: LanguageServer, params: TextDocumentPositionParams) -> list[Location] | None:
            return self._handle_definition(params)

        @self.server.feature("workspace/symbol")
        async def workspace_symbol(ls: LanguageServer, params: WorkspaceSymbolParams) -> list[SymbolInformation]:
            return self._handle_workspace_symbol(params)

        @self.server.feature("textDocument/references")
        async def find_references(ls: LanguageServer, params: ReferenceParams) -> list[Location] | None:
            return self._handle_references(params)

        @self.server.feature("textDocument/prepareRename")
        async def prepare_rename(ls: LanguageServer, params: PrepareRenameParams) -> Range | None:
            return self._handle_prepare_rename(params)

        @self.server.feature("textDocument/rename")
        async def rename_symbol(ls: LanguageServer, params: RenameParams) -> WorkspaceEdit | None:
            return self._handle_rename(params)

        @self.server.feature("textDocument/signatureHelp")
        async def signature_help(ls: LanguageServer, params: SignatureHelpParams) -> SignatureHelp | None:
            return self._handle_signature_help(params)

        @self.server.feature(
            "textDocument/semanticTokens/full",
            SemanticTokensLegend(
                token_types=[
                    "keyword",  # 0 - opcodes
                    "function",  # 1 - labels
                    "comment",  # 2 - comments
                    "number",  # 3 - numbers
                    "string",  # 4 - strings
                    "operator",  # 5 - operators
                    "variable",  # 6 - registers
                    "macro",  # 7 - directives
                    "type",  # 8 - size specifiers
                ],
                token_modifiers=[],
            ),
        )
        async def semantic_tokens_full(ls: LanguageServer, params: SemanticTokensParams) -> SemanticTokens | None:
            return self._handle_semantic_tokens_full(params)

        @self.server.feature("textDocument/formatting")
        async def format_document(ls: LanguageServer, params: DocumentFormattingParams) -> list[TextEdit] | None:
            return self._handle_format_document(params)

        @self.server.feature("textDocument/rangeFormatting")
        async def format_range(ls: LanguageServer, params: DocumentRangeFormattingParams) -> list[TextEdit] | None:
            return self._handle_format_document(
                DocumentFormattingParams(text_document=params.text_document, options=params.options)
            )

    async def _handle_did_open(self, params: DidOpenTextDocumentParams) -> None:
        workspace = self._ensure_workspace_index()
        include_paths = workspace.include_paths if workspace else []
        # Heavy: scanner + parser + symbol extraction. Off the event loop.
        doc = await asyncio.to_thread(
            A816Document,
            params.text_document.uri,
            params.text_document.text,
            include_paths,
        )
        self.documents[params.text_document.uri] = doc
        if workspace:
            await asyncio.to_thread(workspace.replace_document, doc)
        self._publish_diagnostics_for(doc, workspace)

    def _publish_diagnostics_for(self, doc: A816Document, workspace: WorkspaceIndex | None) -> None:
        """Push per-doc + cross-doc diagnostics. Single funnel so every
        update path goes through the same merge."""
        cross_doc = workspace.undeclared_pool_diagnostics(doc) if workspace else []
        self._publish_diagnostics(doc.uri, [*doc.diagnostics, *cross_doc])

    def _apply_content_changes(self, doc: A816Document, changes: list[Any]) -> str:
        current_content = doc.content
        for change in changes:
            if isinstance(change, TextDocumentContentChangeWholeDocument):
                current_content = change.text
                logger.debug("Full document replacement")
            else:
                logger.debug(f"Incremental change at {change.range.start.line}:{change.range.start.character}")
                current_content = self._apply_text_change(current_content, change)
        return current_content

    async def _handle_did_change(self, params: DidChangeTextDocumentParams) -> None:
        doc = self.documents.get(params.text_document.uri)
        if not doc or not params.content_changes:
            return
        new_content = self._apply_content_changes(doc, list(params.content_changes))
        # Re-parse off the event loop so large-file edits don't block.
        await asyncio.to_thread(doc.update_content, new_content)
        workspace = self._ensure_workspace_index()
        if workspace:
            await asyncio.to_thread(workspace.replace_document, doc)
        self._publish_diagnostics_for(doc, workspace)
        try:
            self.server.workspace_semantic_tokens_refresh(None)
        except (AttributeError, RuntimeError, TypeError) as e:
            logger.debug(f"Could not refresh semantic tokens: {e}")

    def _handle_did_close(self, params: DidCloseTextDocumentParams) -> None:
        self.documents.pop(params.text_document.uri, None)
        workspace = self._ensure_workspace_index()
        if workspace:
            workspace.reload_document_from_disk(params.text_document.uri)

    async def _handle_did_save(self, params: DidSaveTextDocumentParams) -> None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return
        if params.text is not None:
            await asyncio.to_thread(doc.update_content, params.text)
        else:
            await asyncio.to_thread(doc.analyze)
        workspace = self._ensure_workspace_index()
        if workspace:
            await asyncio.to_thread(workspace.replace_document, doc)
        self._publish_diagnostics_for(doc, workspace)

    @staticmethod
    def _doc_symbol_range(pos: Position, name_len: int) -> Range:
        return Range(start=pos, end=Position(line=pos.line, character=pos.character + name_len))

    def _doc_symbols_for_labels(self, doc: A816Document, uri: str) -> list[DocumentSymbol]:
        return [
            DocumentSymbol(
                name=label,
                kind=SymbolKind.Function,
                range=self._doc_symbol_range(pos, len(label)),
                selection_range=self._doc_symbol_range(pos, len(label)),
            )
            for label, (pos, file_uri) in doc.labels.items()
            if file_uri == uri
        ]

    def _doc_symbols_for_macros(self, doc: A816Document, uri: str) -> list[DocumentSymbol]:
        symbols: list[DocumentSymbol] = []
        for macro_name, (pos, file_uri) in doc.macros.items():
            if file_uri != uri:
                continue
            param_info = (
                self._format_macro_params(doc.macro_params.get(macro_name, []))
                if macro_name in doc.macro_params
                else ""
            )
            doc_text = doc.macro_docstrings.get(macro_name.lower())
            detail = doc_text.splitlines()[0] if doc_text else None
            symbols.append(
                DocumentSymbol(
                    name=f"{macro_name}{param_info}",
                    kind=SymbolKind.Method,
                    range=self._doc_symbol_range(pos, len(macro_name)),
                    selection_range=self._doc_symbol_range(pos, len(macro_name)),
                    detail=detail,
                )
            )
        return symbols

    def _doc_symbols_for_symbols(self, doc: A816Document, uri: str) -> list[DocumentSymbol]:
        return [
            DocumentSymbol(
                name=symbol,
                kind=SymbolKind.Variable,
                range=self._doc_symbol_range(pos, len(symbol)),
                selection_range=self._doc_symbol_range(pos, len(symbol)),
            )
            for symbol, (pos, file_uri) in doc.symbols.items()
            if file_uri == uri
        ]

    def _doc_symbols_for_pools(self, doc: A816Document, uri: str) -> list[DocumentSymbol]:
        return [
            DocumentSymbol(
                name=name,
                kind=SymbolKind.Namespace,
                range=self._doc_symbol_range(pos, len(name)),
                selection_range=self._doc_symbol_range(pos, len(name)),
                detail=".pool",
            )
            for name, (pos, file_uri) in doc.pools.items()
            if file_uri == uri
        ]

    def _doc_symbols_for_allocs(self, doc: A816Document, uri: str) -> list[DocumentSymbol]:
        return [
            DocumentSymbol(
                name=name,
                kind=SymbolKind.Function,
                range=self._doc_symbol_range(pos, len(name)),
                selection_range=self._doc_symbol_range(pos, len(name)),
                detail=".alloc / .relocate",
            )
            for name, (pos, file_uri) in doc.allocs.items()
            if file_uri == uri
        ]

    def _handle_document_symbols(self, params: DocumentSymbolParams) -> list[DocumentSymbol]:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return []
        uri = params.text_document.uri
        return [
            *self._doc_symbols_for_labels(doc, uri),
            *self._doc_symbols_for_macros(doc, uri),
            *self._doc_symbols_for_symbols(doc, uri),
            *self._doc_symbols_for_pools(doc, uri),
            *self._doc_symbols_for_allocs(doc, uri),
        ]

    @staticmethod
    def _location_for(pos: Position, file_uri: str, word_len: int) -> Location:
        return Location(
            uri=file_uri,
            range=Range(start=pos, end=Position(line=pos.line, character=pos.character + word_len)),
        )

    def _local_definition(self, doc: A816Document, word: str) -> Location | None:
        for container in (doc.labels, doc.macros, doc.symbols, doc.pools, doc.allocs):
            if word in container:
                pos, file_uri = container[word]
                return self._location_for(pos, file_uri, len(word))
        return None

    @staticmethod
    def _workspace_definition(workspace: WorkspaceIndex, word: str) -> Location | None:
        location = workspace.get_label_location(word) or workspace.get_symbol_location(word)
        if location:
            return location
        macro_location = workspace.get_macro_location(word)
        return macro_location[0] if macro_location else None

    def _handle_definition(self, params: TextDocumentPositionParams) -> list[Location] | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num]
        word_start, word_end = self._word_span(line, params.position.character)
        if word_start >= word_end:
            return None
        word = line[word_start:word_end]

        if word not in doc.externs:
            local = self._local_definition(doc, word)
            if local:
                return [local]
        workspace = self._ensure_workspace_index()
        if workspace:
            ws_location = self._workspace_definition(workspace, word)
            if ws_location:
                return [ws_location]
        include_location = self._check_include_directive(
            doc, line_num, params.position.character, params.text_document.uri
        )
        return [include_location] if include_location else None

    def _workspace_symbol_entries(
        self,
        items: dict[str, tuple[Position, str]],
        kind: SymbolKind,
        query: str,
        workspace: WorkspaceIndex,
    ) -> list[SymbolInformation]:
        results: list[SymbolInformation] = []
        for name, (position, uri) in items.items():
            if query and query not in name.lower():
                continue
            end = Position(line=position.line, character=position.character + len(name))
            results.append(
                SymbolInformation(
                    name=name,
                    kind=kind,
                    location=Location(uri=uri, range=Range(start=position, end=end)),
                    container_name=self._workspace_container_name(uri, workspace),
                )
            )
        return results

    def _handle_workspace_symbol(self, params: WorkspaceSymbolParams) -> list[SymbolInformation]:
        workspace = self._ensure_workspace_index()
        if not workspace:
            return []
        query = (params.query or "").strip().lower()
        results = self._workspace_symbol_entries(workspace.labels, SymbolKind.Function, query, workspace)
        results.extend(self._workspace_symbol_entries(workspace.symbols, SymbolKind.Variable, query, workspace))
        results.extend(self._workspace_symbol_entries(workspace.macros, SymbolKind.Method, query, workspace))
        results.extend(self._workspace_symbol_entries(workspace.pools, SymbolKind.Namespace, query, workspace))
        results.extend(self._workspace_symbol_entries(workspace.allocs, SymbolKind.Function, query, workspace))
        return results[:100]

    @staticmethod
    def _refs_in_document(
        document: A816Document, uri: str, pattern: re.Pattern[str], seen: set[tuple[str, int, int]]
    ) -> list[Location]:
        out: list[Location] = []
        masks = build_code_mask(document.lines)
        for i, doc_line in enumerate(document.lines):
            mask = masks[i]
            for match in pattern.finditer(doc_line):
                start, end = match.start(), match.end()
                if any(not mask[j] for j in range(start, end)):
                    continue
                key = (uri, i, start)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Location(
                        uri=uri,
                        range=Range(
                            start=Position(line=i, character=start),
                            end=Position(line=i, character=end),
                        ),
                    )
                )
        return out

    @staticmethod
    def _scan_definitions(
        stores: tuple[dict[str, tuple[Position, str]], ...],
        word: str,
        defs: set[tuple[str, int, int]],
        force_uri: str | None = None,
    ) -> None:
        for store in stores:
            for name, (position, store_uri) in store.items():
                if name == word:
                    defs.add((force_uri or store_uri, position.line, position.character))

    @staticmethod
    def _collect_definition_locations(
        doc: A816Document, uri: str, word: str, workspace: WorkspaceIndex | None
    ) -> set[tuple[str, int, int]]:
        defs: set[tuple[str, int, int]] = set()
        A816LanguageServer._scan_definitions((doc.labels, doc.symbols, doc.macros), word, defs, force_uri=uri)
        if workspace:
            A816LanguageServer._scan_definitions((workspace.labels, workspace.symbols, workspace.macros), word, defs)
        return defs

    def _handle_references(self, params: ReferenceParams) -> list[Location] | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num]
        word_start, word_end = self._word_span(line, params.position.character)
        if word_start >= word_end:
            return None
        word = line[word_start:word_end]
        include_declaration = getattr(getattr(params, "context", None), "include_declaration", True)
        pattern = re.compile(r"\b" + re.escape(word) + r"\b")
        seen: set[tuple[str, int, int]] = set()
        references = self._refs_in_document(doc, params.text_document.uri, pattern, seen)
        workspace = self._ensure_workspace_index()
        if workspace:
            for uri, ws_doc in workspace.documents.items():
                if uri == params.text_document.uri:
                    continue
                references.extend(self._refs_in_document(ws_doc, uri, pattern, seen))
        if not references:
            return None
        if not include_declaration:
            defs = self._collect_definition_locations(doc, params.text_document.uri, word, workspace)
            references = [
                loc for loc in references if (loc.uri, loc.range.start.line, loc.range.start.character) not in defs
            ]
        return references or None

    _RENAME_SYMBOL_PATTERN = re.compile(r"^[A-Za-z_]\w*$")

    def _handle_prepare_rename(self, params: PrepareRenameParams) -> Range | None:
        """Tell the editor which range it can rename in place.

        Returns the word range under the cursor when it parses as a
        symbol identifier; `None` otherwise (rejects rename on
        instructions, numbers, registers).
        """
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num]
        word_start, word_end = self._word_span(line, params.position.character)
        if word_start >= word_end:
            return None
        word = line[word_start:word_end]
        if not self._RENAME_SYMBOL_PATTERN.match(word):
            return None
        # Reject opcodes / registers / directives — those are language
        # built-ins, not user symbols.
        lowered = word.lower()
        if lowered in snes_opcode_table:
            return None
        if lowered in {"a", "x", "y", "s", "p", "pc"}:
            return None
        if lowered in {"byte", "word", "long", "dword"}:
            return None
        return Range(
            start=Position(line=line_num, character=word_start),
            end=Position(line=line_num, character=word_end),
        )

    def _handle_rename(self, params: RenameParams) -> WorkspaceEdit | None:
        """Rename every reference to the symbol under the cursor across
        the workspace. Returns a WorkspaceEdit grouping per-file
        TextEdits. Editor applies atomically.
        """
        new_name = params.new_name
        if not self._RENAME_SYMBOL_PATTERN.match(new_name):
            return None

        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num]
        word_start, word_end = self._word_span(line, params.position.character)
        if word_start >= word_end:
            return None
        old_name = line[word_start:word_end]
        if not self._RENAME_SYMBOL_PATTERN.match(old_name):
            return None
        if old_name == new_name:
            return WorkspaceEdit(changes={})

        pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
        seen: set[tuple[str, int, int]] = set()
        references = self._refs_in_document(doc, params.text_document.uri, pattern, seen)
        workspace = self._ensure_workspace_index()
        if workspace:
            for uri, ws_doc in workspace.documents.items():
                if uri == params.text_document.uri:
                    continue
                references.extend(self._refs_in_document(ws_doc, uri, pattern, seen))

        changes: dict[str, list[TextEdit]] = {}
        for loc in references:
            edit = TextEdit(range=loc.range, new_text=new_name)
            changes.setdefault(loc.uri, []).append(edit)

        if not changes:
            return None
        return WorkspaceEdit(changes=changes)

    @staticmethod
    def _addressing_mode_label(mode: AddressingMode) -> str:
        labels = {
            AddressingMode.none: "No operand",
            AddressingMode.immediate: "#value",
            AddressingMode.direct: "address",
            AddressingMode.direct_indexed: "address,X or address,Y",
            AddressingMode.indirect: "(address)",
            AddressingMode.indirect_indexed: "(address),Y",
            AddressingMode.indirect_long: "[address]",
        }
        return labels.get(mode, str(mode.name))

    def _handle_signature_help(self, params: SignatureHelpParams) -> SignatureHelp | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num][: params.position.character]
        words = line.strip().split()
        if not words:
            return None
        if words[0].endswith(":"):
            words = words[1:]
        if not words:
            return None
        opcode = words[0].lower()
        base_opcode = opcode.split(".")[0] if "." in opcode else opcode
        if base_opcode not in snes_opcode_table:
            return None
        addressing_modes = snes_opcode_table[base_opcode]
        mode_descriptions = [self._addressing_mode_label(mode) for mode in addressing_modes]
        signature_info = SignatureInformation(
            label=f"{opcode.upper()}",
            documentation=f"Supported addressing modes: {', '.join(mode_descriptions)}",
        )
        return SignatureHelp(signatures=[signature_info], active_signature=0, active_parameter=0)

    def _handle_format_document(self, params: DocumentFormattingParams) -> list[TextEdit] | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        formatting_options = self._create_formatting_options(params.options)
        original_formatter = self.formatter
        self.formatter = A816Formatter(formatting_options)
        try:
            formatted_content = self.formatter.format_text(
                doc.content, file_path=doc.uri, include_paths=doc.include_paths
            )
            if formatted_content != doc.content:
                return [TextEdit(range=self._full_document_range(doc), new_text=formatted_content)]
            return []
        except FormattingError as exc:
            logger.exception("Formatter failed for %s: %s", doc.uri, exc)
            self.server.window_show_message(ShowMessageParams(type=MessageType.Error, message=str(exc)))
            return []
        finally:
            self.formatter = original_formatter

    def _create_formatting_options(self, lsp_options: LSPFormattingOptions) -> FormattingOptions:
        """Convert LSP formatting options to A816 formatting options"""
        return FormattingOptions(
            indent_size=lsp_options.tab_size,
            opcode_indent=lsp_options.tab_size,
            space_after_comma=getattr(lsp_options, "insert_spaces", True),
        )

    def _ensure_workspace_index(self) -> WorkspaceIndex | None:
        """Ensure workspace-level symbols are indexed and up to date."""
        try:
            root_path = self.server.workspace.root_path
        except RuntimeError:
            root_path = None
        if not root_path:
            root_path = os.getcwd()
        if not root_path:
            return None
        root = Path(root_path).resolve()
        if self.workspace_index is None or (
            self.workspace_index.root_path is not None and self.workspace_index.root_path != root
        ):
            self.workspace_index = WorkspaceIndex(root)
        if self.workspace_index.root_path is None:
            self.workspace_index.root_path = root
        if not self.workspace_index.built:
            self.workspace_index.rebuild()
        return self.workspace_index

    def _full_document_range(self, doc: A816Document) -> Range:
        """Return a Range covering the entire document."""
        if not doc.lines:
            return Range(start=Position(line=0, character=0), end=Position(line=0, character=0))

        last_line_index = len(doc.lines) - 1
        last_line = doc.lines[last_line_index]
        end_character = len(last_line)
        return Range(
            start=Position(line=0, character=0),
            end=Position(line=last_line_index, character=end_character),
        )

    @staticmethod
    def _quoted_span(line: str, match: re.Match[str]) -> tuple[int, int] | None:
        delimiter = '"' if '"' in match.group(0) else "'"
        quote_start = line.find(delimiter, match.start())
        if quote_start == -1:
            return None
        quote_end = line.find(delimiter, quote_start + 1)
        if quote_end == -1:
            return None
        return quote_start, quote_end

    @staticmethod
    def _file_location(path: str) -> Location:
        return Location(
            uri=Path(path).as_uri(),
            range=Range(start=Position(line=0, character=0), end=Position(line=0, character=0)),
        )

    def _try_directive_location(
        self,
        line: str,
        char_pos: int,
        pattern: str,
        resolve: Callable[[str], str | None],
    ) -> Location | None:
        match = re.search(pattern, line, re.IGNORECASE)
        if not match:
            return None
        span = self._quoted_span(line, match)
        if span is None:
            return None
        quote_start, quote_end = span
        if not (quote_start <= char_pos <= quote_end):
            return None
        resolved_path = resolve(match.group(1))
        if resolved_path and os.path.exists(resolved_path):
            return self._file_location(resolved_path)
        return None

    def _check_include_directive(
        self, doc: A816Document, line_num: int, char_pos: int, current_uri: str
    ) -> Location | None:
        """Return Location if cursor is on an .include or .import directive's path."""
        try:
            if line_num >= len(doc.lines):
                return None
            line = doc.lines[line_num]
            location = self._try_directive_location(
                line,
                char_pos,
                r'\.include\s+[\'"]([^\'"]+)[\'"]',
                lambda p: self._resolve_include_path(p, current_uri),
            )
            if location is not None:
                return location
            return self._try_directive_location(
                line,
                char_pos,
                r'\.import\s+[\'"]([^\'"]+)[\'"]',
                lambda m: self._resolve_module_path(m, current_uri),
            )
        except (OSError, ValueError, AttributeError) as e:
            logger.debug(f"Error checking include directive: {e}")
            return None

    def _resolve_include_path(self, include_path: str, current_uri: str) -> str | None:
        """Resolve include path against current dir, falling back to workspace include paths."""
        try:
            current_path = (
                Path(current_uri[len(FILE_URI_PREFIX) :])
                if current_uri.startswith(FILE_URI_PREFIX)
                else Path(current_uri)
            )
            if os.path.isabs(include_path):
                return str(Path(include_path).resolve())

            resolved_path = (current_path.parent / include_path).resolve()
            if resolved_path.exists():
                return str(resolved_path)
            workspace = self.workspace_index
            if workspace:
                for search_dir in workspace.include_paths:
                    candidate = (search_dir / include_path).resolve()
                    if candidate.exists():
                        return str(candidate)
            return str(resolved_path)
        except (OSError, ValueError) as e:
            logger.debug(f"Error resolving include path '{include_path}' from '{current_uri}': {e}")
            return None

    def _resolve_module_path(self, module_name: str, current_uri: str) -> str | None:
        """Resolve module name to source file path for .import directives.

        Search order: stdlib `@std/...` → current file's directory →
        workspace `module_paths`. Backed by the shared `module_loader`.
        """
        try:
            from a816.module_loader import resolve_module

            current_dir = uri_to_path(current_uri).parent
            search_paths = [current_dir]
            workspace = self.workspace_index
            if workspace:
                search_paths.extend(workspace.module_paths)
            resolved = resolve_module(module_name, ".s", search_paths)
            return str(resolved.resolve()) if resolved is not None else None
        except (OSError, ValueError) as e:
            logger.debug(f"Error resolving module path '{module_name}' from '{current_uri}': {e}")
            return None

    def _apply_text_change(self, content: str, change: TextDocumentContentChangePartial) -> str:
        """Apply an incremental text change to content"""

        if not hasattr(change, "range") or change.range is None:
            return change.text

        lines = content.splitlines(keepends=True)
        start_line = change.range.start.line
        start_char = change.range.start.character
        end_line = change.range.end.line
        end_char = change.range.end.character

        if start_line >= len(lines):
            return content + change.text
        if end_line >= len(lines):
            end_line = len(lines) - 1
            end_char = len(lines[end_line])

        if start_line == end_line:
            self._apply_single_line_change(lines, start_line, start_char, end_char, change.text)
        else:
            self._apply_multi_line_change(lines, start_line, start_char, end_line, end_char, change.text)
        return "".join(lines)

    @staticmethod
    def _apply_single_line_change(lines: list[str], line_idx: int, start_char: int, end_char: int, text: str) -> None:
        line = lines[line_idx] if line_idx < len(lines) else ""
        if not line.endswith("\n") and line_idx < len(lines) - 1:
            line += "\n"
        before = line[:start_char] if start_char < len(line) else line
        after = line[end_char:] if end_char < len(line) else ""
        lines[line_idx] = before + text + after

    @staticmethod
    def _apply_multi_line_change(
        lines: list[str], start_line: int, start_char: int, end_line: int, end_char: int, text: str
    ) -> None:
        start_content = lines[start_line] if start_line < len(lines) else ""
        end_content = lines[end_line] if end_line < len(lines) else ""
        before = start_content[:start_char] if start_char < len(start_content) else start_content
        after = end_content[end_char:] if end_char < len(end_content) else ""
        new_content = before + text + after
        del lines[start_line : end_line + 1]
        if not new_content:
            return
        new_lines = new_content.splitlines(keepends=True)
        if new_content.endswith("\n") and new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        lines[start_line:start_line] = new_lines

    def _publish_diagnostics(self, uri: str, diagnostics: list[Diagnostic]) -> None:
        """Publish diagnostics for a document"""
        from lsprotocol.types import PublishDiagnosticsParams

        self.server.text_document_publish_diagnostics(PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics))

    def start(self) -> None:
        """Start the LSP server"""
        logger.info("Starting a816 LSP server...")
        self.server.start_io()


def lsp_main() -> None:
    """Main entry point for LSP server"""
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s - %(message)s")

    lsp_server = A816LanguageServer()
    lsp_server.start()


if __name__ == "__main__":
    lsp_main()
