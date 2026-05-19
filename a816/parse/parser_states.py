import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeGuard, cast

from a816.cpu.cpu_65c816 import AddressingMode, ValueSize
from a816.error_codes import (
    E_PARSER_FIELD_ACCESS_NEEDS_CAST,
    E_PARSER_INVALID_EXPRESSION,
    E_PARSER_POOL_NO_RANGES,
    E_PARSER_STRUCT_DUPLICATE_FIELD,
    E_PARSER_TYPED_BIND_NEEDS_ASSIGN,
    E_PARSER_UNEXPECTED_TOKEN,
    E_PARSER_UNKNOWN_DIRECTIVE_ATTR,
    E_PARSER_UNKNOWN_POOL_STRATEGY,
)
from a816.parse.ast.nodes import (
    AllocAstNode,
    AsciiAstNode,
    AssignAstNode,
    AstNode,
    BinOp,
    BlockAstNode,
    CastAccessExprNode,
    CastValueExprNode,
    CodeLookupAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    CompoundAstNode,
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
    KeywordAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapArgs,
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
    index_map,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import (
    Parser,
    StateFunc,
    _got_label,
    _token_label,
    accept_token,
    accept_tokens,
    expect_token,
    expect_tokens,
)
from a816.parse.scanner import Scanner, ScannerStateFunc
from a816.parse.scanner_states import lex_initial
from a816.parse.tokens import Token, TokenType


def parse_scope(p: Parser) -> ScopeAstNode:
    current = p.current()
    keyword = p.next()
    expect_token(keyword, TokenType.IDENTIFIER)

    next_token = p.next()
    expect_token(next_token, TokenType.LBRACE)
    block = parse_block(p)
    docstring, block = extract_docstring(block)
    return ScopeAstNode(keyword.value, BlockAstNode(block, next_token), current, docstring=docstring)


def parse_macro_definition_args(p: Parser) -> list[str]:
    args = []

    first_arg = p.next()
    if not accept_token(first_arg, TokenType.RPAREN):
        expect_token(first_arg, TokenType.IDENTIFIER)

        args.append(first_arg.value)

        while True:
            token = p.next()
            expect_tokens(token, [TokenType.COMMA, TokenType.RPAREN, TokenType.IDENTIFIER])

            if accept_token(token, TokenType.RPAREN):
                p.backup()
                break
            elif accept_token(token, TokenType.COMMA):
                continue
            else:
                expect_token(token, TokenType.IDENTIFIER)
                args.append(token.value)
    else:
        p.backup()
    return args


def parse_expression_list_inner(
    p: Parser,
) -> list[ExpressionAstNode | BlockAstNode]:
    expressions: list[ExpressionAstNode | BlockAstNode] = []
    while True:
        if accept_token(p.current(), TokenType.RPAREN):
            break
        if accept_token(p.current(), TokenType.LBRACE):
            current = p.current()
            p.next()
            expressions.append(BlockAstNode(parse_block(p), current))
        else:
            expressions.append(parse_expression(p))
        if accept_tokens(p.current(), [TokenType.COMMA]):
            p.next()
        else:
            break

    return expressions


def parse_expression_list(p: Parser) -> list[ExpressionAstNode | BlockAstNode]:
    expect_token(p.next(), TokenType.LPAREN)
    expressions = parse_expression_list_inner(p)

    expect_token(p.next(), TokenType.RPAREN)

    return expressions


def parse_macro_application(p: Parser) -> MacroApplyAstNode:
    macro_identifier = p.next()
    expect_token(macro_identifier, TokenType.IDENTIFIER)
    return MacroApplyAstNode(macro_identifier.value, parse_expression_list(p), macro_identifier)


def parse_macro(p: Parser) -> MacroAstNode:
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
        fields.append((name_token.value, type_token.value))
        p.next()

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
    """Parse `.alloc NAME in POOL { body }`."""
    keyword = p.current()
    name_token = p.next()
    expect_token(name_token, TokenType.IDENTIFIER)
    _expect_contextual_keyword(p, "in")
    pool_token = p.next()
    expect_token(pool_token, TokenType.IDENTIFIER)
    lbrace = p.next()
    expect_token(lbrace, TokenType.LBRACE)
    body = BlockAstNode(parse_block(p), lbrace)
    return AllocAstNode(name_token.value, pool_token.value, body, keyword)


def parse_relocate(p: Parser) -> RelocateAstNode:
    """Parse `.relocate SYMBOL OLD_START OLD_END into POOL { body }`."""
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


_KEYWORD_HANDLERS: dict[str, Callable[[Parser, Token], AstNode]] = {
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


def parse_keyword(p: Parser) -> KeywordAstNode:
    keyword = p.next()
    handler = _KEYWORD_HANDLERS.get(keyword.value)
    if handler is None:
        raise ParserSyntaxError(
            f"unknown directive `.{keyword.value}`",
            keyword,
            code=str(E_PARSER_UNEXPECTED_TOKEN),
            hint="see https://a816.ringum.net/directives/ for the list of supported `.` directives",
        )
    return cast(KeywordAstNode, handler(p, keyword))


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


def parse_expression(p: Parser) -> ExpressionAstNode:
    nodes = _parse_expression(p)

    return ExpressionAstNode(nodes)


def parse_expression_ep(p: Parser) -> list[AstNode]:
    return [parse_expression(p)]


def _consume_dot_field_path(p: Parser) -> list[str]:
    """Consume a `.IDENT(.IDENT)*` postfix from the token stream."""
    path: list[str] = []
    while p.current().type == TokenType.DOT:
        p.next()
        field_token = p.next()
        expect_token(field_token, TokenType.IDENTIFIER)
        path.append(field_token.value)
    return path


def _parse_lparen_expression(p: Parser, lparen: Token) -> list[ExprNode]:
    """Parse `(inner [as TYPE]) [.field...]`.

    Three shapes emerge:
      - `(inner)` plain parenthesised expression
      - `(inner as T)` typed value carrying a type tag for assign RHS
      - `(inner as T).field(.sub)*` field access into the type's layout
    """
    inner = _parse_expression(p)
    type_name: str | None = None
    as_token = p.current()
    if as_token.type == TokenType.IDENTIFIER and as_token.value == "as":
        p.next()
        type_token = p.next()
        expect_token(type_token, TokenType.IDENTIFIER)
        type_name = type_token.value
    expect_token(p.current(), TokenType.RPAREN)
    p.next()  # consume RPAREN

    field_path = _consume_dot_field_path(p)

    if type_name is not None and field_path:
        return [CastAccessExprNode(lparen, inner, type_name, field_path)]
    if type_name is not None:
        return [CastValueExprNode(lparen, inner, type_name)]
    if field_path:
        raise ParserSyntaxError(
            "field access requires a typed cast",
            lparen,
            code=str(E_PARSER_FIELD_ACCESS_NEEDS_CAST),
            hint="use `(expr as Type).field` so the resolver knows which struct layout to apply",
        )
    # Plain parenthesised expression — restore the wrapping tokens for shunting yard.
    rparen = Token(TokenType.RPAREN, ")", lparen.position)
    return [Parenthesis(lparen), *inner, Parenthesis(rparen)]


def _parse_expression(p: Parser) -> list[ExprNode]:
    tokens: list[ExprNode] = []
    current_token = p.next()
    if accept_token(current_token, TokenType.LPAREN):
        tokens += _parse_lparen_expression(p, current_token)
    elif accept_tokens(
        current_token, [TokenType.NUMBER, TokenType.BOOLEAN, TokenType.QUOTED_STRING, TokenType.IDENTIFIER]
    ):
        tokens.append(Term(current_token))
    elif accept_token(current_token, TokenType.OPERATOR) and current_token.value in ["-", "~"]:
        tokens.append(UnaryOp(current_token))
        tokens += _parse_expression(p)
    else:
        raise ParserSyntaxError(
            f"invalid expression at {_token_label(current_token.type)}",
            current_token,
            code=str(E_PARSER_INVALID_EXPRESSION),
            hint="expressions accept numbers, identifiers, `(...)`, unary `-`/`~`, and binary operators",
        )

    if tokens:
        operator = p.current()

        if accept_token(operator, TokenType.OPERATOR):
            p.next()
            tokens.append(BinOp(operator))
            return tokens + _parse_expression(p)
        else:
            return tokens
    return tokens


def parse_symbol_affectation(
    p: Parser,
) -> SymbolAffectationAstNode | AssignAstNode:
    current = p.current()
    symbol = p.next()
    operator = p.next()
    expect_tokens(operator, [TokenType.EQUAL, TokenType.ASSIGN])
    node_type: type[SymbolAffectationAstNode] | type[AssignAstNode]
    if operator.type == TokenType.EQUAL:
        node_type = SymbolAffectationAstNode
    else:
        node_type = AssignAstNode

    expression = parse_expression(p)

    # `p := expr as T` (no parens) — wrap RHS as a typed cast so codegen can
    # eager-expand the per-field instance symbols.
    as_token = p.current()
    if as_token.type == TokenType.IDENTIFIER and as_token.value == "as":
        if operator.type != TokenType.ASSIGN:
            raise ParserSyntaxError(
                "typed-cast bind requires `:=`, not `=`",
                as_token,
                code=str(E_PARSER_TYPED_BIND_NEEDS_ASSIGN),
                hint="use `name := expr as T` so the binding eager-expands per-field instance symbols",
            )
        p.next()
        type_token = p.next()
        expect_token(type_token, TokenType.IDENTIFIER)
        cast_term = CastValueExprNode(current, list(expression.tokens), type_token.value)
        expression = ExpressionAstNode([cast_term])

    return node_type(symbol.value, expression, current)


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
