"""An expression node orders its tokens once, at construction."""

from __future__ import annotations

import pytest

from a816.parse.ast.expression import eval_expression, expr_to_ast
from a816.parse.ast.nodes import ExpressionAstNode, Parenthesis, Term
from a816.parse.tokens import Token, TokenType
from a816.symbols import Resolver


def test_the_order_is_worked_out_at_construction() -> None:
    expression = expr_to_ast("1 + 2 * 3")

    assert [node.token.value for node in expression.rpn or ()] == ["1", "2", "3", "*", "+"]


def test_the_stored_order_evaluates() -> None:
    assert eval_expression(expr_to_ast("(1 + 2) * 3"), Resolver()) == 9


def _unmatched() -> ExpressionAstNode:
    return ExpressionAstNode([Term(Token(TokenType.NUMBER, "1")), Parenthesis(Token(TokenType.RPAREN, ")"))])


def test_an_unmatched_parenthesis_leaves_no_order() -> None:
    assert _unmatched().rpn is None


def test_an_unmatched_parenthesis_is_reported_when_evaluated() -> None:
    expression = _unmatched()

    with pytest.raises(ValueError, match="mismatched parenthesis"):
        eval_expression(expression, Resolver())
