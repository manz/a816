import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
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
    MarkupContent,
    MarkupKind,
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
from a816.lsp.mask import build_code_mask
from a816.lsp.workspace import WorkspaceIndex
from a816.parse.ast.nodes import (
    AllocAstNode,
    AsciiAstNode,
    AssignAstNode,
    AstNode,
    BinOp,
    CastAccessExprNode,
    CastValueExprNode,
    CodeLookupAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    DataNode,
    DebugAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    ExprNode,
    ExternAstNode,
    ForAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapAstNode,
    OpcodeAstNode,
    Parenthesis,
    PoolAstNode,
    ReclaimAstNode,
    RegisterSizeAstNode,
    RelocateAstNode,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
    Term,
    TextAstNode,
    UnaryOp,
)
from a816.parse.scanner_states import KEYWORDS
from a816.parse.tokens import TokenType
from a816.util import uri_to_path

logger = logging.getLogger(__name__)

FILE_URI_PREFIX = "file://"

__all__ = ["A816Document", "A816LanguageServer", "WorkspaceIndex", "lsp_main"]


class A816LanguageServer:
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
        self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

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
        self._publish_diagnostics(params.text_document.uri, doc.diagnostics)
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
        self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

    def _local_completions(self, doc: A816Document) -> list[CompletionItem]:
        items: list[CompletionItem] = []
        for label in doc.labels:
            items.append(
                CompletionItem(
                    label=label,
                    kind=CompletionItemKind.Function,
                    detail="Label",
                    documentation=doc.label_docstrings.get(label.lower()),
                )
            )
        for symbol in doc.symbols:
            items.append(CompletionItem(label=symbol, kind=CompletionItemKind.Variable, detail="Symbol"))
        for macro_name in doc.macros:
            macro_parameters = doc.macro_params.get(macro_name, [])
            param_sig = f"({', '.join(macro_parameters)})" if macro_parameters else "()"
            macro_doc = doc.macro_docstrings.get(macro_name.lower())
            documentation = macro_doc or (
                f"User-defined macro with {len(macro_parameters)} parameters"
                if macro_parameters
                else "User-defined macro"
            )
            items.append(
                CompletionItem(
                    label=macro_name,
                    kind=CompletionItemKind.Function,
                    detail=f"User Macro{param_sig}",
                    documentation=documentation,
                )
            )
        return items

    def _handle_completions(self, params: CompletionParams) -> CompletionList:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return CompletionList(is_incomplete=False, items=[])
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return CompletionList(is_incomplete=False, items=[])

        line = doc.lines[line_num]
        char_pos = params.position.character
        word_start = char_pos
        while word_start > 0 and line[word_start - 1].isalnum():
            word_start -= 1
        current_word = line[word_start:char_pos].lower()

        # Context-aware: cursor after `in` / `into` / `.reclaim` → pool names only.
        pool_items = self._pool_name_completions_in_context(line, char_pos, doc)
        if pool_items is not None:
            filtered = (
                [item for item in pool_items if item.label.lower().startswith(current_word)]
                if current_word
                else pool_items
            )
            return CompletionList(is_incomplete=False, items=filtered[:50])

        all_items: list[CompletionItem] = []
        all_items.extend(self._opcode_completions)
        all_items.extend(self._keyword_completions)
        all_items.extend(self._register_completions)
        all_items.extend(self._build_labels_completions(doc))
        all_items.extend(self._local_completions(doc))
        workspace = self._ensure_workspace_index()
        if workspace:
            all_items.extend(self._build_workspace_label_completions(doc, workspace))
            all_items.extend(self._build_workspace_symbol_completions(doc, workspace))
            all_items.extend(self._build_workspace_macro_completions(doc, workspace))

        filtered = (
            [item for item in all_items if item.label.lower().startswith(current_word)] if current_word else all_items
        )
        return CompletionList(is_incomplete=False, items=filtered[:50])

    def _pool_name_completions_in_context(
        self, line: str, char_pos: int, doc: A816Document
    ) -> list[CompletionItem] | None:
        """When the cursor sits where a pool name is expected (after
        `.alloc X in `, `.relocate X N N into `, or `.reclaim `), suggest
        only declared pool names. Returns None when context doesn't match
        so the default completion list is used."""
        prefix = line[:char_pos].rstrip()
        triggers = (".alloc", ".relocate", ".reclaim")
        if not any(t in prefix for t in triggers):
            return None
        # Heuristic: cursor follows the `in` / `into` keyword (alloc / relocate)
        # or `.reclaim NAME` (after pool name comes addresses, but the pool name
        # is the first token after `.reclaim`).
        # Prefix contains at least one trigger token so split() is non-empty.
        last = prefix.split()[-1].lower()
        if last not in ("in", "into", ".reclaim"):
            return None
        workspace = self._ensure_workspace_index()
        names: set[str] = set(doc.pools)
        if workspace:
            names |= set(workspace.pools)
        return [CompletionItem(label=name, kind=CompletionItemKind.Module, detail=".pool") for name in sorted(names)]

    @staticmethod
    def _word_span(line: str, char_pos: int) -> tuple[int, int]:
        start = char_pos
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
            start -= 1
        end = char_pos
        while end < len(line) and (line[end].isalnum() or line[end] == "_"):
            end += 1
        return start, end

    @staticmethod
    def _markdown_hover(value: str) -> Hover:
        return Hover(contents=MarkupContent(kind=MarkupKind.Markdown, value=value))

    def _hover_for_opcode_or_keyword(self, base_word: str, word: str) -> Hover | None:
        if base_word in snes_opcode_table:
            return self._markdown_hover(
                f"**{word.upper()}** - 65c816 Instruction\n\nSupported addressing modes: {len(snes_opcode_table[base_word])}"
            )
        if base_word in KEYWORDS:
            return self._markdown_hover(f"**{word.upper()}** - Assembler directive")
        return None

    def _hover_for_label_or_scope(
        self, doc: A816Document, workspace: WorkspaceIndex | None, raw_word: str, word: str
    ) -> Hover | None:
        label_doc = doc.label_docstrings.get(word) or (workspace.get_label_doc(raw_word) if workspace else None)
        if label_doc:
            return self._markdown_hover(f"**{raw_word}**\n\n{label_doc}")
        scope_doc = doc.scope_docstrings.get(word) or (workspace.get_scope_doc(raw_word) if workspace else None)
        if scope_doc:
            return self._markdown_hover(f"**{raw_word}**\n\n{scope_doc}")
        return None

    def _hover_for_macro(
        self, doc: A816Document, workspace: WorkspaceIndex | None, raw_word: str, word: str
    ) -> Hover | None:
        macro_name = next((name for name in doc.macros if name.lower() == word), None)
        if macro_name:
            doc_text = doc.macro_docstrings.get(word)
            if doc_text:
                params_str = self._format_macro_params(doc.macro_params.get(macro_name, []))
                return self._markdown_hover(f"**{macro_name}{params_str}**\n\n{doc_text}")
        if workspace:
            macro_location = workspace.get_macro_location(raw_word)
            if macro_location:
                _, actual = macro_location
                doc_text = workspace.get_macro_doc(actual)
                if doc_text:
                    params_str = self._format_macro_params(workspace.get_macro_params(actual))
                    return self._markdown_hover(f"**{actual}{params_str}**\n\n{doc_text}")
        return None

    @staticmethod
    def _format_macro_params(param_list: list[str]) -> str:
        return f"({', '.join(param_list)})" if param_list else "()"

    def _handle_hover(self, params: HoverParams) -> Hover | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num]

        workspace = self._ensure_workspace_index()
        directive_hover = self._hover_for_module_directive(line, params.text_document.uri, workspace)
        if directive_hover:
            return directive_hover

        word_start, word_end = self._word_span(line, params.position.character)
        if word_start >= word_end:
            return None
        raw_word = line[word_start:word_end]
        word = raw_word.lower()
        base_word = word.split(".")[0] if "." in word else word

        opcode_or_keyword = self._hover_for_opcode_or_keyword(base_word, word)
        if opcode_or_keyword:
            return opcode_or_keyword
        pool_hover = self._hover_for_pool_directive(doc, raw_word)
        if pool_hover:
            return pool_hover
        dotted_word = self._dotted_word_span(line, params.position.character)
        struct_hover = self._hover_for_struct_field(dotted_word)
        if struct_hover:
            return struct_hover
        return self._hover_for_label_or_scope(doc, workspace, raw_word, word) or self._hover_for_macro(
            doc, workspace, raw_word, word
        )

    @staticmethod
    def _dotted_word_span(line: str, char_pos: int) -> str:
        """Like `_word_span` but stretches across `.` boundaries so
        `Type.field.mask` extracts as one string."""
        start = char_pos
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
            start -= 1
        end = char_pos
        while end < len(line) and (line[end].isalnum() or line[end] in "_."):
            end += 1
        return line[start:end]

    def _hover_for_struct_field(self, word: str) -> Hover | None:
        """Auto-doc for `Type.field`, `Type.field.mask`, `Type.field.shift`.

        Reaches into the workspace-shared struct registry built during AST
        indexing. Bit-field aux symbols get computed mask / shift values
        inlined into the hover so the user doesn't need to mentally compute
        them while editing.
        """
        struct, field, aux = self._parse_struct_field_path(word)
        if struct is None or field is None:
            return None
        meta = self._struct_field_meta(struct, field)
        if meta is None:
            return None
        field_type, bit_width, mask, shift = meta
        if aux == "mask":
            body = (
                f"**`{struct}.{field}.mask`** = `0x{mask:02X}`\n\n"
                f"Pre-shifted bit-mask for the `{field}` field "
                f"({bit_width}-bit, shift {shift}). Use as an immediate operand."
            )
            return self._markdown_hover(body)
        if aux == "shift":
            body = (
                f"**`{struct}.{field}.shift`** = `{shift}`\n\n"
                f"LSB position of the `{field}` field inside its containing byte."
            )
            return self._markdown_hover(body)
        if bit_width is not None:
            body = (
                f"**`{struct}.{field}`** — `{field_type}` bit-field\n\n"
                f"Width: `{bit_width}` bits, shift `{shift}`, mask `0x{mask:02X}`."
            )
        else:
            body = f"**`{struct}.{field}`** — `{field_type}` field of struct `{struct}`."
        return self._markdown_hover(body)

    @staticmethod
    def _parse_struct_field_path(word: str) -> tuple[str | None, str | None, str | None]:
        """Split `Type.field` / `Type.field.mask` / `Type.field.shift` into parts."""
        parts = word.split(".")
        if len(parts) == 2:
            return parts[0], parts[1], None
        if len(parts) == 3 and parts[2] in ("mask", "shift"):
            return parts[0], parts[1], parts[2]
        return None, None, None

    _BIT_FIELD_TYPE_RE: ClassVar[re.Pattern[str]] = re.compile(r"u(\d+)")

    def _struct_field_meta(self, struct_name: str, field_name: str) -> tuple[str, int | None, int, int] | None:
        """Return `(declared_type, bit_width, mask, shift)` for a struct field, or None."""
        for doc in self.documents.values():
            for node in doc.ast_nodes:
                if isinstance(node, StructAstNode) and node.name == struct_name:
                    meta = self._field_meta_in_struct(node, field_name)
                    if meta is not None:
                        return meta
        return None

    def _field_meta_in_struct(self, node: StructAstNode, field_name: str) -> tuple[str, int | None, int, int] | None:
        bit_position = 0
        for fname, ftype in node.fields:
            bit_match = self._BIT_FIELD_TYPE_RE.fullmatch(ftype)
            if bit_match:
                width = int(bit_match.group(1))
                if fname == field_name:
                    shift = bit_position % 8
                    mask = ((1 << width) - 1) << shift
                    return ftype, width, mask, shift
                bit_position += width
                continue
            bit_position = 0  # primitive flushes the current bit run
            if fname == field_name:
                return ftype, None, 0, 0
        return None

    def _hover_for_pool_directive(self, doc: A816Document, word: str) -> Hover | None:
        if word in doc.pools:
            detail = doc.pool_details.get(word, "")
            consumer_count = len(doc.pool_consumers.get(word, []))
            body = f"**`.pool {word}`**\n\n{detail}\n\n{consumer_count} consumer(s) in this document"
            return self._markdown_hover(body)
        if word in doc.allocs:
            pool_name = doc.alloc_target_pool.get(word, "?")
            detail = doc.pool_details.get(pool_name, "")
            kind = ".relocate" if word in doc.allocs and pool_name in doc.alloc_target_pool.values() else ".alloc"
            del kind  # cannot disambiguate cheaply; show generic
            body = f"**`{word}`** in pool `{pool_name}`\n\n{detail}"
            return self._markdown_hover(body)
        return None

    def _hover_for_module_directive(
        self, line: str, current_uri: str, workspace: WorkspaceIndex | None
    ) -> Hover | None:
        """Return the target module's leading docstring when cursor lands on
        an `.include "path"` or `.import "module"` line."""
        if workspace is None:
            return None
        include_match = re.search(r'\.include\s+[\'"]([^\'"]+)[\'"]', line, re.IGNORECASE)
        import_match = re.search(r'\.import\s+[\'"]([^\'"]+)[\'"]', line, re.IGNORECASE)
        target_uri: str | None = None
        label: str | None = None
        if include_match:
            resolved = self._resolve_include_path(include_match.group(1), current_uri)
            if resolved:
                target_uri = Path(resolved).as_uri()
                label = include_match.group(1)
        elif import_match:
            resolved = self._resolve_module_path(import_match.group(1), current_uri)
            if resolved:
                target_uri = Path(resolved).as_uri()
                label = import_match.group(1)
        if not target_uri or label is None:
            return None
        docstring = workspace.module_docstrings.get(target_uri)
        if not docstring:
            return None
        return self._markdown_hover(f"**{label}**\n\n{docstring}")

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

    def _handle_semantic_tokens_full(self, params: SemanticTokensParams) -> SemanticTokens | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        return SemanticTokens(data=self._analyze_semantic_tokens(doc))

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

    def _analyze_semantic_tokens(self, doc: A816Document) -> list[int]:
        """Analyze document and generate semantic tokens using the actual parser"""
        tokens: list[int] = []
        prev_line = 0
        prev_char = 0

        # Use the parsed AST nodes to generate semantic tokens
        semantic_tokens = self._extract_semantic_tokens_from_ast(doc)

        # Convert to LSP format (delta encoding)
        for token in sorted(semantic_tokens, key=lambda t: (t["line"], t["char"])):
            current_line = token["line"]
            current_char = token["char"]

            if current_line == prev_line:
                delta_line = 0
                delta_char = current_char - prev_char
            else:
                delta_line = current_line - prev_line
                delta_char = current_char

            tokens.extend(
                [
                    delta_line,  # deltaLine
                    delta_char,  # deltaStart
                    token["length"],  # length
                    token["type"],  # tokenType
                    0,  # tokenModifiers
                ]
            )

            # Update previous position
            prev_line = current_line
            prev_char = current_char

        return tokens

    def _extract_semantic_tokens_from_ast(self, doc: A816Document) -> list[dict[str, Any]]:
        """Extract semantic tokens from parsed AST nodes, with a line-based
        fallback when parsing failed hard (scanner errors leave `ast_nodes`
        empty, which would otherwise produce zero highlights and a plain-text
        document in the editor)."""
        tokens: list[dict[str, Any]] = []

        logger.debug(f"Processing {len(doc.ast_nodes)} AST nodes for semantic tokens")

        if not doc.ast_nodes:
            if doc.parse_error:
                logger.info("Falling back to line tokenizer: %s", doc.parse_error.message)
            return self._line_based_tokens(doc)

        for node in doc.ast_nodes:
            self._visit_node_for_tokens(node, tokens, doc)

        logger.debug(f"Generated {len(tokens)} AST-only tokens")
        return tokens

    def _line_based_tokens(self, doc: A816Document) -> list[dict[str, Any]]:
        """Best-effort highlighting when the AST is unavailable."""
        tokens: list[dict[str, Any]] = []
        for idx, line in enumerate(doc.lines):
            tokens.extend(self._tokenize_line(line, idx))
        return tokens

    _DIRECTIVE_TYPES: ClassVar[tuple[type, ...]] = (
        MacroApplyAstNode,
        CodePositionAstNode,
        CodeRelocationAstNode,
        MapAstNode,
        IfAstNode,
        ForAstNode,
        MacroAstNode,
        AssignAstNode,
        SymbolAffectationAstNode,
        ExternAstNode,
        ImportAstNode,
        StructAstNode,
        DataNode,
        AsciiAstNode,
        TextAstNode,
        TableAstNode,
        IncludeBinaryAstNode,
        IncludeIpsAstNode,
        ScopeAstNode,
        PoolAstNode,
        AllocAstNode,
        RelocateAstNode,
        ReclaimAstNode,
        DebugAstNode,
        LabelDeclAstNode,
        RegisterSizeAstNode,
        CodeLookupAstNode,
    )

    @staticmethod
    def _semantic_token_type(node: AstNode) -> int | None:
        if isinstance(node, LabelAstNode):
            return 1  # function (label)
        if isinstance(node, OpcodeAstNode):
            return 0  # keyword (opcode)
        if isinstance(node, CommentAstNode):
            return 2  # comment
        if isinstance(node, DocstringAstNode):
            return 4  # string
        if isinstance(node, IncludeAstNode) or isinstance(node, A816LanguageServer._DIRECTIVE_TYPES):
            return 7  # macro (directive)
        return None

    @staticmethod
    def _terminates_after_emit(node: AstNode) -> bool:
        return isinstance(node, DocstringAstNode | IncludeAstNode)

    def _visit_token_children(self, node: AstNode, tokens: list[dict[str, Any]], doc: A816Document) -> None:
        for attr in ("body", "block", "else_block", "value", "expression", "min_value", "max_value"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                for entry in child:
                    if isinstance(entry, AstNode):
                        self._visit_node_for_tokens(entry, tokens, doc)
            elif isinstance(child, AstNode):
                self._visit_node_for_tokens(child, tokens, doc)

    def _visit_node_for_tokens(self, node: AstNode, tokens: list[dict[str, Any]], doc: A816Document) -> None:
        """Recursively visit AST nodes to extract semantic tokens."""
        try:
            if not node.file_info or not node.file_info.position:
                return
            if node.file_info.position.file.filename and node.file_info.position.file.filename != doc.uri:
                return

            pos = node.file_info.position
            token_text = node.file_info.value
            type_id = self._semantic_token_type(node)
            if type_id is not None:
                tokens.append({"line": pos.line, "char": pos.column, "length": len(token_text), "type": type_id})

            if isinstance(node, OpcodeAstNode) and node.operand:
                self._visit_node_for_tokens(node.operand, tokens, doc)
                return
            if isinstance(node, ExpressionAstNode):
                self._analyze_expression_tokens(node, tokens)
                return
            if self._terminates_after_emit(node):
                return

            self._visit_token_children(node, tokens, doc)
        except (AttributeError, KeyError, IndexError, TypeError) as e:
            logger.debug(f"Error processing AST node {type(node).__name__}: {e}")

    def _analyze_expression_tokens(self, expr_node: ExpressionAstNode, tokens: list[dict[str, Any]]) -> None:
        """Highlight every token inside an expression — numbers, identifiers,
        operators, parens, and the `as TYPE` cast wrapping.
        """
        try:
            for expr_part in expr_node.tokens:
                self._highlight_expr_part(expr_part, tokens)
        except (AttributeError, KeyError, IndexError, TypeError) as e:
            logger.debug(f"Error analyzing expression tokens: {e}")

    def _highlight_expr_part(self, part: ExprNode, tokens: list[dict[str, Any]]) -> None:
        if isinstance(part, CastValueExprNode | CastAccessExprNode):
            # Recurse into the cast's inner expression — same shape as the
            # outer ExpressionAstNode token list.
            for inner in part.inner:
                self._highlight_expr_part(inner, tokens)
            return
        if isinstance(part, BinOp | UnaryOp):
            tok = part.token
            if tok.position:
                tokens.append(
                    {
                        "line": tok.position.line,
                        "char": tok.position.column,
                        "length": len(tok.value),
                        "type": 5,  # operator
                    }
                )
            return
        if isinstance(part, Parenthesis):
            return  # parens carry no semantic colour of their own
        if not isinstance(part, Term):
            return
        expr_token = part.token
        if not expr_token.position:
            return
        match expr_token.type:
            case TokenType.NUMBER:
                tokens.append(
                    {
                        "line": expr_token.position.line,
                        "char": expr_token.position.column,
                        "length": len(expr_token.value),
                        "type": 3,  # number
                    }
                )
            case TokenType.QUOTED_STRING:
                tokens.append(
                    {
                        "line": expr_token.position.line,
                        "char": expr_token.position.column,
                        "length": len(expr_token.value),
                        "type": 4,  # string
                    }
                )
            case TokenType.IDENTIFIER:
                tokens.append(
                    {
                        "line": expr_token.position.line,
                        "char": expr_token.position.column,
                        "length": len(expr_token.value),
                        "type": 6,  # variable
                    }
                )

    def _classify_identifier(self, identifier: str, doc: A816Document) -> int:
        """Classify an identifier as a specific token type"""
        # Check if it's a known label
        if identifier in doc.labels:
            return 1  # function (label)

        # Check if it's a known macro
        if identifier in doc.macros:
            return 7  # macro (user-defined macro)

        # Check if it's a known symbol
        if identifier in doc.symbols:
            return 6  # variable (symbol)

        # Check if it's a directive or keyword
        if identifier.lower() in KEYWORDS:
            return 7  # macro (directive)

        # Check if it's an opcode
        base_identifier = identifier.lower().split(".")[0]
        if base_identifier in snes_opcode_table:
            return 0  # keyword (opcode)

        # Check if it's a macro parameter (within macro scope)
        # This would require context tracking, but for now treat as variable

        # Default to variable for unknown identifiers
        return 6  # variable

    def _tokenize_line(self, line: str, line_num: int) -> list[dict[str, Any]]:
        """Tokenize a single line for semantic highlighting"""
        tokens: list[dict[str, Any]] = []

        # Handle comments first
        comment_pos = line.find(";")
        if comment_pos != -1:
            tokens.append(
                {
                    "line": line_num,
                    "char": comment_pos,
                    "length": len(line) - comment_pos,
                    "type": 2,  # comment
                }
            )
            # Only process text before comment
            line = line[:comment_pos]

        stripped = line.strip()
        if not stripped:
            return tokens

        # Check for label
        label_match = re.match(r"^(\s*)([a-zA-Z_]\w*):(.*)$", line, flags=re.ASCII)
        if label_match:
            indent = len(label_match.group(1))
            label_name = label_match.group(2)
            tokens.append(
                {
                    "line": line_num,
                    "char": indent,
                    "length": len(label_name),
                    "type": 1,  # function (label)
                }
            )
            # Continue with rest of line
            rest = label_match.group(3).strip()
            if rest:
                rest_pos = line.find(rest, indent + len(label_name) + 1)
                tokens.extend(self._tokenize_instruction(rest, line_num, rest_pos))
        else:
            # Regular instruction line
            first_non_space = len(line) - len(line.lstrip())
            tokens.extend(self._tokenize_instruction(stripped, line_num, first_non_space))

        return tokens

    def _tokenize_instruction(self, instruction: str, line_num: int, start_pos: int) -> list[dict[str, Any]]:
        """Tokenize an instruction for semantic highlighting"""
        tokens: list[dict[str, Any]] = []
        parts = instruction.split()

        if not parts:
            return tokens

        # Find the actual position of the first part (opcode/directive)
        opcode = parts[0]
        opcode_pos = instruction.find(opcode)
        if opcode_pos == -1:
            opcode_pos = 0

        base_opcode = opcode.lower().split(".")[0]

        if base_opcode in snes_opcode_table:
            # It's an opcode
            tokens.append(
                {
                    "line": line_num,
                    "char": start_pos + opcode_pos,
                    "length": len(opcode),
                    "type": 0,  # keyword
                }
            )
        elif base_opcode in KEYWORDS:
            # It's a directive
            tokens.append(
                {
                    "line": line_num,
                    "char": start_pos + opcode_pos,
                    "length": len(opcode),
                    "type": 7,  # macro (directive)
                }
            )

        # Handle operands
        if len(parts) > 1:
            operand_text = " ".join(parts[1:])
            operand_pos = instruction.find(operand_text, opcode_pos + len(opcode))
            if operand_pos != -1:
                tokens.extend(self._tokenize_operand(operand_text, line_num, start_pos + operand_pos))

        return tokens

    @staticmethod
    def _consume_hex_digits(operand: str, i: int) -> int:
        while i < len(operand) and operand[i] in "0123456789ABCDEFabcdef":
            i += 1
        return i

    @staticmethod
    def _consume_decimal(operand: str, i: int) -> int:
        while i < len(operand) and operand[i].isdigit():
            i += 1
        return i

    def _consume_number(self, operand: str, i: int) -> int:
        """Advance past a number literal (hex / decimal / #-prefixed immediate)."""
        if operand.startswith(("0x", "0X"), i):
            return self._consume_hex_digits(operand, i + 2)
        if operand[i] == "#":
            i += 1
            if operand.startswith(("0x", "0X"), i):
                return self._consume_hex_digits(operand, i + 2)
            return self._consume_decimal(operand, i)
        return self._consume_decimal(operand, i)

    @staticmethod
    def _is_register_at(operand: str, i: int) -> bool:
        return operand[i].upper() in "XYS" and (i == 0 or not operand[i - 1].isalnum())

    def _tokenize_operand(self, operand: str, line_num: int, start_pos: int) -> list[dict[str, Any]]:
        """Tokenize operand for semantic highlighting."""
        tokens: list[dict[str, Any]] = []
        i = 0
        while i < len(operand):
            char = operand[i]
            if char.isspace():
                i += 1
                continue
            if operand.startswith(("0x", "0X"), i) or char.isdigit() or char == "#":
                start = i
                i = self._consume_number(operand, i)
                tokens.append({"line": line_num, "char": start_pos + start, "length": i - start, "type": 3})
            elif self._is_register_at(operand, i):
                tokens.append({"line": line_num, "char": start_pos + i, "length": 1, "type": 6})
                i += 1
            elif char in "()[],.+-*&|":
                tokens.append({"line": line_num, "char": start_pos + i, "length": 1, "type": 5})
                i += 1
            else:
                i += 1
        return tokens

    def _build_opcode_completions(self) -> list[CompletionItem]:
        """Build completion items for all opcodes"""
        completions = []
        for opcode in snes_opcode_table.keys():
            completions.append(
                CompletionItem(
                    label=opcode.upper(),
                    kind=CompletionItemKind.Keyword,
                    detail="65c816 Instruction",
                    documentation=f"65c816 instruction with {len(snes_opcode_table[opcode])} addressing modes",
                )
            )

            # Add size variants
            for size in ["b", "w", "l"]:
                completions.append(
                    CompletionItem(
                        label=f"{opcode.upper()}.{size.upper()}",
                        kind=CompletionItemKind.Keyword,
                        detail=f"65c816 Instruction ({size.upper()})",
                        documentation=f"65c816 instruction with {size}-size specifier",
                    )
                )

        return completions

    def _build_keyword_completions(self) -> list[CompletionItem]:
        """Build completion items for assembler keywords"""
        return [
            CompletionItem(label=keyword.upper(), kind=CompletionItemKind.Keyword, detail="Assembler directive")
            for keyword in KEYWORDS
        ]

    def _build_register_completions(self) -> list[CompletionItem]:
        """Build completion items for registers"""
        registers = ["X", "Y", "S", "A"]
        return [
            CompletionItem(label=reg, kind=CompletionItemKind.Variable, detail="65c816 Register") for reg in registers
        ]

    def _build_labels_completions(self, doc: A816Document) -> list[CompletionItem]:
        return [
            CompletionItem(label=sym, kind=CompletionItemKind.Variable, detail="Symbol") for sym in doc.symbols.keys()
        ]

    def _build_workspace_label_completions(self, doc: A816Document, workspace: WorkspaceIndex) -> list[CompletionItem]:
        doc_labels = set(doc.labels.keys())
        items: list[CompletionItem] = []
        for label in workspace.labels.keys():
            if label in doc_labels:
                continue
            doc_text = workspace.get_label_doc(label)
            items.append(
                CompletionItem(
                    label=label,
                    kind=CompletionItemKind.Function,
                    detail="Label",
                    documentation=doc_text,
                )
            )
        return items

    def _build_workspace_symbol_completions(self, doc: A816Document, workspace: WorkspaceIndex) -> list[CompletionItem]:
        doc_symbols = set(doc.symbols.keys())
        items: list[CompletionItem] = []
        for symbol in workspace.symbols.keys():
            if symbol in doc_symbols:
                continue
            items.append(CompletionItem(label=symbol, kind=CompletionItemKind.Variable, detail="Symbol"))
        return items

    def _build_workspace_macro_completions(self, doc: A816Document, workspace: WorkspaceIndex) -> list[CompletionItem]:
        doc_macros = set(doc.macros.keys())
        items: list[CompletionItem] = []
        for macro in workspace.macros.keys():
            if macro in doc_macros:
                continue
            macro_params = workspace.get_macro_params(macro)
            param_sig = f"({', '.join(macro_params)})" if macro_params else ""
            macro_doc = workspace.get_macro_doc(macro)
            documentation = macro_doc or (
                f"User-defined macro with {len(macro_params)} parameters" if macro_params else "User-defined macro"
            )
            items.append(
                CompletionItem(
                    label=macro,
                    kind=CompletionItemKind.Function,
                    detail=f"User Macro{param_sig}",
                    documentation=documentation,
                )
            )
        return items

    def _workspace_container_name(self, uri: str, workspace: WorkspaceIndex) -> str | None:
        try:
            path = uri_to_path(uri)
        except ValueError:
            return None
        root = workspace.root_path
        if root:
            try:
                rel = path.relative_to(root)
                parent = rel.parent
                if parent and parent != Path(""):
                    return parent.as_posix()
                return None
            except ValueError:
                pass
        parent = path.parent
        if parent and parent != Path(""):
            return parent.as_posix()
        return None

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
