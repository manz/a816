"""AstNode base + expression node hierarchy.

Owns the abstract `AstNode` everything subclasses plus the
expression-token sub-tree (`ExprNode` + `Term`/`BinOp`/`UnaryOp`/
`Parenthesis`/`CastAccess`/`CastValue`/`ExpressionAstNode`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from a816.parse.tokens import Token


class AstNode(ABC):
    kind: str

    def __init__(self, kind: str, file_info: Token, docstring: str | None = None) -> None:
        self.kind = kind
        self.file_info = file_info
        self.docstring = docstring

    @abstractmethod
    def to_representation(self) -> tuple[Any, ...]:
        """Returns the tuple representation of the node."""

    def to_canonical(self) -> str:
        """Returns the canonical representation of the node."""
        return f"# {self.kind} node (to_canonical not implemented)"


@dataclass
class ExprNode:
    token: Token

    def __eq__(self, other: Any) -> bool:
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


def _inner_canonical(inner: list[ExprNode]) -> str:
    return " ".join(node.to_canonical() for node in inner)


class CastAccessExprNode(ExprNode):
    """`(inner as TYPE).field` — atomic term resolving to `eval(inner) + TYPE.field`.

    Supports chained access (`).a.b.c`) via the `field_path` list. Only the
    leaf access produces a value; intermediate names look up nested struct
    sub-fields registered as `TYPE.a.b.c` during struct codegen.
    """

    def __init__(self, token: Token, inner: list[ExprNode], type_name: str, field_path: list[str]):
        super().__init__(token)
        self.inner = inner
        self.type_name = type_name
        self.field_path = field_path

    def to_canonical(self) -> str:
        suffix = ".".join(self.field_path)
        return f"({_inner_canonical(self.inner)} as {self.type_name}).{suffix}"


class CastValueExprNode(ExprNode):
    """`(inner as TYPE)` — atomic term that evaluates to `eval(inner)` and carries
    the type tag so an assign RHS can eager-expand into per-field instance symbols.
    """

    def __init__(self, token: Token, inner: list[ExprNode], type_name: str):
        super().__init__(token)
        self.inner = inner
        self.type_name = type_name

    def to_canonical(self) -> str:
        return f"({_inner_canonical(self.inner)} as {self.type_name})"


class ExpressionAstNode(AstNode):
    tokens: list[ExprNode]

    def __init__(self, tokens: list[ExprNode]) -> None:
        super().__init__("expression", tokens[0].token)
        self.tokens = tokens

    def to_representation(self) -> tuple[Any, ...]:
        return (" ".join([expr_node.to_canonical() for expr_node in self.tokens]),)

    def to_canonical(self) -> str:
        return " ".join([expr_node.to_canonical() for expr_node in self.tokens])
