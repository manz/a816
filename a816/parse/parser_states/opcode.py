"""Opcode + addressing-mode operand parsing + code-lookup `{{symbol}}`."""

from __future__ import annotations

from typing import TypeGuard

from a816.cpu.cpu_65c816 import AddressingMode, ValueSize
from a816.error_codes import E_PARSER_INVALID_EXPRESSION
from a816.parse.ast.nodes import CodeLookupAstNode, ExpressionAstNode, OpcodeAstNode, index_map
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import Parser, accept_token, expect_token
from a816.parse.parser_states.expr import parse_expression
from a816.parse.tokens import Token, TokenType


def is_value_size(value_size: str) -> TypeGuard[ValueSize]:
    return value_size in ["b", "w", "l"]


def parse_opcode(p: Parser) -> OpcodeAstNode:
    opcode: Token = p.next()
    size: str | None = None
    index: str | None = None

    if accept_token(opcode, TokenType.OPCODE_NAKED):
        addressing_mode = AddressingMode.none
    else:
        addressing_mode = AddressingMode.direct

    if accept_token(p.current(), TokenType.OPCODE_SIZE):
        size = p.current().value.lower()
        p.next()

    addressing_mode, inner_index, operand = parse_operand_and_addressing(addressing_mode, opcode, p)

    if accept_token(p.current(), TokenType.ADDRESSING_MODE_INDEX):
        index = p.next().value.lower()
        addressing_mode = index_map[addressing_mode]

    return OpcodeAstNode(
        addressing_mode=addressing_mode,
        opcode=opcode.value,
        value_size=size if size is not None and is_value_size(size) else None,
        operand=operand,
        index=index or inner_index,
        file_info=opcode,
    )


def _parse_immediate_operand(p: Parser) -> tuple[AddressingMode, None, ExpressionAstNode]:
    p.next()
    if accept_token(p.current(), TokenType.EOF):
        raise ParserSyntaxError(
            "unexpected end of input after `#` immediate prefix",
            p.current(),
            None,
            code=str(E_PARSER_INVALID_EXPRESSION),
            hint="`#` introduces an immediate operand — supply a value, e.g. `lda #0x42`",
        )
    return AddressingMode.immediate, None, parse_expression(p)


def _parse_indirect_inner(
    p: Parser,
) -> tuple[AddressingMode, str | None, ExpressionAstNode]:
    """Parse `(expr [,X|Y|S])`. Caller-side try/except converts SyntaxError
    raised here into a fallback to direct addressing.
    """
    p.next()
    operand = parse_expression(p)
    # Cast inside operand parens (`(addr as T).field`) is not an indirect
    # addressing mode; bail to the direct path.
    if p.current().type == TokenType.IDENTIFIER and p.current().value == "as":
        raise SyntaxError()
    inner_index: str | None = None
    addressing_mode = AddressingMode.indirect
    if accept_token(p.current(), TokenType.ADDRESSING_MODE_INDEX):
        addressing_mode = AddressingMode.dp_or_sr_indirect_indexed
        inner_index = p.current().value
        p.next()
    expect_token(p.current(), TokenType.RPAREN)
    from a816.parse.parser import accept_tokens

    if accept_tokens(p.peek(), [TokenType.OPERATOR, TokenType.DOT]):
        raise SyntaxError()
    p.next()
    return addressing_mode, inner_index, operand


def _parse_paren_operand(
    p: Parser,
) -> tuple[AddressingMode, str | None, ExpressionAstNode]:
    saved_position = p.pos
    try:
        return _parse_indirect_inner(p)
    except SyntaxError:
        p.pos = saved_position
        return AddressingMode.direct, None, parse_expression(p)


def _parse_indirect_long_operand(p: Parser) -> tuple[AddressingMode, None, ExpressionAstNode]:
    p.next()
    operand = parse_expression(p)
    expect_token(p.next(), TokenType.RBRAKET)
    return AddressingMode.indirect_long, None, operand


def parse_operand_and_addressing(
    addressing_mode: AddressingMode, opcode: Token, p: Parser
) -> tuple[AddressingMode, str | None, ExpressionAstNode | None]:
    if accept_token(p.current(), TokenType.SHARP):
        return _parse_immediate_operand(p)
    if accept_token(p.current(), TokenType.LPAREN):
        return _parse_paren_operand(p)
    if accept_token(p.current(), TokenType.LBRAKET):
        return _parse_indirect_long_operand(p)
    if accept_token(opcode, TokenType.OPCODE):
        return addressing_mode, None, parse_expression(p)
    return addressing_mode, None, None


def parse_code_lookup(p: Parser) -> CodeLookupAstNode:
    current = p.current()
    identifier = p.next()
    expect_token(identifier, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.DOUBLE_RBRACE)

    return CodeLookupAstNode(identifier.value, current)
