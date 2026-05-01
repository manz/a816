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
    Range,
    ReferenceParams,
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
    ScopeAstNode,
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
        self.externs: set[str] = set()  # extern symbol names (declarations, not definitions)
        self.macro_params: dict[str, list[str]] = {}  # macro_name -> parameter_names
        self.macro_docstrings: dict[str, str] = {}
        self.scope_docstrings: dict[str, str] = {}
        self.label_docstrings: dict[str, str] = {}
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
        self.externs.clear()
        self.macro_params.clear()
        self.macro_docstrings.clear()
        self.scope_docstrings.clear()
        self.label_docstrings.clear()
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
            self.macro_docstrings[node.name.lower()] = node.docstring

    def _record_import(self, node: ImportAstNode) -> None:
        if node.module_name and node.module_name not in self.imports:
            self.imports.append(node.module_name)

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
                self.scope_docstrings[node.name.lower()] = node.docstring
        elif isinstance(node, MacroAstNode):
            self._record_macro(node)
        elif isinstance(node, ImportAstNode):
            self._record_import(node)
        elif isinstance(node, IncludeAstNode):
            self._visit_include(node)
            return

        self._visit_children(node)

    def _collect_docstrings(self) -> None:
        """Associate docstrings with labels, macros, and scopes"""
        self.label_docstrings.clear()

        def set_doc(target: tuple[str, str], text: str) -> None:
            cleaned = text.strip()
            if not cleaned:
                return
            kind, name = target
            if kind == "label":
                self.label_docstrings[name] = cleaned
            elif kind == "macro":
                self.macro_docstrings[name] = cleaned
            elif kind == "scope":
                self.scope_docstrings[name] = cleaned

        def collect_nodes(
            nodes: list[AstNode], pending_doc: str | None = None, pending_target: tuple[str, str] | None = None
        ) -> tuple[str | None, tuple[str, str] | None]:
            doc_buffer = pending_doc
            target = pending_target

            for node in nodes:
                if isinstance(node, DocstringAstNode):
                    text = node.text.strip()
                    if not text:
                        continue
                    if target:
                        set_doc(target, text)
                        target = None
                    else:
                        doc_buffer = text
                    continue

                doc_buffer, target = handle_node(node, doc_buffer, target)

            return doc_buffer, target

        def collect_block(
            block: AstNode | list[AstNode] | None,
            pending_doc: str | None = None,
            pending_target: tuple[str, str] | None = None,
        ) -> tuple[str | None, tuple[str, str] | None]:
            if block is None:
                return pending_doc, pending_target
            if isinstance(block, BlockAstNode):
                return collect_nodes(block.body, pending_doc, pending_target)
            if isinstance(block, CompoundAstNode):
                return collect_nodes(block.body, pending_doc, pending_target)
            if isinstance(block, list):
                return collect_nodes(block, pending_doc, pending_target)
            if isinstance(block, AstNode):
                return collect_nodes([block], pending_doc, pending_target)
            return pending_doc, pending_target

        def handle_node(
            node: AstNode, doc_buffer: str | None, target: tuple[str, str] | None
        ) -> tuple[str | None, tuple[str, str] | None]:
            buffer = doc_buffer
            pending_target = target

            if isinstance(node, LabelAstNode):
                key = ("label", node.label.lower())
                if buffer:
                    set_doc(key, buffer)
                    buffer = None
                    pending_target = None
                else:
                    pending_target = key
                return buffer, pending_target

            if isinstance(node, MacroAstNode):
                key = ("macro", node.name.lower())
                doc_text = (node.docstring or buffer or "").strip()
                if doc_text:
                    set_doc(key, doc_text)
                    buffer = None
                    pending_target = None
                else:
                    pending_target = key
                collect_block(node.block)
                return buffer, pending_target

            if isinstance(node, ScopeAstNode):
                key = ("scope", node.name.lower())
                doc_text = (node.docstring or buffer or "").strip()
                if doc_text:
                    set_doc(key, doc_text)
                    buffer = None
                    pending_target = None
                else:
                    pending_target = key
                collect_block(node.body)
                return buffer, pending_target

            if isinstance(node, CompoundAstNode):
                return collect_nodes(node.body, buffer, pending_target)

            if isinstance(node, BlockAstNode):
                return collect_nodes(node.body, buffer, pending_target)

            if isinstance(node, IncludeAstNode):
                included_nodes = [child for child in node.included_nodes if isinstance(child, AstNode)]
                collect_nodes(included_nodes)
                return buffer, None

            if isinstance(node, CommentAstNode):
                return buffer, pending_target

            if hasattr(node, "body") and isinstance(node.body, list):
                collect_nodes(node.body)
            if hasattr(node, "block"):
                collect_block(node.block)
            if hasattr(node, "else_block"):
                collect_block(node.else_block)

            return buffer, None

        collect_nodes(self.ast_nodes)

    def _get_file_uri_for_token(self, token: Token) -> str:
        """Get the file URI for a token, handling both current and included files"""
        if token.position:
            token_filename = token.position.file.filename
            # Convert file path to URI if it's not already one
            if not token_filename.startswith(FILE_URI_PREFIX):
                try:
                    # Handle relative paths by resolving them first
                    if not os.path.isabs(token_filename):
                        # Get the directory of the current document
                        current_doc_path = (
                            Path(self.uri.replace(FILE_URI_PREFIX, ""))
                            if self.uri.startswith(FILE_URI_PREFIX)
                            else Path(self.uri)
                        )
                        if current_doc_path.is_file():
                            base_dir = current_doc_path.parent
                        else:
                            base_dir = current_doc_path

                        # Resolve the relative path
                        resolved_path = (base_dir / token_filename).resolve()
                        return resolved_path.as_uri()
                    else:
                        # Absolute path
                        return Path(token_filename).as_uri()
                except (ValueError, OSError) as e:
                    # If path resolution fails, fall back to current document URI
                    logger.debug(f"Failed to resolve token file path '{token_filename}': {e}")
                    return self.uri
            return token_filename
        # Default to current document URI
        return self.uri

    def _generate_diagnostics(self) -> None:
        """Generate diagnostics from parse errors and AST analysis"""
        # Add parse error as diagnostic if present
        if self.parse_error:
            self._add_parse_error_diagnostic()

        # Additional AST-based diagnostics could be added here
        # For now, we rely on the parser for most error detection

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

    def _entry_from_pragma(self) -> Path | None:
        if not self.root_path:
            return None
        for path in sorted(self.root_path.rglob("*.s")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for _ in range(64):
                        line = handle.readline()
                        if not line:
                            break
                        if self.ENTRYPOINT_PRAGMA in line:
                            return path.resolve()
            except OSError:
                continue
        return None

    def _entry_from_config(self) -> Path | None:
        if not self.root_path or tomllib is None:
            return None

        current = self.root_path
        config_file: Path | None = None

        while True:
            candidate = current / "a816.toml"
            if candidate.exists():
                config_file = candidate
                break
            if current.parent == current:
                break
            current = current.parent

        if not config_file:
            return None

        try:
            with config_file.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None

        config_root = config_file.parent

        # Read include-paths from config
        for p in data.get("include-paths", []):
            resolved = (config_root / p).resolve()
            if resolved not in self.include_paths:
                self.include_paths.append(resolved)

        # Read module-paths from config
        for p in data.get("module-paths", []):
            resolved = (config_root / p).resolve()
            if resolved not in self.module_paths:
                self.module_paths.append(resolved)

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

    def _explore_from(self, entrypoint: Path) -> None:
        queue: list[Path] = [entrypoint.resolve()]
        visited: set[Path] = set()
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            if not current.exists():
                logger.debug("WorkspaceIndex: include not found %s", current)
                continue
            try:
                content = current.read_text(encoding="utf-8")
            except OSError:
                logger.debug("WorkspaceIndex: unable to read %s", current)
                continue
            doc = A816Document(current.as_uri(), content, include_paths=self.include_paths)
            self._store_document(doc)
            for include in self._extract_includes(current, content):
                if include not in visited:
                    queue.append(include)

    def _extract_includes(self, file_path: Path, content: str) -> list[Path]:
        found_paths: set[Path] = set()

        # Extract .include directives
        for match in re.finditer(r"\.include\s+['\"]([^'\"]+)['\"]", content, re.IGNORECASE):
            raw_path = match.group(1).strip()
            # Try relative to parent file first
            candidate = (file_path.parent / raw_path).resolve()
            if candidate.exists():
                found_paths.add(candidate)
            else:
                # Fall back to configured include paths
                for search_dir in self.include_paths:
                    candidate = (search_dir / raw_path).resolve()
                    if candidate.exists():
                        found_paths.add(candidate)
                        break

        # Extract .import directives and resolve to source files
        for match in re.finditer(r"\.import\s+['\"]([^'\"]+)['\"]", content, re.IGNORECASE):
            module_name = match.group(1).strip()
            module_file = module_name + ".s"
            found = False

            # 1. Check same directory as file
            candidate = (file_path.parent / module_file).resolve()
            if candidate.exists():
                found_paths.add(candidate)
                found = True

            # 2. Check configured module paths
            if not found:
                for search_dir in self.module_paths:
                    candidate = (search_dir / module_file).resolve()
                    if candidate.exists():
                        found_paths.add(candidate)
                        found = True
                        break

        return list(found_paths)

    def _store_document(self, doc: A816Document) -> None:
        if not doc.uri:
            return
        self.documents[doc.uri] = doc
        new_label_names = set(doc.labels.keys())
        new_symbol_names = set(doc.symbols.keys())
        new_macro_names = set(doc.macros.keys())

        for label in new_label_names:
            position, uri = doc.labels[label]
            self.labels[label] = (position, uri)
            self.label_name_lookup[label.lower()] = label

        for symbol in new_symbol_names:
            position, uri = doc.symbols[symbol]
            self.symbols[symbol] = (position, uri)

        for macro in new_macro_names:
            position, uri = doc.macros[macro]
            self.macros[macro] = (position, uri)
            self.macro_name_lookup[macro.lower()] = macro
            if macro in doc.macro_params:
                self.macro_params[macro] = doc.macro_params[macro]

        if doc.label_docstrings:
            for key, value in doc.label_docstrings.items():
                self.label_docstrings[key] = value

        if doc.macro_docstrings:
            for key, value in doc.macro_docstrings.items():
                self.macro_docstrings[key] = value

        if doc.scope_docstrings:
            for key, value in doc.scope_docstrings.items():
                self.scope_docstrings[key] = value
                self.scope_name_lookup[key] = key  # already lower-case

        self.doc_labels[doc.uri] = new_label_names
        self.doc_symbols[doc.uri] = new_symbol_names
        self.doc_macros[doc.uri] = new_macro_names
        self.doc_label_docstrings[doc.uri] = set(doc.label_docstrings.keys())
        self.doc_macro_docstrings[doc.uri] = set(doc.macro_docstrings.keys())
        self.doc_scope_docstrings[doc.uri] = set(doc.scope_docstrings.keys())
        self.doc_macro_params[doc.uri] = set(doc.macro_params.keys())

    def _prune_previous_entries(self, uri: str) -> None:
        for label in self.doc_labels.get(uri, set()):
            entry = self.labels.get(label)
            if entry and entry[1] == uri:
                self.labels.pop(label, None)
                self.label_name_lookup.pop(label.lower(), None)
        for symbol in self.doc_symbols.get(uri, set()):
            entry = self.symbols.get(symbol)
            if entry and entry[1] == uri:
                self.symbols.pop(symbol, None)
        for macro in self.doc_macros.get(uri, set()):
            entry = self.macros.get(macro)
            if entry and entry[1] == uri:
                self.macros.pop(macro, None)
                self.macro_name_lookup.pop(macro.lower(), None)
            if macro in self.macro_params:
                self.macro_params.pop(macro, None)
        for key in self.doc_label_docstrings.get(uri, set()):
            self.label_docstrings.pop(key, None)
        for key in self.doc_macro_docstrings.get(uri, set()):
            self.macro_docstrings.pop(key, None)
        for key in self.doc_scope_docstrings.get(uri, set()):
            self.scope_docstrings.pop(key, None)
            self.scope_name_lookup.pop(key, None)
        self.doc_labels.pop(uri, None)
        self.doc_symbols.pop(uri, None)
        self.doc_macros.pop(uri, None)
        self.doc_label_docstrings.pop(uri, None)
        self.doc_macro_docstrings.pop(uri, None)
        self.doc_scope_docstrings.pop(uri, None)
        self.doc_macro_params.pop(uri, None)


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
        """Setup all LSP handlers"""

        @self.server.feature("textDocument/didOpen")
        async def did_open(ls: LanguageServer, params: DidOpenTextDocumentParams) -> None:
            """Handle document open event"""
            workspace = self._ensure_workspace_index()
            include_paths = workspace.include_paths if workspace else []
            doc = A816Document(params.text_document.uri, params.text_document.text, include_paths=include_paths)
            self.documents[params.text_document.uri] = doc
            if workspace:
                workspace.replace_document(doc)

            # Send diagnostics
            self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

        @self.server.feature("textDocument/didChange")
        async def did_change(ls: LanguageServer, params: DidChangeTextDocumentParams) -> None:
            """Handle document change event with proper incremental updates"""
            doc = self.documents.get(params.text_document.uri)
            if not doc or not params.content_changes:
                return

            # Apply all content changes in order
            current_content = doc.content

            for change in params.content_changes:
                if isinstance(change, TextDocumentContentChangeEvent_Type2):
                    # Full document replacement
                    current_content = change.text
                    logger.debug("Full document replacement")
                else:
                    # Incremental change - apply to current content
                    logger.debug(f"Incremental change at {change.range.start.line}:{change.range.start.character}")
                    current_content = self._apply_text_change(current_content, change)

            # Update document with final content
            doc.update_content(current_content)
            workspace = self._ensure_workspace_index()
            if workspace:
                workspace.replace_document(doc)

            # Send updated diagnostics
            self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

            # IMPORTANT: Trigger semantic token refresh for real-time syntax highlighting
            # This ensures syntax highlighting updates as you type
            try:
                # Send a semantic tokens refresh request to the client using pygls
                self.server.send_notification("workspace/semanticTokens/refresh")
            except (AttributeError, RuntimeError) as e:
                logger.debug(f"Could not refresh semantic tokens: {e}")
                # This is optional - some clients don't support refresh

        @self.server.feature("textDocument/didClose")
        async def did_close(ls: LanguageServer, params: DidCloseTextDocumentParams) -> None:
            """Handle document close event"""
            self.documents.pop(params.text_document.uri, None)
            workspace = self._ensure_workspace_index()
            if workspace:
                workspace.reload_document_from_disk(params.text_document.uri)

        @self.server.feature("textDocument/didSave")
        async def did_save(ls: LanguageServer, params: DidSaveTextDocumentParams) -> None:
            """Handle document save event - re-analyze and publish diagnostics"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return

            # If the save includes text, use it; otherwise re-analyze existing content
            if params.text is not None:
                doc.update_content(params.text)
            else:
                # Re-analyze with current content to ensure diagnostics are fresh
                doc.analyze()

            workspace = self._ensure_workspace_index()
            if workspace:
                workspace.replace_document(doc)

            # Publish updated diagnostics
            self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

        @self.server.feature("textDocument/completion")
        async def completions(ls: LanguageServer, params: CompletionParams) -> CompletionList:
            """Provide completion suggestions"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return CompletionList(is_incomplete=False, items=[])

            line_num = params.position.line
            if line_num >= len(doc.lines):
                return CompletionList(is_incomplete=False, items=[])

            line = doc.lines[line_num]
            char_pos = params.position.character

            # Get current word context
            word_start = char_pos
            while word_start > 0 and line[word_start - 1].isalnum():
                word_start -= 1

            current_word = line[word_start:char_pos].lower()

            # Combine all completions
            all_items = []
            all_items.extend(self._opcode_completions)
            all_items.extend(self._keyword_completions)
            all_items.extend(self._register_completions)
            all_items.extend(self._build_labels_completions(doc))
            # Add local symbols, labels, and macros
            for label in doc.labels:
                label_doc = doc.label_docstrings.get(label.lower())
                all_items.append(
                    CompletionItem(
                        label=label,
                        kind=CompletionItemKind.Function,
                        detail="Label",
                        documentation=label_doc,
                    )
                )

            for symbol in doc.symbols:
                all_items.append(CompletionItem(label=symbol, kind=CompletionItemKind.Variable, detail="Symbol"))

            for macro_name in doc.macros:
                macro_parameters = doc.macro_params.get(macro_name, [])
                param_sig = f"({', '.join(macro_parameters)})" if params else ""
                macro_doc = doc.macro_docstrings.get(macro_name.lower())
                documentation = macro_doc or (
                    f"User-defined macro with {len(macro_parameters)} parameters"
                    if macro_parameters
                    else "User-defined macro"
                )
                all_items.append(
                    CompletionItem(
                        label=macro_name,
                        kind=CompletionItemKind.Function,
                        detail=f"User Macro{param_sig}",
                        documentation=documentation,
                    )
                )
            workspace = self._ensure_workspace_index()
            if workspace:
                all_items.extend(self._build_workspace_label_completions(doc, workspace))
                all_items.extend(self._build_workspace_symbol_completions(doc, workspace))
                all_items.extend(self._build_workspace_macro_completions(doc, workspace))

            # Filter by current word
            if current_word:
                filtered_items = [item for item in all_items if item.label.lower().startswith(current_word)]
            else:
                filtered_items = all_items

            return CompletionList(is_incomplete=False, items=filtered_items[:50])  # Limit results

        @self.server.feature("textDocument/hover")
        async def hover(ls: LanguageServer, params: HoverParams) -> Hover | None:
            """Provide hover information"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            line_num = params.position.line
            if line_num >= len(doc.lines):
                return None

            line = doc.lines[line_num]
            char_pos = params.position.character

            # Find word at position
            word_start = char_pos
            while word_start > 0 and (line[word_start - 1].isalnum() or line[word_start - 1] == "_"):
                word_start -= 1

            word_end = char_pos
            while word_end < len(line) and (line[word_end].isalnum() or line[word_end] == "_"):
                word_end += 1

            if word_start >= word_end:
                return None

            raw_word = line[word_start:word_end]
            word = raw_word.lower()

            # Check if it's an opcode
            base_word = word.split(".")[0] if "." in word else word
            if base_word in snes_opcode_table:
                return Hover(
                    contents=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f"**{word.upper()}** - 65c816 Instruction\n\nSupported addressing modes: {len(snes_opcode_table[base_word])}",
                    )
                )

            # Check if it's a keyword
            if base_word in KEYWORDS:
                return Hover(
                    contents=MarkupContent(kind=MarkupKind.Markdown, value=f"**{word.upper()}** - Assembler directive")
                )

            workspace = self._ensure_workspace_index()

            label_doc = doc.label_docstrings.get(word)
            if not label_doc and workspace:
                label_doc = workspace.get_label_doc(raw_word)
            if label_doc:
                return Hover(
                    contents=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f"**{raw_word}**\n\n{label_doc}",
                    )
                )

            scope_doc = doc.scope_docstrings.get(word)
            if not scope_doc and workspace:
                scope_doc = workspace.get_scope_doc(raw_word)
            if scope_doc:
                return Hover(
                    contents=MarkupContent(
                        kind=MarkupKind.Markdown,
                        value=f"**{raw_word}**\n\n{scope_doc}",
                    )
                )

            macro_name = next((name for name in doc.macros if name.lower() == word), None)
            if macro_name:
                doc_text = doc.macro_docstrings.get(word)
                if doc_text:
                    param_list = doc.macro_params.get(macro_name, [])
                    params_str = f"({', '.join(param_list)})" if param_list else "()"
                    return Hover(
                        contents=MarkupContent(
                            kind=MarkupKind.Markdown,
                            value=f"**{macro_name}{params_str}**\n\n{doc_text}",
                        )
                    )

            if workspace:
                macro_location = workspace.get_macro_location(raw_word)
                if macro_location:
                    _, actual_macro_name = macro_location
                    doc_text = workspace.get_macro_doc(actual_macro_name)
                    if doc_text:
                        param_list = workspace.get_macro_params(actual_macro_name)
                        params_str = f"({', '.join(param_list)})" if param_list else "()"
                        return Hover(
                            contents=MarkupContent(
                                kind=MarkupKind.Markdown,
                                value=f"**{actual_macro_name}{params_str}**\n\n{doc_text}",
                            )
                        )

            return None

        @self.server.feature("textDocument/documentSymbol")
        async def document_symbols(ls: LanguageServer, params: DocumentSymbolParams) -> list[DocumentSymbol]:
            """Provide document symbols (labels, macros, and symbols)"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return []

            symbols = []

            # Add labels
            for label, (pos, file_uri) in doc.labels.items():
                # Only include symbols from current document
                if file_uri == params.text_document.uri:
                    symbols.append(
                        DocumentSymbol(
                            name=label,
                            kind=SymbolKind.Function,
                            range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(label))),
                            selection_range=Range(
                                start=pos, end=Position(line=pos.line, character=pos.character + len(label))
                            ),
                        )
                    )

            # Add macros
            for macro_name, (pos, file_uri) in doc.macros.items():
                # Only include symbols from current document
                if file_uri == params.text_document.uri:
                    param_info = ""
                    if macro_name in doc.macro_params:
                        macro_parameters = doc.macro_params[macro_name]
                        param_info = f"({', '.join(macro_parameters)})" if macro_parameters else "()"
                    detail = None
                    doc_text = doc.macro_docstrings.get(macro_name.lower())
                    if doc_text:
                        detail = doc_text.splitlines()[0]

                    symbols.append(
                        DocumentSymbol(
                            name=f"{macro_name}{param_info}",
                            kind=SymbolKind.Method,
                            range=Range(
                                start=pos, end=Position(line=pos.line, character=pos.character + len(macro_name))
                            ),
                            selection_range=Range(
                                start=pos, end=Position(line=pos.line, character=pos.character + len(macro_name))
                            ),
                            detail=detail,
                        )
                    )

            # Add symbols/variables
            for symbol, (pos, file_uri) in doc.symbols.items():
                # Only include symbols from current document
                if file_uri == params.text_document.uri:
                    symbols.append(
                        DocumentSymbol(
                            name=symbol,
                            kind=SymbolKind.Variable,
                            range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(symbol))),
                            selection_range=Range(
                                start=pos, end=Position(line=pos.line, character=pos.character + len(symbol))
                            ),
                        )
                    )

            return symbols

        @self.server.feature("textDocument/definition")
        async def go_to_definition(ls: LanguageServer, params: TextDocumentPositionParams) -> list[Location] | None:
            """Provide go-to-definition for labels, macros, and symbols"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            line_num = params.position.line
            if line_num >= len(doc.lines):
                return None

            line = doc.lines[line_num]
            char_pos = params.position.character

            # Find word at position
            word_start = char_pos
            while word_start > 0 and (line[word_start - 1].isalnum() or line[word_start - 1] == "_"):
                word_start -= 1

            word_end = char_pos
            while word_end < len(line) and (line[word_end].isalnum() or line[word_end] == "_"):
                word_end += 1

            if word_start >= word_end:
                return None

            word = line[word_start:word_end]

            # If word is an extern declaration, skip local lookups and find actual definition
            is_extern = word in doc.externs

            # Check if it's a known label (skip if extern - it won't be here anyway)
            if not is_extern and word in doc.labels:
                pos, file_uri = doc.labels[word]
                return [
                    Location(
                        uri=file_uri,
                        range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(word))),
                    )
                ]

            # Check if it's a known macro
            if not is_extern and word in doc.macros:
                pos, file_uri = doc.macros[word]
                return [
                    Location(
                        uri=file_uri,
                        range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(word))),
                    )
                ]

            # Check if it's a known symbol (skip if extern - we want the actual definition)
            if not is_extern and word in doc.symbols:
                pos, file_uri = doc.symbols[word]
                return [
                    Location(
                        uri=file_uri,
                        range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(word))),
                    )
                ]

            # Look in workspace for cross-file definitions (including extern symbols)
            workspace = self._ensure_workspace_index()
            if workspace:
                label_location = workspace.get_label_location(word)
                if label_location:
                    return [label_location]
                symbol_location = workspace.get_symbol_location(word)
                if symbol_location:
                    return [symbol_location]
                macro_location = workspace.get_macro_location(word)
                if macro_location:
                    location, _ = macro_location
                    return [location]

            # Check if we're on an .include directive
            include_location = self._check_include_directive(doc, line_num, char_pos, params.text_document.uri)
            if include_location:
                return [include_location]

            return None

        @self.server.feature("workspace/symbol")
        async def workspace_symbol(ls: LanguageServer, params: WorkspaceSymbolParams) -> list[SymbolInformation]:
            """Provide workspace-wide symbol lookup"""
            workspace = self._ensure_workspace_index()
            if not workspace:
                return []

            query = (params.query or "").strip().lower()
            results: list[SymbolInformation] = []

            def matches(name: str) -> bool:
                return not query or query in name.lower()

            for label, (position, uri) in workspace.labels.items():
                if not matches(label):
                    continue
                end = Position(line=position.line, character=position.character + len(label))
                container = self._workspace_container_name(uri, workspace)
                results.append(
                    SymbolInformation(
                        name=label,
                        kind=SymbolKind.Function,
                        location=Location(uri=uri, range=Range(start=position, end=end)),
                        container_name=container,
                    )
                )

            for symbol, (position, uri) in workspace.symbols.items():
                if not matches(symbol):
                    continue
                end = Position(line=position.line, character=position.character + len(symbol))
                container = self._workspace_container_name(uri, workspace)
                results.append(
                    SymbolInformation(
                        name=symbol,
                        kind=SymbolKind.Variable,
                        location=Location(uri=uri, range=Range(start=position, end=end)),
                        container_name=container,
                    )
                )

            for macro, (position, uri) in workspace.macros.items():
                if not matches(macro):
                    continue
                end = Position(line=position.line, character=position.character + len(macro))
                container = self._workspace_container_name(uri, workspace)
                results.append(
                    SymbolInformation(
                        name=macro,
                        kind=SymbolKind.Method,
                        location=Location(uri=uri, range=Range(start=position, end=end)),
                        container_name=container,
                    )
                )

            return results[:100]

        @self.server.feature("textDocument/references")
        async def find_references(ls: LanguageServer, params: ReferenceParams) -> list[Location] | None:
            """Find all references to a symbol"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            line_num = params.position.line
            if line_num >= len(doc.lines):
                return None

            line = doc.lines[line_num]
            char_pos = params.position.character

            # Find word at position
            word_start = char_pos
            while word_start > 0 and (line[word_start - 1].isalnum() or line[word_start - 1] == "_"):
                word_start -= 1

            word_end = char_pos
            while word_end < len(line) and (line[word_end].isalnum() or line[word_end] == "_"):
                word_end += 1

            if word_start >= word_end:
                return None

            word = line[word_start:word_end]

            # Find all references to this word
            include_declaration = getattr(getattr(params, "context", None), "include_declaration", True)
            pattern = re.compile(r"\b" + re.escape(word) + r"\b")

            references: list[Location] = []
            seen: set[tuple[str, int, int]] = set()

            def add_references_from_document(document: A816Document, uri: str) -> None:
                for i, doc_line in enumerate(document.lines):
                    for match in pattern.finditer(doc_line):
                        start = Position(line=i, character=match.start())
                        end = Position(line=i, character=match.end())
                        key = (uri, i, match.start())
                        if key in seen:
                            continue
                        references.append(Location(uri=uri, range=Range(start=start, end=end)))
                        seen.add(key)

            add_references_from_document(doc, params.text_document.uri)

            workspace = self._ensure_workspace_index()
            if workspace:
                for uri, workspace_doc in workspace.documents.items():
                    if uri == params.text_document.uri:
                        continue
                    add_references_from_document(workspace_doc, uri)

            if not references:
                return None

            if not include_declaration:
                definition_locations: set[tuple[str, int, int]] = set()

                def collect_definition_locations(document: A816Document, uri: str) -> None:
                    for container in (document.labels, document.symbols, document.macros):
                        for name, (position, _) in container.items():
                            if name == word:
                                definition_locations.add((uri, position.line, position.character))

                collect_definition_locations(doc, params.text_document.uri)

                if workspace:
                    for name, (position, uri) in workspace.labels.items():
                        if name == word:
                            definition_locations.add((uri, position.line, position.character))
                    for name, (position, uri) in workspace.symbols.items():
                        if name == word:
                            definition_locations.add((uri, position.line, position.character))
                    for name, (position, uri) in workspace.macros.items():
                        if name == word:
                            definition_locations.add((uri, position.line, position.character))

                references = [
                    loc
                    for loc in references
                    if (loc.uri, loc.range.start.line, loc.range.start.character) not in definition_locations
                ]

            return references if references else None

        @self.server.feature("textDocument/signatureHelp")
        async def signature_help(ls: LanguageServer, params: SignatureHelpParams) -> SignatureHelp | None:
            """Provide signature help for instructions"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            line_num = params.position.line
            if line_num >= len(doc.lines):
                return None

            line = doc.lines[line_num][: params.position.character]

            # Find the current instruction
            words = line.strip().split()
            if not words:
                return None

            # Skip label if present
            if words[0].endswith(":"):
                words = words[1:]

            if not words:
                return None

            opcode = words[0].lower()
            base_opcode = opcode.split(".")[0] if "." in opcode else opcode

            if base_opcode in snes_opcode_table:
                addressing_modes = snes_opcode_table[base_opcode]
                mode_descriptions = []

                for mode, _ in addressing_modes.items():
                    if mode == AddressingMode.none:
                        mode_descriptions.append("No operand")
                    elif mode == AddressingMode.immediate:
                        mode_descriptions.append("#value")
                    elif mode == AddressingMode.direct:
                        mode_descriptions.append("address")
                    elif mode == AddressingMode.direct_indexed:
                        mode_descriptions.append("address,X or address,Y")
                    elif mode == AddressingMode.indirect:
                        mode_descriptions.append("(address)")
                    elif mode == AddressingMode.indirect_indexed:
                        mode_descriptions.append("(address),Y")
                    elif mode == AddressingMode.indirect_long:
                        mode_descriptions.append("[address]")
                    else:
                        mode_descriptions.append(str(mode.name))

                signature_info = SignatureInformation(
                    label=f"{opcode.upper()}",
                    documentation=f"Supported addressing modes: {', '.join(mode_descriptions)}",
                )

                return SignatureHelp(signatures=[signature_info], active_signature=0, active_parameter=0)

            return None

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
            """Provide semantic tokens for syntax highlighting"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            tokens = self._analyze_semantic_tokens(doc)
            return SemanticTokens(data=tokens)

        @self.server.feature("textDocument/formatting")
        async def format_document(ls: LanguageServer, params: DocumentFormattingParams) -> list[TextEdit] | None:
            """Format entire document"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            # Create formatting options from LSP formatting options
            formatting_options = self._create_formatting_options(params.options)
            original_formatter = self.formatter
            self.formatter = A816Formatter(formatting_options)

            try:
                # Format the document content
                formatted_content = self.formatter.format_text(doc.content)

                # Create a text edit that replaces the entire document
                if formatted_content != doc.content:
                    full_range = self._full_document_range(doc)
                    return [TextEdit(range=full_range, new_text=formatted_content)]
                else:
                    return []
            except FormattingError as exc:
                logger.error("Formatter failed for %s: %s", doc.uri, exc)
                self.server.show_message(str(exc), MessageType.Error)
                return []
            finally:
                # Restore original formatter
                self.formatter = original_formatter

        @self.server.feature("textDocument/rangeFormatting")
        async def format_range(ls: LanguageServer, params: DocumentRangeFormattingParams) -> list[TextEdit] | None:
            """Format document range"""
            doc = self.documents.get(params.text_document.uri)
            if not doc:
                return None

            # For simplicity, format the entire document for now
            # In a more advanced implementation, you could extract and format only the selected range
            return await format_document(
                ls, DocumentFormattingParams(text_document=params.text_document, options=params.options)
            )

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

    def _visit_node_for_tokens(self, node: AstNode, tokens: list[dict[str, Any]], doc: A816Document) -> None:
        """Recursively visit AST nodes to extract semantic tokens"""
        try:
            if not node.file_info or not node.file_info.position:
                return

            # Check if this node's file matches the current document URI
            if node.file_info.position.file.filename:
                # Normalize paths for comparison
                node_file = node.file_info.position.file.filename
                doc_file = doc.uri

                if node_file != doc_file:
                    return

            pos = node.file_info.position
            token_text = node.file_info.value

            # Map AST node types to semantic token types
            if isinstance(node, LabelAstNode):
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 1,  # function (label)
                    }
                )
            elif isinstance(node, OpcodeAstNode):
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 0,  # keyword (opcode)
                    }
                )
                if node.operand:
                    self._visit_node_for_tokens(node.operand, tokens, doc)
                    return
            elif isinstance(node, CommentAstNode):
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 2,  # comment
                    }
                )
            elif isinstance(node, DocstringAstNode):
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 4,  # string
                    }
                )
                return
            elif isinstance(node, IncludeAstNode):
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 7,  # macro (directive)
                    }
                )
                return
            elif isinstance(
                node,
                MacroApplyAstNode
                | CodePositionAstNode
                | MapAstNode
                | IfAstNode
                | MacroAstNode
                | AssignAstNode
                | ExternAstNode
                | ImportAstNode
                | DataNode,
            ):
                # Macro calls, assembler directives, and data declarations.
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 7,
                    }
                )
            elif isinstance(node, ExpressionAstNode):
                # Handle symbols and identifiers in expressions
                self._analyze_expression_tokens(node, tokens)
                return  # Don't process children as we handle them in _analyze_expression_tokens

            # Handle compound nodes with child nodes
            if hasattr(node, "body"):
                if isinstance(node.body, list):
                    for child in node.body:
                        if isinstance(child, AstNode):
                            self._visit_node_for_tokens(child, tokens, doc)
                elif isinstance(node.body, AstNode):
                    self._visit_node_for_tokens(node.body, tokens, doc)

            # Handle other node types with child nodes
            if hasattr(node, "block") and node.block:
                self._visit_node_for_tokens(node.block, tokens, doc)
            if hasattr(node, "else_block") and node.else_block:
                self._visit_node_for_tokens(node.else_block, tokens, doc)

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
                resolved_path = Path(include_path).resolve()
                return str(resolved_path) if resolved_path.exists() else str(resolved_path)

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
