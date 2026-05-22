"""Semantic-tokens + line-based tokenizer mixin for the LSP server.

`_handle_semantic_tokens_full` is the request entry; the rest walk the
document's AST (or fall back to a regex tokenizer when the parse failed
hard) and emit LSP token records keyed by type.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, ClassVar

from lsprotocol.types import SemanticTokens, SemanticTokensParams

from a816.cpu.cpu_65c816 import snes_opcode_table
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

if TYPE_CHECKING:
    from a816.lsp.document import A816Document

logger = logging.getLogger(__name__)


class TokensMixin:
    """Semantic-tokens handler set. Mixed into `A816LanguageServer`."""

    if TYPE_CHECKING:
        documents: dict[str, A816Document]

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

    def _handle_semantic_tokens_full(self, params: SemanticTokensParams) -> SemanticTokens | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        return SemanticTokens(data=self._analyze_semantic_tokens(doc))

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
        if isinstance(node, IncludeAstNode) or isinstance(node, TokensMixin._DIRECTIVE_TYPES):
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
