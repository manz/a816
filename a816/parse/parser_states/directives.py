"""All block + simple directives (scope/macro/map/if/for/struct/pool/alloc/
relocate/reclaim/include/include_ips/extern/import/debug/label_decl/data/
ascii/text/incbin/table/.aN/.iN)."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from a816.error_codes import (
    E_PARSER_POOL_NO_RANGES,
    E_PARSER_STRUCT_DUPLICATE_FIELD,
    E_PARSER_UNEXPECTED_TOKEN,
    E_PARSER_UNKNOWN_DIRECTIVE_ATTR,
    E_PARSER_UNKNOWN_POOL_STRATEGY,
)
from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    BlockAstNode,
    CompoundAstNode,
    DataNode,
    DebugAstNode,
    ExpressionAstNode,
    ExternAstNode,
    ForAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    IncludeIpsAstNode,
    LabelDeclAstNode,
    MacroAstNode,
    MapArgs,
    MapAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RegisterSizeAstNode,
    RelocateAstNode,
    ScopeAstNode,
    StructAstNode,
    Term,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import (
    Parser,
    StateFunc,
    accept_token,
    expect_token,
)
from a816.parse.parser_states.expr import parse_expression, parse_expression_list_inner
from a816.parse.scanner import Scanner, ScannerStateFunc
from a816.parse.scanner_states import lex_initial
from a816.parse.tokens import Token, TokenType


def parse_scope(p: Parser) -> ScopeAstNode:
    from a816.parse.parser_states.core import extract_docstring, parse_block

    current = p.current()
    keyword = p.next()
    expect_token(keyword, TokenType.IDENTIFIER)

    next_token = p.next()
    expect_token(next_token, TokenType.LBRACE)
    block = parse_block(p)
    docstring, block = extract_docstring(block)
    return ScopeAstNode(keyword.value, BlockAstNode(block, next_token), current, docstring=docstring)


def parse_macro(p: Parser) -> MacroAstNode:
    from a816.parse.parser_states.core import extract_docstring, parse_block
    from a816.parse.parser_states.expr import parse_macro_definition_args

    macro_identifier = p.next()
    expect_token(macro_identifier, TokenType.IDENTIFIER)

    expect_token(p.next(), TokenType.LPAREN)

    args = parse_macro_definition_args(p)
    expect_token(p.next(), TokenType.RPAREN)
    block_token = p.current()
    expect_token(p.next(), TokenType.LBRACE)
    block = parse_block(p)
    docstring, block = extract_docstring(block)

    return MacroAstNode(
        macro_identifier.value,
        args,
        BlockAstNode(block, block_token),
        macro_identifier,
        docstring=docstring,
    )


def parse_map(p: Parser) -> MapAstNode:
    args: MapArgs = {}
    first_identifier = p.current()
    expect_token(first_identifier, TokenType.IDENTIFIER)

    while p.current().type == TokenType.IDENTIFIER:
        identifier = p.next()

        expect_token(identifier, TokenType.IDENTIFIER)
        key = identifier.value

        if key in {
            "identifier",
            "writable",
            "bank_range",
            "addr_range",
            "mask",
            "mirror_bank_range",
        }:
            map_key = cast(
                Literal[
                    "identifier",
                    "writable",
                    "bank_range",
                    "addr_range",
                    "mask",
                    "mirror_bank_range",
                ],
                key,
            )
            expect_token(p.next(), TokenType.EQUAL)
            number1 = p.next()
            expect_token(number1, TokenType.NUMBER)
            if accept_token(p.current(), TokenType.COMMA):
                p.next()
                number2 = p.next()
                expect_token(number2, TokenType.NUMBER)

                args[map_key] = (
                    ast.literal_eval(number1.value),
                    ast.literal_eval(number2.value),
                )
            else:
                args[map_key] = ast.literal_eval(number1.value)
        else:
            raise ParserSyntaxError(
                f"unknown attribute for `.map` directive: `{identifier.value}`",
                identifier,
                code=str(E_PARSER_UNKNOWN_DIRECTIVE_ATTR),
                hint="valid attributes: identifier, writable, bank_range, addr_range, mask, mirror_bank_range",
            )

    return MapAstNode(args, first_identifier)


def parse_if(p: Parser) -> IfAstNode:
    from a816.parse.parser_states.core import parse_block

    current = p.current()
    condition = parse_expression(p)
    expect_token(p.next(), TokenType.LBRACE)
    body = CompoundAstNode(parse_block(p), p.current())
    else_body = None
    if p.current().value == "else":
        p.next()
        expect_token(p.next(), TokenType.LBRACE)
        else_body = CompoundAstNode(parse_block(p), p.current())

    return IfAstNode(condition, body, else_body, current)


def parse_for(p: Parser) -> ForAstNode:
    from a816.parse.parser_states.core import parse_block

    current = p.current()
    variable = p.next()
    expect_token(variable, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.ASSIGN)
    start = parse_expression(p)
    expect_token(p.next(), TokenType.COMMA)
    end = parse_expression(p)

    expect_token(p.next(), TokenType.LBRACE)
    block = CompoundAstNode(parse_block(p), p.current())

    return ForAstNode(variable.value, start, end, block, current)


# Primitive struct field types. Field types outside this set are resolved at
# codegen time against registered struct types so nested layouts compose.
STRUCT_FIELD_TYPES = {"byte", "word", "long", "dword"}


def parse_struct(p: Parser) -> StructAstNode:
    """Parse a `.struct Name { ... }` body.

    Field shape is always `type name`. Primitive types are
    `byte/word/long/dword`; `uN` (any positive `N`) declares a
    bit-field of `N` bits packed into the surrounding byte run; any
    other identifier references a previously declared `.struct`.
    """
    current = p.current()

    variable = p.next()
    expect_token(variable, TokenType.IDENTIFIER)

    expect_token(p.next(), TokenType.LBRACE)
    fields: list[tuple[str, str]] = []
    seen: set[str] = set()
    while p.current().type != TokenType.EOF:
        if p.current().type == TokenType.COMMENT:
            p.next()
            continue
        if p.current().type == TokenType.COMMA:
            p.next()
            continue
        if p.current().type == TokenType.RBRACE:
            break

        type_token = p.current()
        expect_token(type_token, TokenType.IDENTIFIER)
        p.next()

        name_token = p.current()
        expect_token(name_token, TokenType.IDENTIFIER)
        if name_token.value in seen:
            raise ParserSyntaxError(
                f"Duplicate struct field `{name_token.value}`",
                name_token,
                TokenType.IDENTIFIER,
                code=str(E_PARSER_STRUCT_DUPLICATE_FIELD),
                hint="each field name must be unique within a `.struct` block",
            )
        seen.add(name_token.value)
        p.next()
        fields.append((name_token.value, type_token.value))

    expect_token(p.next(), TokenType.RBRACE)

    return StructAstNode(variable.value, fields, current)


def parse_directive_with_quoted_string(p: Parser) -> str:
    string = p.next()
    expect_token(string, TokenType.QUOTED_STRING)

    return string.value[1:-1]


def parse_include_ips(p: Parser) -> IncludeIpsAstNode:
    current = p.current()
    string = parse_directive_with_quoted_string(p)

    expect_token(p.next(), TokenType.COMMA)
    expression = parse_expression(p)

    return IncludeIpsAstNode(string, expression, current)


def parse_label_decl(p: Parser, keyword: Token) -> LabelDeclAstNode:
    """Parse `.label NAME = EXPR` directive."""
    symbol_token = p.next()
    expect_token(symbol_token, TokenType.IDENTIFIER)
    operator = p.next()
    expect_token(operator, TokenType.EQUAL)
    expression = parse_expression(p)
    return LabelDeclAstNode(symbol_token.value, expression, keyword)


def parse_extern(p: Parser) -> ExternAstNode:
    """Parse extern symbol_name"""
    symbol_token = p.current()
    expect_token(symbol_token, TokenType.IDENTIFIER)
    p.next()  # consume the identifier
    return ExternAstNode(symbol_token.value, symbol_token)


def parse_import(p: Parser) -> ImportAstNode:
    """Parse .import "module_name" directive.

    The .import directive imports all public symbols from a module.
    Module resolution happens at code generation time.
    """
    current = p.current()
    module_name = parse_directive_with_quoted_string(p)
    return ImportAstNode(module_name, current)


def parse_debug(p: Parser) -> DebugAstNode:
    message_token = p.current()
    expect_token(message_token, TokenType.QUOTED_STRING)
    p.next()  # consume the identifier
    return DebugAstNode(message_token.value[1:-1], message_token)


def _resolve_include_path(p: Parser, keyword: Token, include_path: str) -> str:
    """Locate an include file: parent-relative first, then `--include-path`s."""
    if keyword.position and keyword.position.file:
        parent_filename = keyword.position.file.filename
        if parent_filename.startswith("file://"):
            from urllib.parse import unquote, urlparse

            parent_filename = unquote(urlparse(parent_filename).path)
        parent_dir = Path(parent_filename).parent
        if parent_dir.exists():
            candidate = parent_dir / include_path
            if candidate.exists():
                return str(candidate)

    for search_dir in p.include_paths or []:
        candidate = search_dir / include_path
        if candidate.exists():
            return str(candidate)

    return include_path  # let the eventual open() raise the canonical error


def parse_include(p: Parser, keyword: Token) -> IncludeAstNode:
    from a816.parse.parser_states.core import parse_initial

    include_path = parse_directive_with_quoted_string(p)
    resolved_path = _resolve_include_path(p, keyword, include_path)
    with open(resolved_path, encoding="utf-8") as fd:
        source = fd.read()
    scanner = Scanner(cast(ScannerStateFunc, lex_initial))
    tokens = scanner.scan(resolved_path, source)
    parser = Parser(tokens, cast(StateFunc, parse_initial), include_paths=p.include_paths)
    sub_ast = parser.parse()
    return IncludeAstNode(include_path, sub_ast, keyword)


def _data_node(kind: str) -> Callable[[Parser, Token], DataNode]:
    def _handle(p: Parser, keyword: Token) -> DataNode:
        return DataNode(kind, parse_expression_list_inner(p), keyword)

    return _handle


def _quoted_directive(node_cls: type) -> Callable[[Parser, Token], AstNode]:
    def _handle(p: Parser, keyword: Token) -> AstNode:
        return cast(AstNode, node_cls(parse_directive_with_quoted_string(p), keyword))

    return _handle


def _register_size(register: str, size: int) -> Callable[[Parser, Token], RegisterSizeAstNode]:
    def _handle(_p: Parser, keyword: Token) -> RegisterSizeAstNode:
        return RegisterSizeAstNode(register, size, keyword)

    return _handle


_POOL_STRATEGIES = {"pack", "order"}


def _zero_expression(file_info: Token) -> ExpressionAstNode:
    """Synthesise the literal expression `0` for default pool fill byte."""
    zero_tok = Token(TokenType.NUMBER, "0", file_info.position)
    return ExpressionAstNode([Term(zero_tok)])


def _parse_pool_strategy(p: Parser) -> str:
    strat_token = p.next()
    expect_token(strat_token, TokenType.IDENTIFIER)
    if strat_token.value not in _POOL_STRATEGIES:
        raise ParserSyntaxError(
            f"unknown pool strategy `{strat_token.value}`",
            strat_token,
            code=str(E_PARSER_UNKNOWN_POOL_STRATEGY),
            hint=f"expected one of: {', '.join(sorted(_POOL_STRATEGIES))}",
        )
    return strat_token.value


@dataclass
class _PoolAttrs:
    ranges: list[tuple[ExpressionAstNode, ExpressionAstNode]]
    fill: ExpressionAstNode
    strategy: str


def _parse_pool_attr(p: Parser, key_token: Token, attrs: _PoolAttrs) -> None:
    key = key_token.value
    if key == "range":
        lo = parse_expression(p)
        hi = parse_expression(p)
        attrs.ranges.append((lo, hi))
    elif key == "fill":
        attrs.fill = parse_expression(p)
    elif key == "strategy":
        attrs.strategy = _parse_pool_strategy(p)
    else:
        raise ParserSyntaxError(
            f"unknown `.pool` attribute `{key}`",
            key_token,
            code=str(E_PARSER_UNKNOWN_DIRECTIVE_ATTR),
            hint="expected one of: range, fill, strategy",
        )


def parse_pool(p: Parser) -> PoolAstNode:
    """Parse `.pool NAME { range LO HI | fill VAL | strategy ID ... }`."""
    keyword = p.current()
    name_token = p.next()
    expect_token(name_token, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.LBRACE)

    attrs = _PoolAttrs(ranges=[], fill=_zero_expression(keyword), strategy="pack")

    while p.current().type != TokenType.EOF:
        current = p.current()
        if current.type == TokenType.RBRACE:
            break
        if current.type == TokenType.COMMENT:
            p.next()
            continue
        expect_token(current, TokenType.IDENTIFIER)
        key_token = p.next()
        _parse_pool_attr(p, key_token, attrs)

    expect_token(p.next(), TokenType.RBRACE)
    if not attrs.ranges:
        raise ParserSyntaxError(
            f"pool `{name_token.value}` declares no ranges",
            keyword,
            code=str(E_PARSER_POOL_NO_RANGES),
            hint="add at least one `range LO HI` line so the allocator has space to work with",
        )
    return PoolAstNode(
        name_token.value,
        attrs.ranges,
        attrs.fill,
        attrs.strategy,
        keyword,
    )


def _expect_contextual_keyword(p: Parser, expected: str) -> Token:
    token = p.next()
    expect_token(token, TokenType.IDENTIFIER)
    if token.value != expected:
        raise ParserSyntaxError(
            f"expected `{expected}`, found `{token.value}`",
            token,
            code=str(E_PARSER_UNEXPECTED_TOKEN),
        )
    return token


def parse_alloc(p: Parser) -> AllocAstNode:
    """Parse `.alloc` in four shapes:

    * `.alloc NAME in POOL { body }` — pooled, named.
    * `.alloc in POOL { body }` — pooled, anonymous (asset bins
      dumped into a pool without a per-blob name).
    * `.alloc NAME at ADDR [size N] { body }` — pinned, named.
    * `.alloc at ADDR [size N] { body }` — pinned, anonymous (3-byte
      hijacks shouldn't tax with names).
    """
    from a816.parse.parser_states.core import parse_block

    keyword = p.current()
    first = p.next()
    expect_token(first, TokenType.IDENTIFIER)

    # `.alloc at ADDR ...` — anonymous pinned.
    if first.value == "at":
        return _parse_pinned_alloc_tail(p, parse_block, keyword, name=None)

    # `.alloc in POOL ...` — anonymous pooled.
    if first.value == "in":
        pool_token = p.next()
        expect_token(pool_token, TokenType.IDENTIFIER)
        body = _parse_alloc_body(p, parse_block)
        return AllocAstNode(None, pool_token.value, body, keyword)

    # `.alloc NAME ...` — pooled or named-pinned.
    name = first.value
    separator = _expect_contextual_keyword_one_of(p, ("in", "at"))
    if separator.value == "in":
        pool_token = p.next()
        expect_token(pool_token, TokenType.IDENTIFIER)
        body = _parse_alloc_body(p, parse_block)
        return AllocAstNode(name, pool_token.value, body, keyword)

    return _parse_pinned_alloc_tail(p, parse_block, keyword, name=name)


ParseBlockFn = Callable[[Parser], list[AstNode]]


def _parse_pinned_alloc_tail(p: Parser, parse_block: ParseBlockFn, keyword: Token, *, name: str | None) -> AllocAstNode:
    """Common tail for the two pinned alloc shapes: ADDR [size N] { body }."""
    at_address = parse_expression(p)
    at_size = _parse_optional_size_clause(p)
    body = _parse_alloc_body(p, parse_block)
    return AllocAstNode(name, None, body, keyword, at_address=at_address, at_size=at_size)


def _parse_alloc_body(p: Parser, parse_block: ParseBlockFn) -> BlockAstNode:
    lbrace = p.next()
    expect_token(lbrace, TokenType.LBRACE)
    return BlockAstNode(parse_block(p), lbrace)


def _parse_optional_size_clause(p: Parser) -> ExpressionAstNode | None:
    """`size N` after `at ADDR`. Optional; returns None when absent."""
    if p.current().type == TokenType.IDENTIFIER and p.current().value == "size":
        p.next()  # consume 'size'
        return parse_expression(p)
    return None


def _expect_contextual_keyword_one_of(p: Parser, options: tuple[str, ...]) -> Token:
    """Like `_expect_contextual_keyword` but matches any of `options`."""
    token = p.next()
    expect_token(token, TokenType.IDENTIFIER)
    if token.value not in options:
        joined = " | ".join(f"`{o}`" for o in options)
        raise ParserSyntaxError(
            f"expected one of {joined}, found `{token.value}`",
            token,
            code=str(E_PARSER_UNEXPECTED_TOKEN),
        )
    return token


def parse_relocate(p: Parser) -> RelocateAstNode:
    """Parse `.relocate SYMBOL OLD_START OLD_END into POOL { body }`."""
    from a816.parse.parser_states.core import parse_block

    keyword = p.current()
    symbol_token = p.next()
    expect_token(symbol_token, TokenType.IDENTIFIER)
    old_start = parse_expression(p)
    old_end = parse_expression(p)
    _expect_contextual_keyword(p, "into")
    pool_token = p.next()
    expect_token(pool_token, TokenType.IDENTIFIER)
    lbrace = p.next()
    expect_token(lbrace, TokenType.LBRACE)
    body = BlockAstNode(parse_block(p), lbrace)
    return RelocateAstNode(
        symbol_token.value,
        old_start,
        old_end,
        pool_token.value,
        body,
        keyword,
    )


def parse_reclaim(p: Parser) -> ReclaimAstNode:
    """Parse `.reclaim POOL START END`."""
    keyword = p.current()
    pool_token = p.next()
    expect_token(pool_token, TokenType.IDENTIFIER)
    start = parse_expression(p)
    end = parse_expression(p)
    return ReclaimAstNode(pool_token.value, start, end, keyword)
