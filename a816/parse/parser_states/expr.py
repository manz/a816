"""Expression parsing + macro application + symbol affectation."""

from __future__ import annotations

from a816.error_codes import (
    E_PARSER_FIELD_ACCESS_NEEDS_CAST,
    E_PARSER_INVALID_EXPRESSION,
    E_PARSER_TYPED_BIND_NEEDS_ASSIGN,
)
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    BinOp,
    BlockAstNode,
    CastAccessExprNode,
    CastValueExprNode,
    ExpressionAstNode,
    ExprNode,
    MacroApplyAstNode,
    Parenthesis,
    SymbolAffectationAstNode,
    Term,
    UnaryOp,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import (
    Parser,
    _token_label,
    accept_token,
    accept_tokens,
    expect_token,
    expect_tokens,
)
from a816.parse.tokens import Token, TokenType


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
            # Late import: parse_block lives in core, which transitively imports this module via dispatch.
            from a816.parse.parser_states.core import parse_block

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
