import logging
import os
import re
from pathlib import Path
from typing import Any

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
    DocumentFormattingParams,
    DocumentRangeFormattingParams,
    DocumentSymbol,
    DocumentSymbolParams,
    Hover,
    HoverParams,
    Location,
    MarkupContent,
    MarkupKind,
    Position,
    Range,
    ReferenceParams,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
    SignatureHelp,
    SignatureHelpParams,
    SignatureInformation,
    SymbolKind,
    TextDocumentContentChangeEvent_Type1,
    TextDocumentContentChangeEvent_Type2,
    TextDocumentPositionParams,
    TextEdit,
)
from lsprotocol.types import (
    FormattingOptions as LSPFormattingOptions,
)
from pygls.server import LanguageServer

from a816.cpu.cpu_65c816 import AddressingMode, snes_opcode_table
from a816.formatter import A816Formatter, FormattingOptions
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    CodePositionAstNode,
    CommentAstNode,
    DataNode,
    ExpressionAstNode,
    ExternAstNode,
    IfAstNode,
    LabelAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapAstNode,
    OpcodeAstNode,
    SymbolAffectationAstNode,
    Term,
)
from a816.parse.errors import ParserSyntaxError, ScannerException
from a816.parse.mzparser import MZParser
from a816.parse.scanner_states import KEYWORDS
from a816.parse.tokens import Token, TokenType

logger = logging.getLogger(__name__)


class A816Document:
    """Represents an a816 assembly document with analysis capabilities"""

    def __init__(self, uri: str, content: str):
        self.uri = uri
        self.content = content
        self.lines = content.splitlines()
        self.symbols: dict[str, tuple[Position, str]] = {}  # symbol -> (position, file_uri)
        self.labels: dict[str, tuple[Position, str]] = {}  # label -> (position, file_uri)
        self.macros: dict[str, tuple[Position, str]] = {}  # macro -> (position, file_uri)
        self.macro_params: dict[str, list[str]] = {}  # macro_name -> parameter_names
        self.diagnostics: list[Diagnostic] = []
        self.ast_nodes: list[AstNode] = []
        self.parse_error: str | None = None
        self.analyze()

    def update_content(self, content: str) -> None:
        """Update document content and re-analyze"""
        self.content = content
        self.lines = content.splitlines()
        self.symbols.clear()
        self.labels.clear()
        self.macros.clear()
        self.macro_params.clear()
        self.diagnostics.clear()
        self.ast_nodes.clear()
        self.parse_error = None
        self.analyze()

    def analyze(self) -> None:
        """Analyze document using the parser and extract symbols, labels, and diagnostics"""
        try:
            # Parse using the actual a816 parser
            parser_result = MZParser.parse_as_ast(self.content, self.uri)
            self.ast_nodes = parser_result.nodes
            self.parse_error = parser_result.error

            # Extract symbols and labels from AST
            self._extract_symbols_from_ast()

        except (ScannerException, ParserSyntaxError) as e:
            # These should be handled by MZParser.parse_as_ast, but just in case
            self.parse_error = str(e)
            self.ast_nodes = []
            logger.warning("Parser exception not caught by MZParser: %s", e)
        except Exception as e:
            # Catch any other unexpected exceptions
            self.parse_error = f"Unexpected parser error: {str(e)}"
            self.ast_nodes = []
            logger.exception("Unexpected error during document analysis")

        # Generate diagnostics from parse errors and AST analysis
        self._generate_diagnostics()

    def _extract_symbols_from_ast(self) -> None:
        """Extract symbols and labels from the parsed AST"""
        try:
            for node in self.ast_nodes:
                self._visit_node_for_symbols(node)
        except Exception:
            logger.exception("Error extracting symbols from AST")
            # Continue without symbols rather than crashing

    def _visit_node_for_symbols(self, node: AstNode) -> None:
        """Recursively visit AST nodes to extract symbols and labels"""
        if isinstance(node, LabelAstNode):
            # Convert token position to LSP Position
            token = node.file_info
            if token.position:
                pos = Position(line=token.position.line, character=token.position.column)  # Already 0-indexed
                file_uri = self._get_file_uri_for_token(token)
                self.labels[node.label] = (pos, file_uri)
        elif isinstance(node, AssignAstNode | SymbolAffectationAstNode):
            # Extract symbol assignments/definitions
            if hasattr(node, "symbol") and node.symbol:
                token = node.file_info
                if token.position:
                    pos = Position(line=token.position.line, character=token.position.column)
                    file_uri = self._get_file_uri_for_token(token)
                    self.symbols[node.symbol] = (pos, file_uri)
        elif isinstance(node, ExternAstNode):
            # Extract external symbol declarations
            if hasattr(node, "symbol") and node.symbol:
                token = node.file_info
                if token.position:
                    pos = Position(line=token.position.line, character=token.position.column)
                    file_uri = self._get_file_uri_for_token(token)
                    self.symbols[node.symbol] = (pos, file_uri)
        elif isinstance(node, MacroAstNode):
            # Extract macro definitions
            if hasattr(node, "name") and node.name:
                token = node.file_info
                if token.position:
                    pos = Position(line=token.position.line, character=token.position.column)
                    file_uri = self._get_file_uri_for_token(token)
                    self.macros[node.name] = (pos, file_uri)

                    # Extract macro parameters if available
                    if hasattr(node, "parameters") and node.parameters:
                        self.macro_params[node.name] = [
                            param.name for param in node.parameters if hasattr(param, "name")
                        ]
        elif isinstance(node, MacroApplyAstNode):
            # Extract macro applications/calls
            if hasattr(node, "name") and node.name:
                # Mark as a macro usage for semantic highlighting - handled in AST traversal
                pass

        # Handle compound nodes with body attributes
        if hasattr(node, "body"):
            if isinstance(node.body, list):
                for child in node.body:
                    self._visit_node_for_symbols(child)
            else:
                self._visit_node_for_symbols(node.body)

        # Handle other node types with child nodes
        if hasattr(node, "block") and node.block:
            self._visit_node_for_symbols(node.block)
        if hasattr(node, "else_block") and node.else_block:
            self._visit_node_for_symbols(node.else_block)

    def _get_file_uri_for_token(self, token: Token) -> str:
        """Get the file URI for a token, handling both current and included files"""
        if token.position:
            token_filename = token.position.file.filename
            # Convert file path to URI if it's not already one
            if not token_filename.startswith("file://"):
                try:
                    # Handle relative paths by resolving them first
                    if not os.path.isabs(token_filename):
                        # Get the directory of the current document
                        current_doc_path = (
                            Path(self.uri.replace("file://", "")) if self.uri.startswith("file://") else Path(self.uri)
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

        # Try to extract line and column information from error message
        lines = self.parse_error.split("\n")
        error_line = 0
        error_col = 0
        message = self.parse_error

        # Parse error format: "filename:line:column : error message"
        # Handle URI schemes like "test://file.s:1:19 : message"
        if lines and " : " in lines[0]:
            # Split on " : " first to separate the location from message
            location_part, message = lines[0].split(" : ", 1)
            # Now find the last two colons for line:column
            parts = location_part.rsplit(":", 2)
            if len(parts) >= 2:
                try:
                    error_line = max(0, int(parts[-2]))  # Already 0-indexed from parser
                    error_col = max(0, int(parts[-1]))
                except (ValueError, IndexError):
                    pass

        # If we have caret position info, try to use it
        if len(lines) >= 3 and "^" in lines[2]:
            caret_pos = lines[2].find("^")
            if caret_pos >= 0:
                error_col = caret_pos

        # Ensure we don't go beyond document bounds
        if error_line >= len(self.lines):
            error_line = len(self.lines) - 1
        if error_line >= 0 and error_col >= len(self.lines[error_line]):
            error_col = len(self.lines[error_line])

        # Create diagnostic
        self.diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=error_line, character=error_col),
                    end=Position(line=error_line, character=error_col + 1),
                ),
                message=message,
                severity=DiagnosticSeverity.Error,
            )
        )


class A816LanguageServer:
    """Enhanced LSP server for a816 assembly language"""

    def __init__(self) -> None:
        self.server = LanguageServer("a816-language-server", "v1.0")
        self.documents: dict[str, A816Document] = {}
        self.formatter = A816Formatter()
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
            doc = A816Document(params.text_document.uri, params.text_document.text)
            self.documents[params.text_document.uri] = doc

            # Send diagnostics
            await self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

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

            # Send updated diagnostics
            await self._publish_diagnostics(params.text_document.uri, doc.diagnostics)

            # IMPORTANT: Trigger semantic token refresh for real-time syntax highlighting
            # This ensures syntax highlighting updates as you type
            try:
                # Send a semantic tokens refresh request to the client using pygls
                self.server.send_notification("workspace/semanticTokens/refresh")
            except Exception as e:
                logger.debug(f"Could not refresh semantic tokens: {e}")
                # This is optional - some clients don't support refresh

        @self.server.feature("textDocument/didClose")
        async def did_close(ls: LanguageServer, params: DidCloseTextDocumentParams) -> None:
            """Handle document close event"""
            self.documents.pop(params.text_document.uri, None)

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
                all_items.append(CompletionItem(label=label, kind=CompletionItemKind.Function, detail="Label"))

            for symbol in doc.symbols:
                all_items.append(CompletionItem(label=symbol, kind=CompletionItemKind.Variable, detail="Symbol"))

            for macro_name in doc.macros:
                macro_parameters = doc.macro_params.get(macro_name, [])
                param_sig = f"({', '.join(macro_parameters)})" if params else ""
                all_items.append(
                    CompletionItem(
                        label=macro_name,
                        kind=CompletionItemKind.Function,
                        detail=f"User Macro{param_sig}",
                        documentation=f"User-defined macro with {len(macro_parameters)} parameters"
                        if macro_parameters
                        else "User-defined macro",
                    )
                )

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

            word = line[word_start:word_end].lower()

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

            # Check if it's a known label
            if word in doc.labels:
                pos, file_uri = doc.labels[word]
                return [
                    Location(
                        uri=file_uri,
                        range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(word))),
                    )
                ]

            # Check if it's a known macro
            if word in doc.macros:
                pos, file_uri = doc.macros[word]
                return [
                    Location(
                        uri=file_uri,
                        range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(word))),
                    )
                ]

            # Check if it's a known symbol
            if word in doc.symbols:
                pos, file_uri = doc.symbols[word]
                return [
                    Location(
                        uri=file_uri,
                        range=Range(start=pos, end=Position(line=pos.line, character=pos.character + len(word))),
                    )
                ]

            # Check if we're on an .include directive
            include_location = self._check_include_directive(doc, line_num, char_pos, params.text_document.uri)
            if include_location:
                return [include_location]

            return None

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
            references = []
            for i, doc_line in enumerate(doc.lines):
                # Simple word boundary search
                pattern = r"\b" + re.escape(word) + r"\b"
                for match in re.finditer(pattern, doc_line):
                    references.append(
                        Location(
                            uri=params.text_document.uri,
                            range=Range(
                                start=Position(line=i, character=match.start()),
                                end=Position(line=i, character=match.end()),
                            ),
                        )
                    )

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
                    return [
                        TextEdit(
                            range=Range(
                                start=Position(line=0, character=0), end=Position(line=len(doc.lines), character=0)
                            ),
                            new_text=formatted_content,
                        )
                    ]
                else:
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
            space_after_comma=not lsp_options.insert_spaces if hasattr(lsp_options, "insert_spaces") else True,
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
            elif isinstance(node, MacroApplyAstNode):
                # Macro application/call - highlight as user-defined macro
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 7,  # macro (macro call)
                    }
                )
            elif isinstance(
                node, CodePositionAstNode | MapAstNode | IfAstNode | MacroAstNode | AssignAstNode | ExternAstNode
            ):
                # Assembler directives
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 7,  # macro (directive)
                    }
                )
            elif isinstance(node, DataNode):
                # Data declarations like .db, .dw
                tokens.append(
                    {
                        "line": pos.line,
                        "char": pos.column,
                        "length": len(token_text),
                        "type": 7,  # macro (directive)
                    }
                )
            elif isinstance(node, ExpressionAstNode):
                # Handle symbols and identifiers in expressions
                self._analyze_expression_tokens(node, tokens, doc)
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

        except Exception as e:
            logger.debug(f"Error processing AST node {type(node).__name__}: {e}")

    def _analyze_expression_tokens(
        self, expr_node: ExpressionAstNode, tokens: list[dict[str, Any]], doc: A816Document
    ) -> None:
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
                                tokens.append({
                                    "line": expr_token.position.line,
                                    "char": expr_token.position.column,
                                    "length": len(expr_token.value),
                                    "type": 3,  # number
                                })
                            case TokenType.IDENTIFIER:
                                tokens.append({
                                    "line": expr_token.position.line,
                                    "char": expr_token.position.column,
                                    "length": len(expr_token.value),
                                    "type": 6,  # variable
                                })

        except Exception as e:
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
        label_match = re.match(r"^(\s*)([a-zA-Z_][a-zA-Z0-9_]*):(.*)$", line)
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

    def _tokenize_operand(self, operand: str, line_num: int, start_pos: int) -> list[dict[str, Any]]:
        """Tokenize operand for semantic highlighting"""
        tokens: list[dict[str, Any]] = []

        # Simple tokenization for numbers, strings, and registers
        i = 0
        while i < len(operand):
            char = operand[i]

            if char.isspace():
                i += 1
                continue

            # Numbers (hex, decimal, binary)
            if char.isdigit() or char == "0x" or char == "#":
                start = i
                if char == "0x":  # Hex
                    i += 1
                    while i < len(operand) and operand[i] in "0123456789ABCDEFabcdef":
                        i += 1
                elif char == "#":  # Immediate
                    i += 1
                    if i < len(operand) and operand[i] == "0x":
                        i += 1
                        while i < len(operand) and operand[i] in "0123456789ABCDEFabcdef":
                            i += 1
                    else:
                        while i < len(operand) and operand[i].isdigit():
                            i += 1
                else:  # Decimal
                    while i < len(operand) and operand[i].isdigit():
                        i += 1

                tokens.append(
                    {
                        "line": line_num,
                        "char": start_pos + start,
                        "length": i - start,
                        "type": 3,  # number
                    }
                )

            # Registers
            elif char.upper() in "XYS" and (i == 0 or not operand[i - 1].isalnum()):
                tokens.append(
                    {
                        "line": line_num,
                        "char": start_pos + i,
                        "length": 1,
                        "type": 6,  # variable (register)
                    }
                )
                i += 1

            # Operators and punctuation
            elif char in "()[],.+-*&|":
                tokens.append(
                    {
                        "line": line_num,
                        "char": start_pos + i,
                        "length": 1,
                        "type": 5,  # operator
                    }
                )
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

    def _check_include_directive(
        self, doc: A816Document, line_num: int, char_pos: int, current_uri: str
    ) -> Location | None:
        """Check if the cursor is on an .include directive and return the file location"""
        try:
            if line_num >= len(doc.lines):
                return None

            line = doc.lines[line_num].strip()

            # Match .include 'filename' or .include "filename"
            include_match = re.match(r'\.include\s+[\'"]([^\'"]+)[\'"]', line, re.IGNORECASE)
            if not include_match:
                return None

            include_path = include_match.group(1)
            quote_start = line.find('"') if '"' in line else line.find("'")
            quote_end = line.rfind('"') if '"' in line else line.rfind("'")

            # Check if cursor is within the quoted filename
            if quote_start <= char_pos <= quote_end:
                # Resolve the file path
                resolved_path = self._resolve_include_path(include_path, current_uri)
                if resolved_path and os.path.exists(resolved_path):
                    # Convert to file:// URI
                    file_uri = Path(resolved_path).as_uri()

                    return Location(
                        uri=file_uri,
                        range=Range(start=Position(line=0, character=0), end=Position(line=0, character=0)),
                    )

            return None

        except Exception as e:
            logger.debug(f"Error checking include directive: {e}")
            return None

    def _resolve_include_path(self, include_path: str, current_uri: str) -> str | None:
        """Resolve include path relative to current document"""
        try:
            # Convert URI to file path
            if current_uri.startswith("file://"):
                current_path = Path(current_uri[7:])  # Remove file:// prefix
            else:
                # Handle non-file URIs or local paths
                current_path = Path(current_uri)

            current_dir = current_path.parent

            # Handle different path types
            if os.path.isabs(include_path):
                # Absolute path
                resolved_path = Path(include_path)
            else:
                # Relative path - resolve relative to current file
                resolved_path = current_dir / include_path

            # Normalize the path
            resolved_path = resolved_path.resolve()

            # Return as string path
            return str(resolved_path)

        except Exception as e:
            logger.debug(f"Error resolving include path '{include_path}' from '{current_uri}': {e}")
            return None

    def _apply_text_change(self, content: str, change: TextDocumentContentChangeEvent_Type1) -> str:
        """Apply an incremental text change to content"""

        if not hasattr(change, "range") or change.range is None:
            # Full document replacement
            return change.text

        # Split content into lines for easier manipulation
        lines = content.splitlines(keepends=True)

        # Get change coordinates
        start_line = change.range.start.line
        start_char = change.range.start.character
        end_line = change.range.end.line
        end_char = change.range.end.character

        # Handle bounds checking
        if start_line >= len(lines):
            # Change is beyond document, just append
            return content + change.text

        if end_line >= len(lines):
            end_line = len(lines) - 1
            end_char = len(lines[end_line])

        # Apply the change
        if start_line == end_line:
            # Single line change
            line = lines[start_line] if start_line < len(lines) else ""
            if not line.endswith("\n") and start_line < len(lines) - 1:
                line += "\n"

            # Replace part of the line
            before = line[:start_char] if start_char < len(line) else line
            after = line[end_char:] if end_char < len(line) else ""
            lines[start_line] = before + change.text + after
        else:
            # Multi-line change
            start_line_content = lines[start_line] if start_line < len(lines) else ""
            end_line_content = lines[end_line] if end_line < len(lines) else ""

            # Keep part of start line before change
            before = start_line_content[:start_char] if start_char < len(start_line_content) else start_line_content
            # Keep part of end line after change
            after = end_line_content[end_char:] if end_char < len(end_line_content) else ""

            # Replace the range with new content
            new_content = before + change.text + after

            # Remove the lines that were changed
            del lines[start_line : end_line + 1]

            # Insert the new content
            if new_content:
                new_lines = new_content.splitlines(keepends=True)
                # Ensure last line has newline if it should
                if new_content.endswith("\n") and new_lines and not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"
                lines[start_line:start_line] = new_lines

        return "".join(lines)

    async def _publish_diagnostics(self, uri: str, diagnostics: list[Diagnostic]) -> None:
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
