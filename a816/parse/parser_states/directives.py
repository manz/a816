"""All block + simple directives (scope/macro/map/if/for/struct/pool/alloc/
relocate/reclaim/include/include_ips/extern/import/debug/label_decl/data/
ascii/text/incbin/table/.aN/.iN)."""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from a816.build_inputs import record_miss, recording_misses, replay_misses
from a816.error_codes import (
    E_PARSER_EXPECTED_TOKEN,
    E_PARSER_ISTRUCT_DUPLICATE_FIELD,
    E_PARSER_ISTRUCT_STRING_IN_LIST,
    E_PARSER_POOL_NO_RANGES,
    E_PARSER_STRUCT_ARRAY_COUNT,
    E_PARSER_STRUCT_BITFIELD_ARRAY,
    E_PARSER_STRUCT_DUPLICATE_FIELD,
    E_PARSER_UNEXPECTED_TOKEN,
    E_PARSER_UNKNOWN_DIRECTIVE_ATTR,
    E_PARSER_UNKNOWN_POOL_STRATEGY,
)
from a816.parse.ast.expression import eval_number
from a816.parse.ast.nodes import (
    AllocAstNode,
    AssertAstNode,
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
    InitCommentAstNode,
    InitValue,
    LabelDeclAstNode,
    ListInitAstNode,
    MacroAstNode,
    MapArgs,
    MapAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RegisterSizeAstNode,
    RelocateAstNode,
    ReserveAstNode,
    ReserveTypedAstNode,
    ScopeAstNode,
    StringInitAstNode,
    StructAstNode,
    StructFieldInitAstNode,
    StructInitAstNode,
    StructInstanceAstNode,
    Term,
)
from a816.parse.ast.nodes.struct import StructBodyItem
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


_MAP_KEYS = frozenset({"identifier", "writable", "bank_range", "addr_range", "mask", "mirror_bank_range"})
_MapKey = Literal["identifier", "writable", "bank_range", "addr_range", "mask", "mirror_bank_range"]


def _parse_map_value(p: Parser) -> int | tuple[int, int]:
    expect_token(p.next(), TokenType.EQUAL)
    number1 = p.next()
    expect_token(number1, TokenType.NUMBER)
    if not accept_token(p.current(), TokenType.COMMA):
        return cast(int, ast.literal_eval(number1.value))
    p.next()
    number2 = p.next()
    expect_token(number2, TokenType.NUMBER)
    return ast.literal_eval(number1.value), ast.literal_eval(number2.value)


def _token_line(token: Token) -> int | None:
    return token.position.line if token.position is not None else None


def _is_map_attribute(token: Token, keyword: Token) -> bool:
    """Attributes live on the `.map` line; newlines emit no token, so the line number is the terminator."""
    return token.type == TokenType.IDENTIFIER and _token_line(token) == _token_line(keyword)


def parse_map(p: Parser, keyword: Token) -> MapAstNode:
    args: MapArgs = {}
    first_identifier = p.current()
    expect_token(first_identifier, TokenType.IDENTIFIER)

    while _is_map_attribute(p.current(), keyword):
        identifier = p.next()
        if identifier.value not in _MAP_KEYS:
            raise ParserSyntaxError(
                f"unknown attribute for `.map` directive: `{identifier.value}`",
                identifier,
                code=str(E_PARSER_UNKNOWN_DIRECTIVE_ATTR),
                hint="valid attributes: identifier, writable, bank_range, addr_range, mask, mirror_bank_range",
            )
        args[cast(_MapKey, identifier.value)] = _parse_map_value(p)

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

_BIT_FIELD_TYPE_RE = re.compile(r"u\d+", re.ASCII)


def parse_struct(p: Parser) -> StructAstNode:
    """Parse a `.struct Name { ... }` body.

    Field shape is `type name` or `type[N] name`. Primitive types are
    `byte/word/long/dword`; `uN` (any positive `N`) declares a
    bit-field of `N` bits packed into the surrounding byte run; any
    other identifier references a previously declared `.struct`. The
    `[N]` suffix declares an array of `N` consecutive elements and
    travels in the type string (`byte[21]`), like the bit width of `uN`.
    """
    current = p.current()

    variable = p.next()
    expect_token(variable, TokenType.IDENTIFIER)

    open_brace = p.next()
    expect_token(open_brace, TokenType.LBRACE)
    fields: list[tuple[str, str]] = []
    body = _StructBody(_line_of(open_brace))
    seen: set[str] = set()
    while p.current().type != TokenType.EOF:
        token = p.current()
        if token.type == TokenType.COMMA:
            p.next()
            continue
        if token.type == TokenType.COMMENT:
            body.comment(token)
            p.next()
            continue
        if token.type == TokenType.RBRACE:
            break
        name, field_type = _parse_struct_field(p, seen)
        fields.append((name, field_type))
        body.field(f"{field_type} {name}", _line_of(token))

    expect_token(p.next(), TokenType.RBRACE)

    return StructAstNode(variable.value, fields, current, body.items)


class _StructBody:
    """Collects a struct body in source order for the formatter: a comment on
    a field's line trails that field, any other comment stands alone, and a
    gap of blank lines between items is kept as one."""

    def __init__(self, open_line: int) -> None:
        self.items: list[StructBodyItem] = []
        self._last_line = open_line
        self._last_field_line: int | None = None
        self._trailing_column: int | None = None

    def _continues_trailing(self, token: Token, line: int) -> bool:
        """A comment on the next line, at the column of the trailing comment
        just above it, continues that comment."""
        return (
            self._trailing_column is not None
            and line == self._last_line + 1
            and _column_of(token) == self._trailing_column
        )

    def _gap(self, line: int) -> None:
        if self.items and line > self._last_line + 1:
            self.items.append(("blank", "", None))
        self._last_line = line

    def field(self, text: str, line: int) -> None:
        self._trailing_column = None
        self._gap(line)
        self.items.append(("field", text, None))
        self._last_field_line = line

    def comment(self, token: Token) -> None:
        line = _line_of(token)
        if line == self._last_field_line and self.items[-1][0] == "field":
            kind, text, _ = self.items[-1]
            self.items[-1] = (kind, text, token.value)
            self._trailing_column = _column_of(token)
            self._last_line = line + token.value.count("\n")
            return
        if self._continues_trailing(token, line):
            self.items.append(("continuation", token.value, None))
            self._last_line = line
            return
        self._trailing_column = None
        self._gap(line)
        self.items.append(("comment", token.value, None))
        self._last_line = line + token.value.count("\n")


def _line_of(token: Token) -> int:
    position = token.position
    return position.line if position is not None else 0


def _column_of(token: Token) -> int:
    position = token.position
    return position.column if position is not None else 0


def _parse_struct_field(p: Parser, seen: set[str]) -> tuple[str, str]:
    """Parse one `type name` / `type[N] name` field; return `(name, type)`."""
    type_token = p.current()
    expect_token(type_token, TokenType.IDENTIFIER)
    p.next()
    field_type = type_token.value
    if p.current().type == TokenType.LBRAKET:
        field_type += _parse_struct_array_suffix(p, type_token)

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
    return name_token.value, field_type


def _parse_struct_array_suffix(p: Parser, type_token: Token) -> str:
    """Consume `[N]` after a field type; return it for the type string.

    `N` is a number literal or a constant expression (`[LINE_CELLS * 16]`);
    codegen evaluates an expression and checks its value.
    """
    if _BIT_FIELD_TYPE_RE.fullmatch(type_token.value):
        raise ParserSyntaxError(
            f"bit-field `{type_token.value}` cannot be an array",
            type_token,
            code=str(E_PARSER_STRUCT_BITFIELD_ARRAY),
            hint="declare one `uN` field per bit run, or use a `byte[N]` array",
        )
    p.next()
    if p.current().type != TokenType.NUMBER or p.peek().type != TokenType.RBRAKET:
        count = parse_expression(p)
        expect_token(p.next(), TokenType.RBRAKET)
        return f"[{count.to_canonical()}]"
    count_token = p.next()
    if eval_number(count_token.value) < 1:
        raise ParserSyntaxError(
            f"struct array count must be a positive integer, found `{count_token.value}`",
            count_token,
            code=str(E_PARSER_STRUCT_ARRAY_COUNT),
            hint="write the element count as a literal, e.g. `byte[21] title`",
        )
    expect_token(p.next(), TokenType.RBRAKET)
    return f"[{count_token.value}]"


def parse_istruct(p: Parser, keyword: Token) -> StructInstanceAstNode:
    """Parse `.istruct TYPE { field = value, ... }`.

    Fields are separated by commas and/or newlines. A value is a quoted
    string, a `[ ... ]` list, a nested `{ ... }` or an expression; the
    codegen checks each against the field's declared type.
    """
    type_token = p.next()
    expect_token(type_token, TokenType.IDENTIFIER)
    open_token = p.next()
    expect_token(open_token, TokenType.LBRACE)
    init = _parse_struct_init(p, open_token)
    return StructInstanceAstNode(type_token.value, init, type_token, keyword)


def _parse_struct_init(p: Parser, open_token: Token) -> StructInitAstNode:
    """Parse `name = value` entries up to the closing `}` (already past `{`)."""
    items: list[StructFieldInitAstNode | InitCommentAstNode] = []
    seen: set[str] = set()
    while p.current().type != TokenType.RBRACE:
        token = p.current()
        if token.type == TokenType.COMMA:
            p.next()
        elif token.type == TokenType.COMMENT:
            items.append(_parse_init_comment(p))
        else:
            items.append(_parse_field_init(p, seen))
    return StructInitAstNode(items, open_token, p.next())


def _parse_field_init(p: Parser, seen: set[str]) -> StructFieldInitAstNode:
    name_token = p.next()
    expect_token(name_token, TokenType.IDENTIFIER)
    if name_token.value in seen:
        raise ParserSyntaxError(
            f"field `{name_token.value}` is initialized twice",
            name_token,
            code=str(E_PARSER_ISTRUCT_DUPLICATE_FIELD),
            hint="keep one `name = value` entry per field",
        )
    seen.add(name_token.value)
    expect_token(p.next(), TokenType.EQUAL)
    return StructFieldInitAstNode(name_token.value, _parse_init_value(p), name_token)


def _parse_init_value(p: Parser) -> InitValue:
    token = p.current()
    if token.type == TokenType.QUOTED_STRING:
        p.next()
        return StringInitAstNode(token)
    if token.type == TokenType.LBRACE:
        p.next()
        return _parse_struct_init(p, token)
    if token.type == TokenType.LBRAKET:
        p.next()
        return _parse_list_init(p, token)
    return parse_expression(p)


def _parse_list_init(p: Parser, open_token: Token) -> ListInitAstNode:
    """Parse comma-separated values up to the closing `]` (already past `[`)."""
    items: list[InitValue | InitCommentAstNode] = []
    need_comma = False
    while p.current().type != TokenType.RBRAKET:
        token = p.current()
        if token.type == TokenType.COMMENT:
            items.append(_parse_init_comment(p))
        elif need_comma:
            expect_token(p.next(), TokenType.COMMA)
            need_comma = False
        elif token.type == TokenType.QUOTED_STRING:
            raise ParserSyntaxError(
                "strings are not list elements",
                token,
                code=str(E_PARSER_ISTRUCT_STRING_IN_LIST),
                hint='initialize a byte array with the string itself: `name = "TEXT"`',
            )
        else:
            items.append(_parse_init_value(p))
            need_comma = True
    return ListInitAstNode(items, open_token, p.next())


def _parse_init_comment(p: Parser) -> InitCommentAstNode:
    """Consume a comment; it trails a value when it shares that value's line."""
    previous = p.tokens[p.pos - 1] if p.pos > 0 else None
    comment = p.next()
    trailing = (
        previous is not None
        and previous.position is not None
        and comment.position is not None
        and previous.position.line == comment.position.line
    )
    return InitCommentAstNode(comment.value, trailing, comment)


def parse_directive_with_quoted_string(p: Parser) -> str:
    string = p.next()
    expect_token(string, TokenType.QUOTED_STRING)

    return string.value[1:-1]


def parse_assert(p: Parser, keyword: Token) -> AssertAstNode:
    """`.assert EXPR, "message"`."""
    expression = parse_expression(p)
    comma = p.next()
    if comma.type != TokenType.COMMA:
        raise ParserSyntaxError(
            "`.assert` needs a message after its condition",
            comma,
            TokenType.COMMA,
            code=str(E_PARSER_EXPECTED_TOKEN),
            hint='write `.assert EXPR, "what must hold"`; the message is what a failure reports',
        )
    return AssertAstNode(expression, parse_directive_with_quoted_string(p), keyword)


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


def parse_import(p: Parser, keyword: Token) -> ImportAstNode:
    """Parse .import "module_name" directive.

    The .import directive imports all public symbols from a module.
    Module resolution happens at code generation time. The node's
    file_info is the `.import` keyword so diagnostics underline the
    directive itself.
    """
    module_name = parse_directive_with_quoted_string(p)
    return ImportAstNode(module_name, keyword)


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
            record_miss(candidate)

    for search_dir in p.include_paths or []:
        candidate = search_dir / include_path
        if candidate.exists():
            return str(candidate)
        record_miss(candidate)

    return include_path  # let the eventual open() raise the canonical error


#: (resolved path, search paths) -> (content hash, parsed body, lookup misses of its nested includes).
_INCLUDE_AST_CACHE: dict[tuple[str, tuple[str, ...]], tuple[str, list[AstNode], set[str]]] = {}


def clear_include_ast_cache() -> None:
    """Drop every memoised include. For tests and long-running servers."""
    _INCLUDE_AST_CACHE.clear()


def _include_stamp(resolved_path: str) -> str | None:
    """Hash of the file's bytes, or None when it cannot be read (in which case
    the include is parsed fresh and never cached). Content, not mtime and
    size: an edit that keeps both (same size, inside one mtime granule) would
    otherwise serve the old parse to a long-running process."""
    try:
        with open(resolved_path, "rb") as fd:
            return hashlib.sha256(fd.read()).hexdigest()
    except OSError:
        return None


def _parse_include_file(resolved_path: str, include_paths: list[Path]) -> list[AstNode]:
    from a816.parse.parser_states.core import parse_initial

    with open(resolved_path, encoding="utf-8") as fd:
        source = fd.read()
    scanner = Scanner(cast(ScannerStateFunc, lex_initial))
    tokens = scanner.scan(resolved_path, source)
    parser = Parser(tokens, cast(StateFunc, parse_initial), include_paths=include_paths)
    return parser.parse()


def _included_ast(resolved_path: str, include_paths: list[Path]) -> list[AstNode]:
    """The parsed body of an include, memoised per file revision.

    A header pulled in from thirty sites was scanned and parsed thirty
    times; on one project that came to 1024 parses of 55 distinct files.
    The body only depends on the file's bytes and the search paths used to
    resolve its own nested includes, so it is shared between sites.

    Codegen reads these nodes and emits fresh ones rather than mutating
    them, which is what makes sharing safe."""
    key = (resolved_path, tuple(str(path) for path in include_paths))
    stamp = _include_stamp(resolved_path)
    if stamp is not None:
        cached = _INCLUDE_AST_CACHE.get(key)
        if cached is not None and cached[0] == stamp:
            # The nested includes were not resolved again: report what
            # resolving them found missing, for the build cache.
            replay_misses(cached[2])
            return cached[1]
    with recording_misses() as misses:
        sub_ast = _parse_include_file(resolved_path, include_paths)
    replay_misses(misses)
    if stamp is not None:
        # Keyed per file, so the cache stays the size of the project rather
        # than growing with every edit.
        _INCLUDE_AST_CACHE[key] = (stamp, sub_ast, misses)
    return sub_ast


def parse_include(p: Parser, keyword: Token) -> IncludeAstNode:
    include_path = parse_directive_with_quoted_string(p)
    resolved_path = _resolve_include_path(p, keyword, include_path)
    sub_ast = _included_ast(resolved_path, p.include_paths)
    return IncludeAstNode(include_path, sub_ast, keyword, resolved_path=resolved_path)


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
    bss: bool = False
    contexts: list[Token] = field(default_factory=list)


def _expect_identifier(p: Parser) -> Token:
    token = p.next()
    expect_token(token, TokenType.IDENTIFIER)
    return token


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
    elif key == "bss":
        attrs.bss = True  # bare flag: byte-less (WRAM/SRAM/custom-RAM) pool
    elif key == "contexts":
        # `contexts A, B, ...`: mutually exclusive users of a bss pool's
        # memory. Each gets its own allocator (`POOL.A`) over the same ranges.
        attrs.contexts.append(_expect_identifier(p))
        while p.current().type == TokenType.COMMA:
            p.next()
            attrs.contexts.append(_expect_identifier(p))
    else:
        raise ParserSyntaxError(
            f"unknown `.pool` attribute `{key}`",
            key_token,
            code=str(E_PARSER_UNKNOWN_DIRECTIVE_ATTR),
            hint="expected one of: range, fill, strategy, bss, contexts",
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

    close_token = p.next()
    expect_token(close_token, TokenType.RBRACE)
    if not attrs.ranges:
        raise ParserSyntaxError(
            f"pool `{name_token.value}` declares no ranges",
            keyword,
            code=str(E_PARSER_POOL_NO_RANGES),
            hint="add at least one `range LO HI` line so the allocator has space to work with",
        )
    if attrs.contexts and not attrs.bss:
        raise ParserSyntaxError(
            f"pool `{name_token.value}` has contexts but is not `bss`",
            attrs.contexts[0],
            code=str(E_PARSER_UNKNOWN_DIRECTIVE_ATTR),
            hint="only memory can be shared between contexts; emitted bytes have one owner",
        )
    return PoolAstNode(
        name_token.value,
        attrs.ranges,
        attrs.fill,
        attrs.strategy,
        keyword,
        bss=attrs.bss,
        close_token=close_token,
        contexts=[token.value for token in attrs.contexts],
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
        return _parse_pooled_alloc_tail(p, parse_block, keyword, name=None)

    # `.alloc NAME ...` — pooled or named-pinned.
    name = first.value
    separator = _expect_contextual_keyword_one_of(p, ("in", "at"))
    if separator.value == "in":
        return _parse_pooled_alloc_tail(p, parse_block, keyword, name=name)

    return _parse_pinned_alloc_tail(p, parse_block, keyword, name=name)


ParseBlockFn = Callable[[Parser], list[AstNode]]


def _parse_pooled_alloc_tail(p: Parser, parse_block: ParseBlockFn, keyword: Token, *, name: str | None) -> AllocAstNode:
    """Common tail for the pooled shapes: POOL [cross_bank] [align N] { body }.

    `cross_bank` lets a data blob straddle bank edges where the ROM is
    physically contiguous; the code reading it steps the edge itself.
    """
    pool_token = p.next()
    expect_token(pool_token, TokenType.IDENTIFIER)
    cross_bank, align = _parse_alloc_flags(p)
    body, close = _parse_alloc_body(p, parse_block)
    return AllocAstNode(
        name,
        pool_token.value,
        body,
        keyword,
        pool_token=pool_token,
        close_token=close,
        cross_bank=cross_bank,
        align=align,
    )


def _parse_alloc_flags(p: Parser) -> tuple[bool, ExpressionAstNode | None]:
    """`cross_bank` and `align N`, in any order, before the body's `{`."""
    cross_bank = False
    align: ExpressionAstNode | None = None
    while p.current().type == TokenType.IDENTIFIER and p.current().value in ("cross_bank", "align"):
        if p.next().value == "cross_bank":
            cross_bank = True
        else:
            align = parse_expression(p)
    return cross_bank, align


def _parse_pinned_alloc_tail(p: Parser, parse_block: ParseBlockFn, keyword: Token, *, name: str | None) -> AllocAstNode:
    """Common tail for the pinned shapes: ADDR [size N] { body }, or
    ADDR in POOL [cross_bank] [align N] { body }: pinned inside a pool, which
    carves the span out before placing its floating allocs."""
    at_address = parse_expression(p)
    at_size = _parse_optional_size_clause(p)
    pool_token: Token | None = None
    cross_bank, align = False, None
    if p.current().type == TokenType.IDENTIFIER and p.current().value == "in":
        in_token = p.next()
        if at_size is not None:
            raise ParserSyntaxError(
                "`size N` and `in POOL` don't combine: the pool bounds the alloc",
                in_token,
                code=str(E_PARSER_UNEXPECTED_TOKEN),
            )
        pool_token = p.next()
        expect_token(pool_token, TokenType.IDENTIFIER)
        cross_bank, align = _parse_alloc_flags(p)
    body, close = _parse_alloc_body(p, parse_block)
    return AllocAstNode(
        name,
        pool_token.value if pool_token is not None else None,
        body,
        keyword,
        at_address=at_address,
        at_size=at_size,
        pool_token=pool_token,
        close_token=close,
        cross_bank=cross_bank,
        align=align,
    )


def parse_reserve(p: Parser) -> AllocAstNode | ReserveTypedAstNode:
    """Parse a flat byte-less reservation into a `bss` pool:

    * `.reserve NAME SIZE in POOL`: reserve SIZE bytes (allocator picks addr).
    * `.reserve NAME SIZE at ADDR in POOL`: reserve SIZE bytes pinned at ADDR
      (allocator validates the span is in-range + overlap-free). Used for
      fixed memory maps (VRAM/MMIO) where the address is the contract.
    * `.reserve NAME as TYPE in POOL`: reserve sizeof(TYPE) + publish fields.

    The size form desugars to `.alloc NAME in POOL { .res SIZE }` (plus an
    `at ADDR` pin when present); the `as` form is expanded against the struct
    layout at codegen.
    """
    keyword = p.current()
    name_token = p.next()
    expect_token(name_token, TokenType.IDENTIFIER)

    # `.reserve NAME as TYPE in POOL`: struct-typed reservation.
    if p.current().type == TokenType.IDENTIFIER and p.current().value == "as":
        p.next()  # consume `as`
        type_token = p.next()
        expect_token(type_token, TokenType.IDENTIFIER)
        typed_at: ExpressionAstNode | None = None
        if p.current().type == TokenType.IDENTIFIER and p.current().value == "at":
            p.next()  # consume `at`
            typed_at = parse_expression(p)
        _expect_contextual_keyword(p, "in")
        pool_token = p.next()
        expect_token(pool_token, TokenType.IDENTIFIER)
        return ReserveTypedAstNode(
            name_token.value,
            type_token.value,
            pool_token.value,
            keyword,
            type_token=type_token,
            pool_token=pool_token,
            at_address=typed_at,
        )

    size_expr = parse_expression(p)
    # Optional `at ADDR` pin between SIZE and `in POOL`.
    at_address: ExpressionAstNode | None = None
    if p.current().type == TokenType.IDENTIFIER and p.current().value == "at":
        p.next()  # consume `at`
        at_address = parse_expression(p)
    _expect_contextual_keyword(p, "in")
    pool_token = p.next()
    expect_token(pool_token, TokenType.IDENTIFIER)
    body = BlockAstNode([ReserveAstNode(size_expr, keyword)], keyword)
    return AllocAstNode(
        name_token.value, pool_token.value, body, keyword, at_address=at_address, pool_token=pool_token, reserve=True
    )


def _parse_alloc_body(p: Parser, parse_block: ParseBlockFn) -> tuple[BlockAstNode, Token]:
    """Parse `{ body }`; returns the block and its closing `}` token."""
    lbrace = p.next()
    expect_token(lbrace, TokenType.LBRACE)
    body = BlockAstNode(parse_block(p), lbrace)
    return body, p.previous()


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
        pool_token=pool_token,
    )


def parse_reclaim(p: Parser) -> ReclaimAstNode:
    """Parse `.reclaim POOL START END`."""
    keyword = p.current()
    pool_token = p.next()
    expect_token(pool_token, TokenType.IDENTIFIER)
    start = parse_expression(p)
    end = parse_expression(p)
    return ReclaimAstNode(pool_token.value, start, end, keyword)
