from typing import List

from a816.parse.ast.nodes import BinOp, ExpressionAstNode, ExprNode, Term, UnaryOp
from a816.parse.tokens import TokenType
from a816.symbols import Resolver

OPERATOR_PRECEDENCE = {
    # unary 1
    "(": 1,
    ")": 1,
    "~": 2,
    "*": 3,
    "/": 3,
    "%": 3,
    "+": 4,
    "-": 4,
    "<<": 5,
    ">>": 5,
    "&": 8,
    "^": 9,
    "|": 10,
}


def reverse_find_token(items: List[ExprNode], value: str) -> int:
    for pos in range(len(items) - 1, -1, -1):
        if items[pos].token.value == value:
            return pos
    return -1


def shunting_yard(expr_nodes: List[ExprNode]) -> List[ExprNode]:
    output_queue: List[ExprNode] = []
    operator_stack: List[ExprNode] = []

    for expr in expr_nodes:
        if isinstance(expr, Term):
            output_queue.append(expr)
        elif isinstance(expr, BinOp) or isinstance(expr, UnaryOp):
            current_precedence = OPERATOR_PRECEDENCE[expr.token.value] if isinstance(expr, BinOp) else 2

            while (
                len(operator_stack) > 0
                and OPERATOR_PRECEDENCE[operator_stack[-1].token.value] <= current_precedence
                and operator_stack[-1].token.value != "("
            ):
                output_queue.append(operator_stack.pop())
            operator_stack.append(expr)
        elif expr.token.type == TokenType.LPAREN:
            operator_stack.append(expr)
        elif expr.token.type == TokenType.RPAREN:
            lparen_index = reverse_find_token(operator_stack, "(")

            if lparen_index < 0:
                raise ValueError("mismatched parenthesis")

            while len(operator_stack) > lparen_index + 1:
                op = operator_stack.pop()
                output_queue.append(op)
            operator_stack.pop()

    while len(operator_stack) > 0:
        output_queue.append(operator_stack.pop())

    return output_queue


def eval_number(number: str) -> int:
    if number.startswith("0x"):
        base = 16
    elif number.startswith("0b"):
        base = 2
    else:
        base = 10

    return int(number, base)


def eval_expression(expression: ExpressionAstNode, resolver: Resolver) -> int:
    tokens = expression.tokens
    ordered = shunting_yard(tokens)

    values_stack: List[int] = []
    r: int

    for current in ordered:
        if current.token.type == TokenType.NUMBER:
            values_stack.append(eval_number(current.token.value))
        elif current.token.type == TokenType.IDENTIFIER:
            resolved_value = resolver.current_scope.value_for(current.token.value)
            if isinstance(resolved_value, int):
                values_stack.append(resolved_value)
            else:
                raise RuntimeError(f"Unable  to resolve {current.token.value}")
        elif isinstance(current, UnaryOp):
            v1 = values_stack.pop()

            if current.token.value == "-":
                r = -v1
            else:
                raise RuntimeError(f"Unsupported unary Operator {current.token}")

            values_stack.append(r)
        elif isinstance(current, BinOp):
            v2 = values_stack.pop()
            v1 = values_stack.pop()

            if current.token.value == "+":
                r = v1 + v2
            elif current.token.value == "-":
                r = v1 - v2
            elif current.token.value == "*":
                r = v1 * v2
            elif current.token.value == "&":
                r = v1 & v2
            elif current.token.value == "|":
                r = v1 | v2
            elif current.token.value == ">>":
                r = v1 >> v2
            elif current.token.value == "<<":
                r = v1 << v2
            else:
                raise RuntimeError("operator unknown")
            values_stack.append(r)
    return values_stack.pop()


def expr_to_ast(expr_str: str) -> ExpressionAstNode:
    from a816.parse.parser import Parser
    from a816.parse.parser_states import parse_expression_ep
    from a816.parse.scanner import Scanner
    from a816.parse.scanner_states import lex_expression

    scanner = Scanner(lex_expression)
    tokens = scanner.scan("memory", expr_str)
    parser = Parser(tokens, parse_expression_ep)
    nodes = parser.parse()
    first_node = nodes[0]
    assert isinstance(first_node, ExpressionAstNode)
    return first_node


def eval_expression_str(expr_str: str, resolver: Resolver) -> int:
    expr_node = expr_to_ast(expr_str)
    return eval_expression(expr_node, resolver)
