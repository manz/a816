"""Top-level entry, block + decl loop, keyword dispatch, error recovery."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import cast

from a816.error_codes import E_PARSER_UNEXPECTED_TOKEN
from a816.parse.ast.nodes import (
    AsciiAstNode,
    AstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    CompoundAstNode,
    DocstringAstNode,
    IncludeBinaryAstNode,
    KeywordAstNode,
    LabelAstNode,
    TableAstNode,
    TextAstNode,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import (
    Parser,
    _got_label,
    accept_token,
    accept_tokens,
    expect_token,
)
from a816.parse.parser_states.directives import (
    _data_node,
    _quoted_directive,
    _register_size,
    parse_alloc,
    parse_debug,
    parse_directive_with_quoted_string,
    parse_extern,
    parse_for,
    parse_if,
    parse_import,
    parse_include,
    parse_include_ips,
    parse_label_decl,
    parse_macro,
    parse_map,
    parse_pool,
    parse_reclaim,
    parse_relocate,
    parse_scope,
    parse_struct,
)
from a816.parse.parser_states.expr import (
    parse_expression,
    parse_macro_application,
    parse_symbol_affectation,
)
from a816.parse.parser_states.opcode import parse_code_lookup, parse_opcode
from a816.parse.tokens import Token, TokenType

_DIRECTIVE_HANDLERS: dict[str, Callable[[Parser, Token], AstNode]] = {
    "scope": lambda p, _kw: parse_scope(p),
    "ascii": _quoted_directive(AsciiAstNode),
    "text": _quoted_directive(TextAstNode),
    "dw": _data_node("dw"),
    "dl": _data_node("dl"),
    "db": _data_node("db"),
    "pointer": _data_node("pointer"),
    "include": parse_include,
    "include_ips": lambda p, _kw: parse_include_ips(p),
    "incbin": lambda p, _kw: IncludeBinaryAstNode(parse_directive_with_quoted_string(p), p.current()),
    "table": lambda p, _kw: TableAstNode(parse_directive_with_quoted_string(p), p.current()),
    "macro": lambda p, _kw: parse_macro(p),
    "map": lambda p, _kw: parse_map(p),
    "if": lambda p, _kw: parse_if(p),
    "for": lambda p, _kw: parse_for(p),
    "struct": lambda p, _kw: parse_struct(p),
    "extern": lambda p, _kw: parse_extern(p),
    "import": lambda p, _kw: parse_import(p),
    "debug": lambda p, _kw: parse_debug(p),
    "label": parse_label_decl,
    "pool": lambda p, _kw: parse_pool(p),
    "alloc": lambda p, _kw: parse_alloc(p),
    "relocate": lambda p, _kw: parse_relocate(p),
    "reclaim": lambda p, _kw: parse_reclaim(p),
    "a8": _register_size("a", 8),
    "a16": _register_size("a", 16),
    "i8": _register_size("i", 8),
    "i16": _register_size("i", 16),
}


def parse_directive(p: Parser) -> KeywordAstNode:
    """Dispatch a `.NAME` directive to its handler.

    The scanner has already emitted the KEYWORD token; we look up the
    handler in `_DIRECTIVE_HANDLERS` and let it consume the rest of
    the directive body.
    """
    keyword = p.next()
    handler = _DIRECTIVE_HANDLERS.get(keyword.value)
    if handler is None:
        raise ParserSyntaxError(
            f"unknown directive `.{keyword.value}`",
            keyword,
            code=str(E_PARSER_UNEXPECTED_TOKEN),
            hint="see https://a816.ringum.net/directives/ for the list of supported `.` directives",
        )
    return cast(KeywordAstNode, handler(p, keyword))


# Back-compat alias for callers that imported the old name. Drop in
# a follow-up once the rest of the codebase migrates.
parse_keyword = parse_directive


def parse_label(p: Parser) -> LabelAstNode:
    p.backup()
    current_token = p.next()

    return LabelAstNode(current_token.value, current_token)


def parse_block(p: Parser) -> list[AstNode]:
    decl = []
    while p.current().type != TokenType.EOF:
        if p.current().type == TokenType.RBRACE:
            break
        statement = parse_decl(p)
        if statement is not None:
            decl.append(statement)

    expect_token(p.next(), TokenType.RBRACE)
    return decl


def extract_docstring(statements: list[AstNode]) -> tuple[str | None, list[AstNode]]:
    if statements and isinstance(statements[0], DocstringAstNode):
        # isinstance already narrows the type, no cast needed
        return statements[0].text, statements[1:]
    return None, statements


def parse_code_position_keyword(p: Parser) -> CodePositionAstNode:
    current = p.current()
    code_position = parse_expression(p)
    return CodePositionAstNode(code_position, current)


def parse_code_relocation_keyword(p: Parser) -> CodeRelocationAstNode:
    current = p.current()
    code_position = parse_expression(p)
    return CodeRelocationAstNode(code_position, current)


def parse_decl(
    p: Parser,
) -> AstNode | None:
    current_token = p.next()
    if accept_token(current_token, TokenType.COMMENT):
        return CommentAstNode(current_token.value, current_token)
    elif accept_token(current_token, TokenType.DOCSTRING):
        raw_text = ast.literal_eval(current_token.value)
        return DocstringAstNode(raw_text, current_token)
    elif accept_token(current_token, TokenType.DOUBLE_LBRACE):
        return parse_code_lookup(p)
    elif accept_tokens(current_token, [TokenType.OPCODE, TokenType.OPCODE_NAKED]):
        p.backup()
        return parse_opcode(p)
    elif accept_token(current_token, TokenType.KEYWORD):
        p.backup()
        return parse_keyword(p)
    elif accept_token(current_token, TokenType.IDENTIFIER):
        p.backup()
        if accept_token(p.peek(), TokenType.LPAREN):
            return parse_macro_application(p)
        else:
            # might be another thing but we check for equal inside parse_symbol_affectation
            return parse_symbol_affectation(p)
    elif accept_token(current_token, TokenType.LABEL):
        return parse_label(p)
    elif accept_token(current_token, TokenType.LBRACE):
        return CompoundAstNode(parse_block(p), current_token)
    elif accept_token(current_token, TokenType.STAR_EQ):
        return parse_code_position_keyword(p)
    elif accept_token(current_token, TokenType.AT_EQ):
        return parse_code_relocation_keyword(p)
    else:
        raise ParserSyntaxError(
            f"unexpected {_got_label(current_token)} at top level",
            current_token,
            None,
            code=str(E_PARSER_UNEXPECTED_TOKEN),
            hint="declarations begin with an opcode, label, `.directive`, identifier, or `{...}` block",
        )


_MAX_PARSE_ERRORS = 20

# Tokens that mark a fresh top-level statement; the recovery walker fast-
# forwards to one of these after a syntax error so a single typo doesn't
# cascade into dozens of follow-up parse failures.
_TOP_LEVEL_SYNC_TOKENS = (
    TokenType.LABEL,
    TokenType.OPCODE,
    TokenType.OPCODE_NAKED,
    TokenType.KEYWORD,
    TokenType.STAR_EQ,
    TokenType.AT_EQ,
    TokenType.DOUBLE_LBRACE,
)


def _recover_to_next_statement(p: Parser) -> None:
    """Skip ahead to the next plausible statement start after a parse error."""
    depth = 0
    while p.current().type != TokenType.EOF:
        current = p.current()
        if current.type == TokenType.LBRACE:
            depth += 1
            p.next()
            continue
        if current.type == TokenType.RBRACE:
            if depth == 0:
                return  # let the enclosing block consume it
            depth -= 1
            p.next()
            continue
        if depth == 0 and current.type in _TOP_LEVEL_SYNC_TOKENS:
            return
        p.next()


def parse_initial(p: Parser) -> list[AstNode]:
    statements: list[AstNode] = []
    while p.current().type != TokenType.EOF:
        try:
            statement = parse_decl(p)
        except ParserSyntaxError as exc:
            p.errors.append(exc)
            if len(p.errors) >= _MAX_PARSE_ERRORS:
                raise
            _recover_to_next_statement(p)
            continue
        if statement:
            statements.append(statement)

    if p.errors:
        # Re-raise the first error so existing single-error callers still
        # see something. mzparser unpacks the full list off `p.errors` for
        # multi-diagnostic rendering.
        raise p.errors[0]
    return statements
