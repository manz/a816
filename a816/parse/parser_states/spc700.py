"""SPC700 operand grammar: `dst, src` operands, registers, `!abs`, `[dp+x]`, `mem.bit`."""

from __future__ import annotations

from a816.cpu.spc700 import REGISTERS
from a816.error_codes import E_PARSER_MISSING_OPERAND
from a816.parse.ast.nodes.base import ExpressionAstNode
from a816.parse.ast.nodes.spc700 import SpcOpcodeAstNode, SpcOperand
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import Parser, accept_token, expect_token
from a816.parse.parser_states.expr import parse_expression
from a816.parse.tokens import Token, TokenType


def parse_spc_opcode(p: Parser) -> SpcOpcodeAstNode:
    opcode = p.next()
    operands: list[SpcOperand] = []
    if accept_token(opcode, TokenType.OPCODE):
        operands.append(_parse_operand(opcode, p))
        while accept_token(p.current(), TokenType.COMMA):
            p.next()
            operands.append(_parse_operand(opcode, p))
    return SpcOpcodeAstNode(opcode=opcode.value.lower(), operands=tuple(operands), file_info=opcode)


def _index(p: Parser) -> str | None:
    """`+x` / `+y` after an operand, scanned as one index token."""
    if not accept_token(p.current(), TokenType.ADDRESSING_MODE_INDEX):
        return None
    return p.next().value[-1].lower()


def _register(token: Token) -> str | None:
    if token.type is TokenType.IDENTIFIER and token.value.lower() in REGISTERS:
        return token.value.lower()
    return None


def _parse_operand(opcode: Token, p: Parser) -> SpcOperand:
    current = p.current()
    if current.type in (TokenType.RBRACE, TokenType.EOF, TokenType.COMMA):
        raise ParserSyntaxError(
            f"`{opcode.value}` needs an operand",
            opcode,
            None,
            code=str(E_PARSER_MISSING_OPERAND),
        )
    if accept_token(current, TokenType.SHARP):
        p.next()
        return SpcOperand("imm", parse_expression(p))
    if accept_token(current, TokenType.BANG):
        p.next()
        expression = parse_expression(p)
        index = _index(p)
        return SpcOperand(f"abs+{index}" if index else "abs", expression)
    if accept_token(current, TokenType.LBRAKET):
        return _parse_bracket(p)
    register_indirect = _register_indirect(p)
    if register_indirect is not None:
        return register_indirect
    register = _register(current)
    if register is not None and p.peek().type is not TokenType.DOT:
        p.next()
        return SpcOperand(register)
    if accept_token(current, TokenType.OPERATOR) and current.value == "/":
        p.next()
        expression = parse_expression(p)
        return SpcOperand("/mem.bit", expression, _parse_bit(p))
    return _parse_memory(p)


def _parse_memory(p: Parser) -> SpcOperand:
    expression = parse_expression(p)
    if accept_token(p.current(), TokenType.DOT):
        return SpcOperand("mem.bit", expression, _parse_bit(p))
    index = _index(p)
    return SpcOperand(f"mem+{index}" if index else "mem", expression)


def _parse_bit(p: Parser) -> ExpressionAstNode:
    expect_token(p.next(), TokenType.DOT)
    return parse_expression(p)


def _register_indirect(p: Parser) -> SpcOperand | None:
    """`(x)`, `(y)` or `(x)+`; a parenthesised expression is left to `_parse_memory`."""
    if p.current().type is not TokenType.LPAREN:
        return None
    register = _register(p.peek())
    if register not in ("x", "y") or p.tokens[p.pos + 2].type is not TokenType.RPAREN:
        return None
    p.next()
    p.next()
    p.next()
    current = p.current()
    if register == "x" and current.type is TokenType.OPERATOR and current.value == "+":
        p.next()
        return SpcOperand("(x)+")
    return SpcOperand(f"({register})")


def _parse_bracket(p: Parser) -> SpcOperand:
    """`[dp+x]`, `[dp]+y` or `[!abs+x]`."""
    p.next()
    absolute = accept_token(p.current(), TokenType.BANG)
    if absolute:
        p.next()
    expression = parse_expression(p)
    inner = _index(p)
    expect_token(p.next(), TokenType.RBRAKET)
    if absolute and inner == "x":
        return SpcOperand("[abs+x]", expression)
    if inner == "x":
        return SpcOperand("[dp+x]", expression)
    if inner is None and _index(p) == "y":
        return SpcOperand("[dp]+y", expression)
    raise ParserSyntaxError(
        "SPC700 indirect operands are `[dp+x]`, `[dp]+y` or `[!abs+x]`",
        p.current(),
        None,
        code=str(E_PARSER_MISSING_OPERAND),
    )
