"""`A816Document`: per-file analysis surface used by the LSP server.

Owns the AST + symbol/label/macro/pool indexes, docstring association
state, and diagnostic generation for a single open file. The LSP server
holds one instance per document and queries it for hover / definition /
completion data.
"""

from __future__ import annotations

import inspect
import logging
import os
from pathlib import Path

from lsprotocol.types import Diagnostic, DiagnosticSeverity, Position, Range

from a816.parse.ast.nodes import (
    AllocAstNode,
    AssignAstNode,
    AstNode,
    BlockAstNode,
    CastValueExprNode,
    CommentAstNode,
    CompoundAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    ExternAstNode,
    ImportAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    LabelAstNode,
    MacroAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.errors import ParseError, ParserSyntaxError, ScannerException
from a816.parse.mzparser import A816Parser
from a816.parse.tokens import Token
from a816.stdlib import resolve_stdlib_module
from a816.util import uri_to_path

logger = logging.getLogger(__name__)

FILE_URI_PREFIX = "file://"


def _struct_node_in_file(path: Path, name: str) -> StructAstNode | None:
    """Parse `path` once and return the first matching `.struct <name>`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    result = A816Parser.parse_as_ast(text, str(path))
    for node in result.nodes:
        if isinstance(node, StructAstNode) and node.name == name:
            return node
    return None


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
        # pool name -> hover-ready summary line (range count, fill, strategy)
        self.pool_details: dict[str, str] = {}
        # alloc / relocate name -> target pool name
        self.alloc_target_pool: dict[str, str] = {}
        # pool name -> list of (position, kind) consumer references in this doc
        # kind ∈ {"alloc", "relocate", "reclaim"}.
        self.pool_consumers: dict[str, list[tuple[Position, str]]] = {}
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
        # LSP-shaped diagnostic → originating fluff hit (carries the fix).
        # CodeAction handler reads this to translate a hit inside a request
        # range into a `WorkspaceEdit`. Only populated for fluff hits; parse
        # errors and pool diagnostics stay outside (no fixes today).
        self.fluff_hits: list[tuple[Diagnostic, object]] = []
        self.ast_nodes: list[AstNode] = []
        self.parse_error: ParseError | None = None
        self.parse_errors: list[ParseError] = []
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
        self.pool_details.clear()
        self.alloc_target_pool.clear()
        self.pool_consumers.clear()
        self.externs.clear()
        self.macro_params.clear()
        self.macro_docstrings.clear()
        self.scope_docstrings.clear()
        self.label_docstrings.clear()
        self.module_docstring = None
        self.imports.clear()
        self.diagnostics.clear()
        self.fluff_hits.clear()
        self.ast_nodes.clear()
        self.parse_error = None
        self.analyze()

    def analyze(self) -> None:
        """Analyze document using the parser and extract symbols, labels, and diagnostics"""
        try:
            # Parse using the actual a816 parser
            parser_result = A816Parser.parse_as_ast(self.content, self.uri, include_paths=self.include_paths)
            self.ast_nodes = parser_result.nodes
            self.parse_error = parser_result.parse_error
            self.parse_errors = list(parser_result.parse_errors or [])
            if self.parse_error is not None and not self.parse_errors:
                self.parse_errors = [self.parse_error]

            # Extract symbols and labels from AST
            self._extract_symbols_from_ast()
            self._collect_docstrings()

        except (ScannerException, ParserSyntaxError) as e:
            # These should be handled by A816Parser.parse_as_ast, but just in case
            self.parse_error = ParseError(
                message=str(e),
                filename=self.uri,
                line=0,
                column=0,
            )
            self.ast_nodes = []
            logger.warning("Parser exception not caught by A816Parser: %s", e)
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
        if not symbol:
            return
        self._record_token_position(node.file_info, self.symbols, symbol)
        self._record_typed_bind_fields(node, symbol)

    def _record_typed_bind_fields(self, node: AstNode, symbol: str) -> None:
        """When the RHS is `(expr as T)`, also index `symbol.field` for every
        field of `T` so `sta.l hdma_ch6.A1TL`-style goto-def lands somewhere.

        Targets the bind site (not the struct field) because that's the
        location the user usually wants to inspect — `hdma_ch6 := ...` is
        the line that anchors why the alias exists.
        """
        if not isinstance(node, AssignAstNode):
            return
        value = getattr(node, "value", None)
        if not isinstance(value, ExpressionAstNode) or len(value.tokens) != 1:
            return
        cast = value.tokens[0]
        if not isinstance(cast, CastValueExprNode):
            return
        if not node.file_info.position:
            return
        bind_pos = Position(line=node.file_info.position.line, character=node.file_info.position.column)
        file_uri = self._get_file_uri_for_token(node.file_info)
        # Walk the type's flat fields, mirroring the codegen-time eager
        # expansion. We need a lookup over StructAstNodes in the document.
        for struct_node in self._iter_struct_nodes(cast.type_name):
            for field_name, _ftype in struct_node.fields:
                self.symbols[f"{symbol}.{field_name}"] = (bind_pos, file_uri)
            return  # only the first matching declaration counts

    def _iter_struct_nodes(self, name: str) -> list[StructAstNode]:
        """Find `.struct <name>` declarations reachable from this document.

        Walks the local AST first; if no match, follows `.import`
        directives (stdlib `@std/...` first, then `include_paths`) and
        parses the target file once. Cycle-safe via `_seen_imports`.
        """
        matches: list[StructAstNode] = []
        for node in self.ast_nodes:
            if isinstance(node, StructAstNode) and node.name == name:
                matches.append(node)
        if matches:
            return matches

        for node in self.ast_nodes:
            if not isinstance(node, ImportAstNode):
                continue
            path = self._resolve_import_for_struct_lookup(node.module_name)
            if path is None:
                continue
            found = _struct_node_in_file(path, name)
            if found is not None:
                matches.append(found)
                break
        return matches

    def _resolve_import_for_struct_lookup(self, module_name: str) -> Path | None:
        stdlib_path = resolve_stdlib_module(module_name, ".s")
        if stdlib_path is not None:
            return stdlib_path
        file_name = module_name + ".s"
        candidates: list[Path] = []
        if self.include_paths:
            candidates.extend(self.include_paths)
        # Document's own directory as a last resort.
        try:
            candidates.append(uri_to_path(self.uri).parent)
        except (OSError, ValueError):
            pass
        for base in candidates:
            candidate = base / file_name
            if candidate.exists():
                return candidate
        return None

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
        """Index `Name.field` plus the auto-generated bit-field aux symbols
        (`.mask` / `.shift`) so goto-def lands on the struct's source line
        for every spelling a user can type in code."""
        import re as _re

        token = node.file_info
        if not token.position:
            return
        pos = Position(line=token.position.line, character=token.position.column)
        file_uri = self._get_file_uri_for_token(token)
        # The bare type name (`PPU`) — used in `(addr as PPU)` casts and
        # nested struct field types — also goto-def's to the declaration.
        self.symbols[node.name] = (pos, file_uri)
        bit_field_re = _re.compile(r"u\d+")
        for field_name, field_type in node.fields:
            self.symbols[f"{node.name}.{field_name}"] = (pos, file_uri)
            if bit_field_re.fullmatch(field_type):
                # `uN` fields publish two extra symbols at codegen time —
                # mirror them here so `Type.field.mask` is also goto-def'able.
                self.symbols[f"{node.name}.{field_name}.mask"] = (pos, file_uri)
                self.symbols[f"{node.name}.{field_name}.shift"] = (pos, file_uri)
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
            self.pool_details[node.pool_name] = self._format_pool_details(node)
        elif isinstance(node, AllocAstNode):
            if node.name is not None and node.pool_name is not None:
                self._record_token_position(node.file_info, self.allocs, node.name)
                self.alloc_target_pool[node.name] = node.pool_name
            if node.pool_name is not None:
                self._record_pool_consumer(node.file_info, node.pool_name, "alloc")
        elif isinstance(node, RelocateAstNode):
            self._record_token_position(node.file_info, self.allocs, node.symbol)
            self.alloc_target_pool[node.symbol] = node.pool_name
            self._record_pool_consumer(node.file_info, node.pool_name, "relocate")
        elif isinstance(node, ReclaimAstNode):
            self._record_pool_consumer(node.file_info, node.pool_name, "reclaim")

    @staticmethod
    def _format_pool_details(node: PoolAstNode) -> str:
        range_count = len(node.ranges)
        fill = node.fill.to_canonical()
        return f"{range_count} range{'s' if range_count != 1 else ''}, fill {fill}, strategy {node.strategy}"

    def _record_pool_consumer(self, token: Token, pool_name: str, kind: str) -> None:
        if not token.position:
            return
        pos = Position(line=token.position.line, character=token.position.column)
        self.pool_consumers.setdefault(pool_name, []).append((pos, kind))

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
        elif isinstance(node, IncludeBinaryAstNode):
            self._record_incbin(node)

        self._visit_children(node)

    def _record_incbin(self, node: IncludeBinaryAstNode) -> None:
        """Index the auto-generated `<sanitized_path>` + `<...>__size` symbols
        so goto-def on either jumps to the `.incbin "..."` line."""
        token = node.file_info
        if not token.position:
            return
        symbol_base = node.file_path.replace("/", "_").replace(".", "_")
        pos = Position(line=token.position.line, character=token.position.column)
        file_uri = self._get_file_uri_for_token(token)
        self.symbols[symbol_base] = (pos, file_uri)
        self.labels[symbol_base] = (pos, file_uri)
        self.symbols[f"{symbol_base}__size"] = (pos, file_uri)

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
        # Pool-name resolution is cross-document: pools live in the
        # workspace's preamble / shared decl files. The undeclared-pool
        # check therefore lives in `WorkspaceIndex` (the only thing
        # that knows the project-wide pool universe), not here. See
        # `WorkspaceIndex.undeclared_pool_diagnostics`.

    def _add_fluff_diagnostics(self) -> None:
        """Surface a816 fluff lint hits as LSP warnings."""
        from urllib.parse import urlparse

        from a816.fluff import lint_text

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
            diagnostic = Diagnostic(
                range=Range(
                    start=Position(line=line, character=column),
                    end=Position(line=line, character=end_col),
                ),
                message=hit.message,
                severity=DiagnosticSeverity.Warning,
                source="a816 fluff",
                code=hit.code,
            )
            self.diagnostics.append(diagnostic)
            self.fluff_hits.append((diagnostic, hit))

    def _clamp_error_span(self, line: int, col: int, length: int) -> tuple[int, int, int]:
        """Clamp a (line, col, length) tuple inside the current document."""
        if not self.lines:
            return 0, 0, length
        if line >= len(self.lines):
            line = len(self.lines) - 1
        if line < 0:
            line = 0
        if col < 0:
            col = 0
        line_length = len(self.lines[line])
        if col >= line_length:
            col = max(0, line_length - 1)
        if col + length > line_length:
            length = max(1, line_length - col)
        return line, col, length

    def _build_diagnostic(self, error: ParseError) -> Diagnostic:
        line, col, length = self._clamp_error_span(error.line, error.column, error.length)
        message = error.message
        if error.hint:
            message = f"{message}\nhint: {error.hint}"
        return Diagnostic(
            range=Range(
                start=Position(line=line, character=col),
                end=Position(line=line, character=col + length),
            ),
            message=message,
            severity=DiagnosticSeverity.Error,
            code=error.code,
            source="a816",
        )

    def _add_parse_error_diagnostic(self) -> None:
        """Convert collected parse error(s) to LSP diagnostics."""
        for err in self.parse_errors or ([self.parse_error] if self.parse_error else []):
            if err is not None:
                self.diagnostics.append(self._build_diagnostic(err))
