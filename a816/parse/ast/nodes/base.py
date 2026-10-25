"""AstNode base + expression node hierarchy.

Owns the abstract `AstNode` everything subclasses plus the
expression-token sub-tree (`ExprNode` + `Term`/`BinOp`/`UnaryOp`/
`Parenthesis`/`CastAccess`/`CastValue`/`ExpressionAstNode`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from a816.parse.tokens import Token, TokenType


def _fits_in(text: str, quote: str) -> bool:
    """True when `text` scans back unchanged between `quote`s: the scanner
    ends a string on a bare `quote` and keeps `\\quote` as two characters."""
    return all(index > 0 and text[index - 1] == "\\" for index, char in enumerate(text) if char == quote)


def quoted(text: str, prefer: str = '"') -> str:
    """`text` (a string's raw inner text) between quotes that keep it intact:
    `prefer` when it fits, else the other quote."""
    other = "'" if prefer == '"' else '"'
    quote = prefer if _fits_in(text, prefer) or not _fits_in(text, other) else other
    return f"{quote}{text}{quote}"


class AstNode:
    """Base of every parsed node.

    A plain class, not an `ABC`: `ABCMeta.__instancecheck__` is a Python
    frame, and codegen runs `isinstance` against AST classes millions of
    times per build. `tests/test_ast_nodes_complete.py` keeps the guarantee
    the abstract method gave: every node class implements `to_representation`.
    """

    def __init__(self, kind: str, file_info: Token, docstring: str | None = None) -> None:
        self.kind: Final[str] = kind
        self.file_info: Final = file_info
        self.docstring: Final = docstring

    def to_representation(self) -> tuple[Any, ...]:
        """Returns the tuple representation of the node."""
        raise NotImplementedError(f"{type(self).__name__} has no tuple representation")

    def to_canonical(self) -> str:
        """Returns the canonical representation of the node."""
        return f"# {self.kind} node (to_canonical not implemented)"


@dataclass
class ExprNode:
    token: Token

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExprNode):
            return False

        return self.token == other.token

    def to_canonical(self) -> str:
        """Render this token in the canonical expression form."""
        return self.token.value


class BinOp(ExprNode):
    """Represents a Binary expression operation"""


class UnaryOp(ExprNode):
    """Represents a Unary expression operation"""


class Term(ExprNode):
    """Represents a expression term"""


class Parenthesis(ExprNode):
    """Represents a Parenthesis expression"""


def _inner_canonical(inner: Sequence[ExprNode]) -> str:
    """Terms joined by spaces, a unary operator kept against its operand (`~3`, `-x`)."""
    parts: list[str] = []
    after_unary = False
    for node in inner:
        text = node.to_canonical()
        if after_unary:
            parts[-1] += text
        else:
            parts.append(text)
        after_unary = isinstance(node, UnaryOp)
    return " ".join(parts)


class CastAccessExprNode(ExprNode):
    """`(inner as TYPE).field` — atomic term resolving to `eval(inner) + TYPE.field`.

    Supports chained access (`).a.b.c`) via the `field_path` list. Only the
    leaf access produces a value; intermediate names look up nested struct
    sub-fields registered as `TYPE.a.b.c` during struct codegen.

    `close_token` is the `)` that closed the cast expression (no field
    path included). Fluff fix builders use it to know the byte range
    of `(inner as TYPE)` without re-scanning source.
    """

    def __init__(
        self,
        token: Token,
        inner: Sequence[ExprNode],
        type_name: str,
        field_path: Sequence[str],
        close_token: Token | None = None,
        field_tokens: Sequence[Token] = (),
    ):
        super().__init__(token)
        self.inner: Final = tuple(inner)
        self.type_name: Final = type_name
        self.field_path: Final = tuple(field_path)
        self.close_token: Final = close_token
        # One token per `field_path` entry; diagnostics underline the leaf.
        self.field_tokens: Final = tuple(field_tokens)

    @property
    def leaf_token(self) -> Token:
        """The last `.field` token, or the cast's `(` when none was kept."""
        return self.field_tokens[-1] if self.field_tokens else self.token

    def to_canonical(self) -> str:
        suffix = ".".join(self.field_path)
        return f"({_inner_canonical(self.inner)} as {self.type_name}).{suffix}"


class CastValueExprNode(ExprNode):
    """`(inner as TYPE)` — atomic term that evaluates to `eval(inner)` and carries
    the type tag so an assign RHS can eager-expand into per-field instance symbols.

    `close_token` is the `)` that closed the cast. Fluff fix builders
    use it for the byte range of the full cast expression.
    """

    def __init__(
        self,
        token: Token,
        inner: Sequence[ExprNode],
        type_name: str,
        close_token: Token | None = None,
    ):
        super().__init__(token)
        self.inner: Final = tuple(inner)
        self.type_name: Final = type_name
        self.close_token: Final = close_token

    def to_canonical(self) -> str:
        return f"({_inner_canonical(self.inner)} as {self.type_name})"


# --- expression ordering (infix tokens to RPN) ---

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
    ">=": 6,
    "<=": 6,
    ">": 6,
    "<": 6,
    "==": 7,
    "!=": 7,
    "&": 8,
    "^": 9,
    "|": 10,
}


# Prefix operators bind tightest: `<x + 1` is `(<x) + 1`, `-x * 2` is `(-x) * 2`.
UNARY_PRECEDENCE = 2


def _precedence(node: ExprNode) -> int:
    """A unary operator by its own rank, not that of the binary operator spelled the same (`-`, `<`)."""
    return UNARY_PRECEDENCE if isinstance(node, UnaryOp) else OPERATOR_PRECEDENCE[node.token.value]


def reverse_find_token(items: Sequence[ExprNode], value: str) -> int:
    for pos in range(len(items) - 1, -1, -1):
        if items[pos].token.value == value:
            return pos
    return -1


def _pop_higher_precedence(
    operator_stack: list[ExprNode], output_queue: list[ExprNode], current_precedence: int
) -> None:
    while (
        operator_stack
        and _precedence(operator_stack[-1]) <= current_precedence
        and operator_stack[-1].token.value != "("
    ):
        output_queue.append(operator_stack.pop())


def _pop_until_lparen(operator_stack: list[ExprNode], output_queue: list[ExprNode]) -> None:
    lparen_index = reverse_find_token(operator_stack, "(")
    if lparen_index < 0:
        raise ValueError("mismatched parenthesis")
    while len(operator_stack) > lparen_index + 1:
        output_queue.append(operator_stack.pop())
    operator_stack.pop()


def shunting_yard(expr_nodes: Sequence[ExprNode]) -> list[ExprNode]:
    output_queue: list[ExprNode] = []
    operator_stack: list[ExprNode] = []

    for expr in expr_nodes:
        if isinstance(expr, Term | CastAccessExprNode | CastValueExprNode | SizeofExprNode):
            output_queue.append(expr)
        elif isinstance(expr, BinOp | UnaryOp):
            _pop_higher_precedence(operator_stack, output_queue, _precedence(expr))
            operator_stack.append(expr)
        elif expr.token.type == TokenType.LPAREN:
            operator_stack.append(expr)
        elif expr.token.type == TokenType.RPAREN:
            _pop_until_lparen(operator_stack, output_queue)

    while operator_stack:
        output_queue.append(operator_stack.pop())
    return output_queue


class ExpressionAstNode(AstNode):
    def __init__(self, tokens: Sequence[ExprNode]) -> None:
        super().__init__("expression", tokens[0].token)
        self.tokens: Final[tuple[ExprNode, ...]] = tuple(tokens)
        # Evaluation order, worked out once: tokens never change, and one
        # expression is evaluated per importer and per label pass. None
        # when the parentheses do not match; evaluation then reports it.
        self.rpn: Final = _rpn_or_none(self.tokens)

    def to_representation(self) -> tuple[Any, ...]:
        return (_inner_canonical(self.tokens),)

    def to_canonical(self) -> str:
        return _inner_canonical(self.tokens)


SIZE_OPERATORS = ("sizeof", "countof")


class SizeofExprNode(ExprNode):
    """`sizeof(PATH)` / `countof(PATH)`: a size, never an address.

    `sizeof` reads a struct, a struct field or a reservation; `countof`
    reads an array field's element count. `token` is the operator keyword,
    `path_token` the single identifier between the parentheses (`T.a.b`).
    """

    def __init__(self, token: Token, path_token: Token, close_token: Token | None = None) -> None:
        super().__init__(token)
        self.path_token: Final = path_token
        self.close_token: Final = close_token

    @property
    def kind(self) -> str:
        return self.token.value

    @property
    def path(self) -> str:
        return self.path_token.value

    def to_canonical(self) -> str:
        return f"{self.kind}({self.path})"


def _rpn_or_none(tokens: Sequence[ExprNode]) -> tuple[ExprNode, ...] | None:
    try:
        return tuple(shunting_yard(tokens))
    except ValueError:
        return None
