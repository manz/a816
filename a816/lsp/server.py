import inspect
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
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
    DiagnosticSeverity,
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
    SignatureHelp,
    SignatureHelpParams,
    SignatureInformation,
    SymbolInformation,
    SymbolKind,
    TextDocumentContentChangeEvent_Type1,
    TextDocumentContentChangeEvent_Type2,
    TextDocumentPositionParams,
    TextEdit,
    WorkspaceEdit,
    WorkspaceSymbolParams,
)
from lsprotocol.types import (
    FormattingOptions as LSPFormattingOptions,
)
from pygls.server import LanguageServer

from a816.cpu.cpu_65c816 import AddressingMode, snes_opcode_table
from a816.exceptions import FormattingError
from a816.formatter import A816Formatter, FormattingOptions
from a816.parse.ast.nodes import (
    AllocAstNode,
    AssignAstNode,
    AstNode,
    BlockAstNode,
    CodePositionAstNode,
    CommentAstNode,
    CompoundAstNode,
    DataNode,
    DocstringAstNode,
    ExpressionAstNode,
    ExternAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    LabelAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapAstNode,
    OpcodeAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
    Term,
)
from a816.parse.errors import ParseError, ParserSyntaxError, ScannerException
from a816.parse.mzparser import MZParser
from a816.parse.scanner_states import KEYWORDS
from a816.parse.tokens import Token, TokenType
from a816.util import uri_to_path

logger = logging.getLogger(__name__)

FILE_URI_PREFIX = "file://"


class A816Document:
    """Represents an a816 assembly document with analysis capabilities"""

    def __init__(self, uri: str, content: str, include_paths: list[Path] | None = None):
        self.uri = uri
        self.content = content
        self.include_paths: list[Path] = include_paths or []
        self.lines = content.splitlines()
        self.symbols: dict[str, tuple[Position, str]] = {}  # symbol -> (position, file_uri)
        self.labels: dict[str, tuple[Position, str]] = {}  # label -> (position, file_uri)
        self.macros: dict[str, tuple[Position, str]] = {}  # macro -> (position, file_uri)
        self.pools: dict[str, tuple[Position, str]] = {}  # pool name -> (position, file_uri)
        self.allocs: dict[str, tuple[Position, str]] = {}  # alloc / relocate name -> (position, file_uri)
        self.externs: set[str] = set()  # extern symbol names (declarations, not definitions)
        self.macro_params: dict[str, list[str]] = {}  # macro_name -> parameter_names
        self.macro_docstrings: dict[str, str] = {}
        self.scope_docstrings: dict[str, str] = {}
        self.label_docstrings: dict[str, str] = {}
        # Leading orphan docstring at the top of the file. Used by hover on
        # `.import "name"` and `.include "path"` to surface the target module's
        # one-shot description without having to chase the file.
        self.module_docstring: str | None = None
        self.imports: list[str] = []  # module names from .import directives
        self.diagnostics: list[Diagnostic] = []
        self.ast_nodes: list[AstNode] = []
        self.parse_error: ParseError | None = None
        self.analyze()

    def update_content(self, content: str) -> None:
        """Update document content and re-analyze"""
        self.content = content
        self.lines = content.splitlines()
        self.symbols.clear()
        self.labels.clear()
        self.macros.clear()
        self.pools.clear()
        self.allocs.clear()
        self.externs.clear()
        self.macro_params.clear()
        self.macro_docstrings.clear()
        self.scope_docstrings.clear()
        self.label_docstrings.clear()
        self.module_docstring = None
        self.imports.clear()
        self.diagnostics.clear()
        self.ast_nodes.clear()
        self.parse_error = None
        self.analyze()

    def analyze(self) -> None:
        """Analyze document using the parser and extract symbols, labels, and diagnostics"""
        try:
            # Parse using the actual a816 parser
            parser_result = MZParser.parse_as_ast(self.content, self.uri, include_paths=self.include_paths)
            self.ast_nodes = parser_result.nodes
            self.parse_error = parser_result.parse_error

            # Extract symbols and labels from AST
            self._extract_symbols_from_ast()
            self._collect_docstrings()

        except (ScannerException, ParserSyntaxError) as e:
            # These should be handled by MZParser.parse_as_ast, but just in case
            self.parse_error = ParseError(
                message=str(e),
                filename=self.uri,
                line=0,
                column=0,
            )
            self.ast_nodes = []
            logger.warning("Parser exception not caught by MZParser: %s", e)
        except (AttributeError, KeyError, IndexError, TypeError) as e:
            # Catch AST processing exceptions
            self.parse_error = ParseError(
                message=f"Unexpected parser error: {str(e)}",
                filename=self.uri,
                line=0,
                column=0,
            )
            self.ast_nodes = []
            logger.exception("Unexpected error during document analysis")

        # Generate diagnostics from parse errors and AST analysis
        self._generate_diagnostics()

    def _extract_symbols_from_ast(self) -> None:
        """Extract symbols and labels from the parsed AST"""
        try:
            for node in self.ast_nodes:
                self._visit_node_for_symbols(node)
        except (AttributeError, KeyError, IndexError, TypeError) as e:
            logger.warning("Error extracting symbols from AST: %s", e)
            # Continue without symbols rather than crashing

    def _record_token_position(self, token: Token, target: dict[str, tuple[Position, str]], name: str) -> None:
        if token.position:
            pos = Position(line=token.position.line, character=token.position.column)
            target[name] = (pos, self._get_file_uri_for_token(token))

    def _record_label(self, node: LabelAstNode) -> None:
        self._record_token_position(node.file_info, self.labels, node.label)

    def _record_symbol_assignment(self, node: AstNode) -> None:
        symbol = getattr(node, "symbol", None)
        if symbol:
            self._record_token_position(node.file_info, self.symbols, symbol)

    def _record_macro(self, node: MacroAstNode) -> None:
        if not getattr(node, "name", None):
            return
        self._record_token_position(node.file_info, self.macros, node.name)
        if not node.file_info.position:
            return
        if hasattr(node, "parameters") and node.parameters:
            self.macro_params[node.name] = [param.name for param in node.parameters if hasattr(param, "name")]
        if hasattr(node, "docstring") and node.docstring:
            self.macro_docstrings[node.name.lower()] = inspect.cleandoc(node.docstring)

    def _record_import(self, node: ImportAstNode) -> None:
        if node.module_name and node.module_name not in self.imports:
            self.imports.append(node.module_name)

    def _record_struct(self, node: StructAstNode) -> None:
        """Index struct fields as Name.field symbols so goto-def works."""
        token = node.file_info
        if not token.position:
            return
        pos = Position(line=token.position.line, character=token.position.column)
        file_uri = self._get_file_uri_for_token(token)
        for field_name, _field_type in node.fields:
            self.symbols[f"{node.name}.{field_name}"] = (pos, file_uri)
        self.symbols[f"{node.name}.__size"] = (pos, file_uri)

    def _visit_include(self, node: IncludeAstNode) -> None:
        for child in node.included_nodes:
            if isinstance(child, AstNode):
                self._visit_node_for_symbols(child)

    def _visit_children(self, node: AstNode) -> None:
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for child in body:
                self._visit_node_for_symbols(child)
        elif isinstance(body, AstNode):
            self._visit_node_for_symbols(body)
        block = getattr(node, "block", None)
        if isinstance(block, AstNode):
            self._visit_node_for_symbols(block)
        else_block = getattr(node, "else_block", None)
        if isinstance(else_block, AstNode):
            self._visit_node_for_symbols(else_block)

    def _record_pool_directive(self, node: AstNode) -> None:
        if isinstance(node, PoolAstNode):
            self._record_token_position(node.file_info, self.pools, node.pool_name)
        elif isinstance(node, AllocAstNode):
            self._record_token_position(node.file_info, self.allocs, node.name)
        elif isinstance(node, RelocateAstNode):
            self._record_token_position(node.file_info, self.allocs, node.symbol)

    def _visit_node_for_symbols(self, node: AstNode) -> None:
        """Recursively visit AST nodes to extract symbols and labels."""
        if isinstance(node, DocstringAstNode):
            return
        if isinstance(node, LabelAstNode):
            self._record_label(node)
        elif isinstance(node, AssignAstNode | SymbolAffectationAstNode):
            self._record_symbol_assignment(node)
        elif isinstance(node, ExternAstNode):
            if getattr(node, "symbol", None):
                self.externs.add(node.symbol)
        elif isinstance(node, ScopeAstNode):
            if node.docstring:
                self.scope_docstrings[node.name.lower()] = inspect.cleandoc(node.docstring)
        elif isinstance(node, MacroAstNode):
            self._record_macro(node)
        elif isinstance(node, PoolAstNode | AllocAstNode | RelocateAstNode | ReclaimAstNode):
            self._record_pool_directive(node)
        elif isinstance(node, ImportAstNode):
            self._record_import(node)
        elif isinstance(node, StructAstNode):
            self._record_struct(node)
            return
        elif isinstance(node, IncludeAstNode):
            self._visit_include(node)
            return

        self._visit_children(node)

    def _set_docstring(self, target: tuple[str, str], text: str) -> None:
        cleaned = inspect.cleandoc(text)
        if not cleaned:
            return
        kind, name = target
        store = {
            "label": self.label_docstrings,
            "macro": self.macro_docstrings,
            "scope": self.scope_docstrings,
        }.get(kind)
        if store is not None:
            store[name] = cleaned

    def _docstrings_collect_block(
        self,
        block: AstNode | list[AstNode] | None,
        pending_doc: str | None = None,
        pending_target: tuple[str, str] | None = None,
    ) -> tuple[str | None, tuple[str, str] | None]:
        if block is None:
            return pending_doc, pending_target
        if isinstance(block, BlockAstNode | CompoundAstNode):
            return self._docstrings_collect_nodes(block.body, pending_doc, pending_target)
        if isinstance(block, list):
            return self._docstrings_collect_nodes(block, pending_doc, pending_target)
        if isinstance(block, AstNode):
            return self._docstrings_collect_nodes([block], pending_doc, pending_target)
        return pending_doc, pending_target

    def _docstrings_handle_targeted(
        self, kind: str, name: str, doc_text: str | None, buffer: str | None
    ) -> tuple[str | None, tuple[str, str] | None]:
        key = (kind, name)
        text = inspect.cleandoc(doc_text or buffer or "")
        if text:
            self._set_docstring(key, text)
            return None, None
        return buffer, key

    def _docstrings_handle_node(
        self, node: AstNode, doc_buffer: str | None, target: tuple[str, str] | None
    ) -> tuple[str | None, tuple[str, str] | None]:
        if isinstance(node, LabelAstNode):
            return self._docstrings_handle_targeted("label", node.label.lower(), None, doc_buffer)
        if isinstance(node, MacroAstNode):
            buffer, pending = self._docstrings_handle_targeted("macro", node.name.lower(), node.docstring, doc_buffer)
            self._docstrings_collect_block(node.block)
            return buffer, pending
        if isinstance(node, ScopeAstNode):
            buffer, pending = self._docstrings_handle_targeted("scope", node.name.lower(), node.docstring, doc_buffer)
            self._docstrings_collect_block(node.body)
            return buffer, pending
        if isinstance(node, CompoundAstNode | BlockAstNode):
            return self._docstrings_collect_nodes(node.body, doc_buffer, target)
        if isinstance(node, IncludeAstNode):
            included = [child for child in node.included_nodes if isinstance(child, AstNode)]
            self._docstrings_collect_nodes(included)
            return doc_buffer, None
        if isinstance(node, CommentAstNode):
            return doc_buffer, target
        body = getattr(node, "body", None)
        if isinstance(body, list):
            self._docstrings_collect_nodes(body)
        block = getattr(node, "block", None)
        if block is not None:
            self._docstrings_collect_block(block)
        else_block = getattr(node, "else_block", None)
        if else_block is not None:
            self._docstrings_collect_block(else_block)
        return doc_buffer, None

    def _docstrings_handle_docstring(
        self, node: DocstringAstNode, doc_buffer: str | None, target: tuple[str, str] | None
    ) -> tuple[str | None, tuple[str, str] | None]:
        text = inspect.cleandoc(node.text)
        if not text:
            return doc_buffer, target
        if target:
            self._set_docstring(target, text)
            return doc_buffer, None
        return text, target

    def _docstrings_collect_nodes(
        self, nodes: list[AstNode], pending_doc: str | None = None, pending_target: tuple[str, str] | None = None
    ) -> tuple[str | None, tuple[str, str] | None]:
        doc_buffer = pending_doc
        target = pending_target
        for node in nodes:
            if isinstance(node, DocstringAstNode):
                doc_buffer, target = self._docstrings_handle_docstring(node, doc_buffer, target)
                continue
            doc_buffer, target = self._docstrings_handle_node(node, doc_buffer, target)
        return doc_buffer, target

    def _collect_docstrings(self) -> None:
        """Associate docstrings with labels, macros, scopes, and the module."""
        self.label_docstrings.clear()
        # Capture the first leading docstring as the module-level description.
        # Anything before the first labelable target counts; the doc visitor
        # emits remaining unattached buffers as the returned `pending_doc`.
        for node in self.ast_nodes:
            if isinstance(node, DocstringAstNode):
                text = inspect.cleandoc(node.text)
                if text:
                    self.module_docstring = text
                break
            if isinstance(node, CommentAstNode):
                continue
            break
        self._docstrings_collect_nodes(self.ast_nodes)

    def _resolve_relative_token_file(self, token_filename: str) -> str:
        current_doc_path = (
            Path(self.uri.replace(FILE_URI_PREFIX, "")) if self.uri.startswith(FILE_URI_PREFIX) else Path(self.uri)
        )
        base_dir = current_doc_path.parent if current_doc_path.is_file() else current_doc_path
        return (base_dir / token_filename).resolve().as_uri()

    def _get_file_uri_for_token(self, token: Token) -> str:
        """Return file URI for a token (handles same- and included-file tokens)."""
        if not token.position:
            return self.uri
        token_filename = token.position.file.filename
        if token_filename.startswith(FILE_URI_PREFIX):
            return token_filename
        try:
            if os.path.isabs(token_filename):
                return Path(token_filename).as_uri()
            return self._resolve_relative_token_file(token_filename)
        except (ValueError, OSError) as e:
            logger.debug(f"Failed to resolve token file path '{token_filename}': {e}")
            return self.uri

    def _generate_diagnostics(self) -> None:
        """Generate diagnostics from parse errors and AST analysis"""
        # Add parse error as diagnostic if present
        if self.parse_error:
            self._add_parse_error_diagnostic()

        self._add_fluff_diagnostics()

    def _add_fluff_diagnostics(self) -> None:
        """Surface a816 fluff lint hits as LSP warnings."""
        from urllib.parse import urlparse

        from a816.fluff_lint import lint_text

        parsed = urlparse(self.uri)
        path = Path(parsed.path) if parsed.scheme == "file" else Path(self.uri)
        try:
            hits = lint_text(self.content, path, include_paths=self.include_paths)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("fluff lint failed for %s: %s", self.uri, exc)
            return
        for hit in hits:
            line = max(0, hit.line - 1)
            column = max(0, hit.column - 1)
            line_length = len(self.lines[line]) if 0 <= line < len(self.lines) else column + 1
            end_col = max(column + 1, line_length)
            self.diagnostics.append(
                Diagnostic(
                    range=Range(
                        start=Position(line=line, character=column),
                        end=Position(line=line, character=end_col),
                    ),
                    message=hit.message,
                    severity=DiagnosticSeverity.Warning,
                    source="a816 fluff",
                    code=hit.code,
                )
            )

    def _add_parse_error_diagnostic(self) -> None:
        """Convert parse error to LSP diagnostic"""
        if not self.parse_error:
            return

        error = self.parse_error
        error_line = error.line
        error_col = error.column
        error_length = error.length

        # Ensure we don't go beyond document bounds
        if not self.lines:
            error_line = 0
            error_col = 0
        else:
            if error_line >= len(self.lines):
                error_line = len(self.lines) - 1
            if error_line < 0:
                error_line = 0
            if error_col < 0:
                error_col = 0

            line_length = len(self.lines[error_line]) if self.lines else 0
            if error_col >= line_length:
                error_col = max(0, line_length - 1)

            # Ensure error_length doesn't exceed line bounds
            if error_col + error_length > line_length:
                error_length = max(1, line_length - error_col)

        # Create diagnostic with proper range
        self.diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=error_line, character=error_col),
                    end=Position(line=error_line, character=error_col + error_length),
                ),
                message=error.message,
                severity=DiagnosticSeverity.Error,
            )
        )


class WorkspaceIndex:
    """Indexes workspace files to provide cross-file symbol resolution."""

    ENTRYPOINT_PRAGMA = ";! a816-lsp entrypoint"

    def __init__(self, root_path: Path | str | None):
        self.root_path = Path(root_path).resolve() if root_path else None
        self.entrypoint: Path | None = None
        self.include_paths: list[Path] = []
        self.module_paths: list[Path] = []
        self.documents: dict[str, A816Document] = {}
        self.labels: dict[str, tuple[Position, str]] = {}
        self.symbols: dict[str, tuple[Position, str]] = {}
        self.macros: dict[str, tuple[Position, str]] = {}
        self.macro_params: dict[str, list[str]] = {}
        self.macro_docstrings: dict[str, str] = {}
        self.label_docstrings: dict[str, str] = {}
        self.scope_docstrings: dict[str, str] = {}
        # uri -> module-level docstring, surfaced when hovering an `.import`
        # or `.include` token that resolves to that file.
        self.module_docstrings: dict[str, str] = {}
        self.doc_labels: dict[str, set[str]] = {}
        self.doc_symbols: dict[str, set[str]] = {}
        self.doc_macros: dict[str, set[str]] = {}
        self.doc_label_docstrings: dict[str, set[str]] = {}
        self.doc_macro_docstrings: dict[str, set[str]] = {}
        self.doc_scope_docstrings: dict[str, set[str]] = {}
        self.doc_macro_params: dict[str, set[str]] = {}
        self.label_name_lookup: dict[str, str] = {}
        self.macro_name_lookup: dict[str, str] = {}
        self.scope_name_lookup: dict[str, str] = {}
        self.built = False

    def clear(self) -> None:
        self.documents.clear()
        self.include_paths.clear()
        self.module_paths.clear()
        self.labels.clear()
        self.symbols.clear()
        self.macros.clear()
        self.macro_params.clear()
        self.macro_docstrings.clear()
        self.label_docstrings.clear()
        self.scope_docstrings.clear()
        self.module_docstrings.clear()
        self.doc_labels.clear()
        self.doc_symbols.clear()
        self.doc_macros.clear()
        self.doc_label_docstrings.clear()
        self.doc_macro_docstrings.clear()
        self.doc_scope_docstrings.clear()
        self.doc_macro_params.clear()
        self.label_name_lookup.clear()
        self.macro_name_lookup.clear()
        self.scope_name_lookup.clear()

    def rebuild(self) -> None:
        """Re-index the workspace from the detected entrypoint."""
        self.clear()
        self.entrypoint = self._detect_entrypoint()
        if not self.entrypoint:
            logger.debug("WorkspaceIndex: no entrypoint detected")
            self.built = True
            return
        self._explore_from(self.entrypoint)
        self.built = True

    def replace_document(self, doc: A816Document) -> None:
        """Add or update a document inside the workspace index."""
        if not doc.uri:
            return
        self._prune_previous_entries(doc.uri)
        self._store_document(doc)
        self.built = True

    def remove_document(self, uri: str) -> None:
        """Remove a document from the index."""
        self._prune_previous_entries(uri)
        self.documents.pop(uri, None)

    def reload_document_from_disk(self, uri: str) -> None:
        """Reload a document from disk after closing it."""
        path = uri_to_path(uri)
        if not path.exists():
            self.remove_document(uri)
            return
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            self.remove_document(uri)
            return
        doc = A816Document(path.as_uri(), content, include_paths=self.include_paths)
        self.replace_document(doc)

    def get_label_location(self, name: str) -> Location | None:
        actual_name = name if name in self.labels else self.label_name_lookup.get(name.lower())
        if not actual_name:
            return None
        position, uri = self.labels.get(actual_name, (None, None))
        if position is None or uri is None:
            return None
        end = Position(line=position.line, character=position.character + len(actual_name))
        return Location(uri=uri, range=Range(start=position, end=end))

    def get_symbol_location(self, name: str) -> Location | None:
        entry = self.symbols.get(name)
        if not entry:
            return None
        position, uri = entry
        end = Position(line=position.line, character=position.character + len(name))
        return Location(uri=uri, range=Range(start=position, end=end))

    def get_macro_location(self, name: str) -> tuple[Location, str] | None:
        actual_name = name if name in self.macros else self.macro_name_lookup.get(name.lower())
        if not actual_name:
            return None
        position, uri = self.macros.get(actual_name, (None, None))
        if position is None or uri is None:
            return None
        end = Position(line=position.line, character=position.character + len(actual_name))
        location = Location(uri=uri, range=Range(start=position, end=end))
        return location, actual_name

    def get_label_doc(self, name: str) -> str | None:
        return self.label_docstrings.get(name.lower())

    def get_macro_doc(self, name: str) -> str | None:
        return self.macro_docstrings.get(name.lower())

    def get_scope_doc(self, name: str) -> str | None:
        return self.scope_docstrings.get(name.lower())

    def get_macro_params(self, name: str) -> list[str]:
        return self.macro_params.get(name, [])

    def _detect_entrypoint(self) -> Path | None:
        if not self.root_path:
            return None
        pragma = self._entry_from_pragma()
        if pragma:
            return pragma
        config = self._entry_from_config()
        if config:
            return config
        return self._fallback_entrypoint()

    def _file_declares_entrypoint(self, path: Path) -> bool:
        """Look for ENTRYPOINT_PRAGMA in the first 64 lines of the file."""
        try:
            with path.open("r", encoding="utf-8") as handle:
                for _ in range(64):
                    line = handle.readline()
                    if not line:
                        return False
                    if self.ENTRYPOINT_PRAGMA in line:
                        return True
        except OSError:
            return False
        return False

    def _entry_from_pragma(self) -> Path | None:
        if not self.root_path:
            return None
        for path in sorted(self.root_path.rglob("*.s")):
            if self._file_declares_entrypoint(path):
                return path.resolve()
        return None

    def _find_a816_toml(self) -> Path | None:
        if self.root_path is None:
            return None
        current = self.root_path
        while True:
            candidate = current / "a816.toml"
            if candidate.exists():
                return candidate
            if current.parent == current:
                return None
            current = current.parent

    def _merge_path_list(self, target: list[Path], config_root: Path, paths: list[str]) -> None:
        for p in paths:
            resolved = (config_root / p).resolve()
            if resolved not in target:
                target.append(resolved)

    def _entry_from_config(self) -> Path | None:
        if tomllib is None:
            return None
        config_file = self._find_a816_toml()
        if config_file is None:
            return None
        try:
            with config_file.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None

        config_root = config_file.parent
        self._merge_path_list(self.include_paths, config_root, data.get("include-paths", []))
        self._merge_path_list(self.module_paths, config_root, data.get("module-paths", []))

        entry = data.get("entrypoint")
        if not entry:
            return None
        result = (config_root / entry).resolve()
        return result if result.exists() else None

    def _fallback_entrypoint(self) -> Path | None:
        if not self.root_path:
            return None
        default = (self.root_path / "ff4.s").resolve()
        if default.exists():
            return default
        for path in sorted(self.root_path.rglob("*.s")):
            return path.resolve()
        return None

    def _read_or_log(self, current: Path) -> str | None:
        if not current.exists():
            logger.debug("WorkspaceIndex: include not found %s", current)
            return None
        try:
            return current.read_text(encoding="utf-8")
        except OSError:
            logger.debug("WorkspaceIndex: unable to read %s", current)
            return None

    def _explore_from(self, entrypoint: Path) -> None:
        queue: list[Path] = [entrypoint.resolve()]
        visited: set[Path] = set()
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            content = self._read_or_log(current)
            if content is None:
                continue
            self._store_document(A816Document(current.as_uri(), content, include_paths=self.include_paths))
            queue.extend(include for include in self._extract_includes(current, content) if include not in visited)

    @staticmethod
    def _resolve_in_paths(file_name: str, file_dir: Path, search_paths: list[Path]) -> Path | None:
        candidate = (file_dir / file_name).resolve()
        if candidate.exists():
            return candidate
        for search_dir in search_paths:
            candidate = (search_dir / file_name).resolve()
            if candidate.exists():
                return candidate
        return None

    def _extract_includes(self, file_path: Path, content: str) -> list[Path]:
        found_paths: set[Path] = set()
        for match in re.finditer(r"\.include\s+['\"]([^'\"]+)['\"]", content, re.IGNORECASE):
            resolved = self._resolve_in_paths(match.group(1).strip(), file_path.parent, self.include_paths)
            if resolved:
                found_paths.add(resolved)
        for match in re.finditer(r"\.import\s+['\"]([^'\"]+)['\"]", content, re.IGNORECASE):
            resolved = self._resolve_in_paths(match.group(1).strip() + ".s", file_path.parent, self.module_paths)
            if resolved:
                found_paths.add(resolved)
        return list(found_paths)

    def _index_labels(self, doc: A816Document) -> set[str]:
        names = set(doc.labels.keys())
        for label in names:
            self.labels[label] = doc.labels[label]
            self.label_name_lookup[label.lower()] = label
        return names

    def _index_symbols(self, doc: A816Document) -> set[str]:
        names = set(doc.symbols.keys())
        for symbol in names:
            self.symbols[symbol] = doc.symbols[symbol]
        return names

    def _index_macros(self, doc: A816Document) -> set[str]:
        names = set(doc.macros.keys())
        for macro in names:
            self.macros[macro] = doc.macros[macro]
            self.macro_name_lookup[macro.lower()] = macro
            if macro in doc.macro_params:
                self.macro_params[macro] = doc.macro_params[macro]
        return names

    def _merge_docstrings(self, doc: A816Document) -> None:
        self.label_docstrings.update(doc.label_docstrings)
        self.macro_docstrings.update(doc.macro_docstrings)
        for key, value in doc.scope_docstrings.items():
            self.scope_docstrings[key] = value
            self.scope_name_lookup[key] = key

    def _store_document(self, doc: A816Document) -> None:
        if not doc.uri:
            return
        self.documents[doc.uri] = doc
        new_labels = self._index_labels(doc)
        new_symbols = self._index_symbols(doc)
        new_macros = self._index_macros(doc)
        self._merge_docstrings(doc)
        if doc.module_docstring:
            self.module_docstrings[doc.uri] = doc.module_docstring
        else:
            self.module_docstrings.pop(doc.uri, None)

        self.doc_labels[doc.uri] = new_labels
        self.doc_symbols[doc.uri] = new_symbols
        self.doc_macros[doc.uri] = new_macros
        self.doc_label_docstrings[doc.uri] = set(doc.label_docstrings.keys())
        self.doc_macro_docstrings[doc.uri] = set(doc.macro_docstrings.keys())
        self.doc_scope_docstrings[doc.uri] = set(doc.scope_docstrings.keys())
        self.doc_macro_params[doc.uri] = set(doc.macro_params.keys())

    @staticmethod
    def _drop_owned(
        store: dict[str, tuple[Position, str]],
        names: set[str],
        uri: str,
        lookup: dict[str, str] | None = None,
    ) -> None:
        for name in names:
            entry = store.get(name)
            if entry and entry[1] == uri:
                store.pop(name, None)
                if lookup is not None:
                    lookup.pop(name.lower(), None)

    def _prune_previous_entries(self, uri: str) -> None:
        self._drop_owned(self.labels, self.doc_labels.get(uri, set()), uri, self.label_name_lookup)
        self._drop_owned(self.symbols, self.doc_symbols.get(uri, set()), uri)
        self._drop_owned(self.macros, self.doc_macros.get(uri, set()), uri, self.macro_name_lookup)
        for macro in self.doc_macros.get(uri, set()):
            self.macro_params.pop(macro, None)
        for key in self.doc_label_docstrings.get(uri, set()):
            self.label_docstrings.pop(key, None)
        for key in self.doc_macro_docstrings.get(uri, set()):
            self.macro_docstrings.pop(key, None)
        for key in self.doc_scope_docstrings.get(uri, set()):
            self.scope_docstrings.pop(key, None)
            self.scope_name_lookup.pop(key, None)
        self.module_docstrings.pop(uri, None)
        for store in (
            self.doc_labels,
            self.doc_symbols,
            self.doc_macros,
            self.doc_label_docstrings,
            self.doc_macro_docstrings,
            self.doc_scope_docstrings,
            self.doc_macro_params,
        ):
            store.pop(uri, None)


@dataclass
class _MaskState:
    """Cross-line scanner state for `_build_code_mask`. The
    triple-quoted-string flag and the C-style block-comment flag both
    survive a newline; single-quoted strings don't span lines in a816
    syntax.
    """

    in_triple: str | None = None
    in_block_comment: bool = False


def _consume_block_comment(raw: str, i: int, mask: list[bool], state: _MaskState) -> int:
    """Mask bytes inside a `/* ... */` block comment. Closes the block
    on `*/`. Returns the new cursor position.
    """
    mask[i] = False
    if i + 1 < len(raw) and raw[i] == "*" and raw[i + 1] == "/":
        mask[i + 1] = False
        state.in_block_comment = False
        return i + 2
    return i + 1


def _consume_triple(raw: str, i: int, mask: list[bool], state: _MaskState) -> int:
    """Mask one byte of a triple-quoted string, closing it on the
    matching delimiter. Returns the new cursor position.
    """
    mask[i] = False
    if state.in_triple is not None and raw[i : i + 3] == state.in_triple:
        mask[i + 1] = False
        mask[i + 2] = False
        state.in_triple = None
        return i + 3
    return i + 1


def _consume_string(raw: str, i: int, mask: list[bool], in_string: str) -> tuple[int, str | None]:
    """Mask one byte of a single-line string, handling backslash escapes
    and the closing delimiter. Returns (new cursor, new state).
    """
    mask[i] = False
    if raw[i] == "\\" and i + 1 < len(raw):
        mask[i + 1] = False
        return i + 2, in_string
    if raw[i] == in_string:
        return i + 1, None
    return i + 1, in_string


def _consume_line_comment(raw: str, i: int, mask: list[bool]) -> int:
    """Mask the rest of the line after a `;`. Returns a sentinel position
    past the line end so `_mask_line`'s loop terminates.
    """
    for j in range(i, len(raw)):
        mask[j] = False
    return len(raw)


def _open_region(raw: str, i: int, mask: list[bool], state: _MaskState) -> tuple[int, str | None]:
    """Examine the current character and open a comment/string region
    if one starts here. Returns `(new_i, in_string_delim)` where the
    delimiter is non-None when a single-line string just opened.
    """
    ch = raw[i]
    if ch == ";":
        return _consume_line_comment(raw, i, mask), None
    if raw[i : i + 2] == "/*":
        state.in_block_comment = True
        mask[i] = mask[i + 1] = False
        return i + 2, None
    if raw[i : i + 3] in ('"""', "'''"):
        state.in_triple = raw[i : i + 3]
        mask[i] = mask[i + 1] = mask[i + 2] = False
        return i + 3, None
    if ch in ('"', "'"):
        mask[i] = False
        return i + 1, ch
    return i + 1, None


def _mask_line(raw: str, state: _MaskState) -> list[bool]:
    """Build the code-mask for a single line, advancing `state` for
    triple-quoted strings and `/* ... */` block comments that may span
    multiple lines.
    """
    mask = [True] * len(raw)
    i = 0
    in_string: str | None = None
    while i < len(raw):
        if state.in_block_comment:
            i = _consume_block_comment(raw, i, mask, state)
            continue
        if state.in_triple is not None:
            i = _consume_triple(raw, i, mask, state)
            continue
        if in_string is not None:
            i, in_string = _consume_string(raw, i, mask, in_string)
            continue
        i, in_string = _open_region(raw, i, mask, state)
    return mask


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
            self._handle_did_open(params)

        @self.server.feature("textDocument/didChange")
        async def did_change(ls: LanguageServer, params: DidChangeTextDocumentParams) -> None:
            self._handle_did_change(params)

        @self.server.feature("textDocument/didClose")
        async def did_close(ls: LanguageServer, params: DidCloseTextDocumentParams) -> None:
            self._handle_did_close(params)

        @self.server.feature("textDocument/didSave")
        async def did_save(ls: LanguageServer, params: DidSaveTextDocumentParams) -> None:
            self._handle_did_save(params)

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

    def _handle_did_open(self, params: DidOpenTextDocumentParams) -> None:
        workspace = self._ensure_workspace_index()
        include_paths = workspace.include_paths if workspace else []
        doc = A816Document(params.text_document.uri, params.text_document.text, include_paths=include_paths)
        self.documents[params.text_document.uri] = doc
        if workspace:
            workspace.replace_document(doc)
        self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

    def _apply_content_changes(self, doc: A816Document, changes: list[Any]) -> str:
        current_content = doc.content
        for change in changes:
            if isinstance(change, TextDocumentContentChangeEvent_Type2):
                current_content = change.text
                logger.debug("Full document replacement")
            else:
                logger.debug(f"Incremental change at {change.range.start.line}:{change.range.start.character}")
                current_content = self._apply_text_change(current_content, change)
        return current_content

    def _handle_did_change(self, params: DidChangeTextDocumentParams) -> None:
        doc = self.documents.get(params.text_document.uri)
        if not doc or not params.content_changes:
            return
        doc.update_content(self._apply_content_changes(doc, list(params.content_changes)))
        workspace = self._ensure_workspace_index()
        if workspace:
            workspace.replace_document(doc)
        self._publish_diagnostics(params.text_document.uri, doc.diagnostics)
        try:
            self.server.semantic_tokens_refresh()
        except (AttributeError, RuntimeError, TypeError) as e:
            logger.debug(f"Could not refresh semantic tokens: {e}")

    def _handle_did_close(self, params: DidCloseTextDocumentParams) -> None:
        self.documents.pop(params.text_document.uri, None)
        workspace = self._ensure_workspace_index()
        if workspace:
            workspace.reload_document_from_disk(params.text_document.uri)

    def _handle_did_save(self, params: DidSaveTextDocumentParams) -> None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return
        if params.text is not None:
            doc.update_content(params.text)
        else:
            doc.analyze()
        workspace = self._ensure_workspace_index()
        if workspace:
            workspace.replace_document(doc)
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
        return self._hover_for_label_or_scope(doc, workspace, raw_word, word) or self._hover_for_macro(
            doc, workspace, raw_word, word
        )

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
        for container in (doc.labels, doc.macros, doc.symbols):
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
        return results[:100]

    @staticmethod
    def _build_code_mask(lines: list[str]) -> list[list[bool]]:
        """Per-line per-char mask: True where the char is real source code,
        False inside a comment or string literal. Tracks triple-quoted
        strings across lines.

        Lexer is intentionally tiny — a full a816 parse is too expensive
        per rename / reference query and the document already holds the
        AST for diagnostics. This mask exists so reference search skips
        accidental matches in text payloads.
        """
        masks: list[list[bool]] = []
        state = _MaskState()
        for raw in lines:
            masks.append(_mask_line(raw, state))
        return masks

    @staticmethod
    def _refs_in_document(
        document: A816Document, uri: str, pattern: re.Pattern[str], seen: set[tuple[str, int, int]]
    ) -> list[Location]:
        out: list[Location] = []
        masks = A816LanguageServer._build_code_mask(document.lines)
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
            self.server.show_message(str(exc), MessageType.Error)
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
        """Extract semantic tokens from parsed AST nodes only"""
        tokens: list[dict[str, Any]] = []

        logger.debug(f"Processing {len(doc.ast_nodes)} AST nodes for semantic tokens")

        # If no AST nodes, return empty - no fallback
        if not doc.ast_nodes:
            if doc.parse_error:
                logger.warning(f"No semantic tokens generated due to parse error: {doc.parse_error}")
            else:
                logger.warning("No AST nodes found, no semantic tokens generated")
            return []

        # Extract tokens from AST nodes only
        for node in doc.ast_nodes:
            self._visit_node_for_tokens(node, tokens, doc)

        logger.debug(f"Generated {len(tokens)} AST-only tokens")
        return tokens

    _DIRECTIVE_TYPES: ClassVar[tuple[type, ...]] = (
        MacroApplyAstNode,
        CodePositionAstNode,
        MapAstNode,
        IfAstNode,
        MacroAstNode,
        AssignAstNode,
        ExternAstNode,
        ImportAstNode,
        StructAstNode,
        DataNode,
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
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for child in body:
                if isinstance(child, AstNode):
                    self._visit_node_for_tokens(child, tokens, doc)
        elif isinstance(body, AstNode):
            self._visit_node_for_tokens(body, tokens, doc)
        block = getattr(node, "block", None)
        if isinstance(block, AstNode):
            self._visit_node_for_tokens(block, tokens, doc)
        else_block = getattr(node, "else_block", None)
        if isinstance(else_block, AstNode):
            self._visit_node_for_tokens(else_block, tokens, doc)

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
        """Analyze expression nodes for symbols and identifiers"""
        try:
            """This is borked"""
            # Check if the expression represents a symbol/identifier
            for expr_part in expr_node.tokens:
                if isinstance(expr_part, Term):
                    expr_token = expr_part.token
                    if expr_token.position:
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
                            case TokenType.IDENTIFIER:
                                tokens.append(
                                    {
                                        "line": expr_token.position.line,
                                        "char": expr_token.position.column,
                                        "length": len(expr_token.value),
                                        "type": 6,  # variable
                                    }
                                )

        except (AttributeError, KeyError, IndexError, TypeError) as e:
            logger.debug(f"Error analyzing expression tokens: {e}")

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

        Search order:
        1. Same directory as current file
        2. module_paths configured in a816.toml
        """
        try:
            current_dir = uri_to_path(current_uri).parent
            module_file = module_name + ".s"

            candidate = (current_dir / module_file).resolve()
            if candidate.exists():
                return str(candidate)

            workspace = self.workspace_index
            if workspace:
                for search_dir in workspace.module_paths:
                    workspace_candidate = (search_dir / module_file).resolve()
                    if workspace_candidate.exists():
                        return str(workspace_candidate)

            return None

        except (OSError, ValueError) as e:
            logger.debug(f"Error resolving module path '{module_name}' from '{current_uri}': {e}")
            return None

    def _apply_text_change(self, content: str, change: TextDocumentContentChangeEvent_Type1) -> str:
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
        self.server.publish_diagnostics(uri, diagnostics)

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
