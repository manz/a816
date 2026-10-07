"""AstNode base + expression node hierarchy.

Owns the abstract `AstNode` everything subclasses plus the
expression-token sub-tree (`ExprNode` + `Term`/`BinOp`/`UnaryOp`/
`Parenthesis`/`CastAccess`/`CastValue`/`ExpressionAstNode`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from a816.parse.tokens import Token


class AstNode(ABC):
    def __init__(self, kind: str, file_info: Token, docstring: str | None = None) -> None:
        self.kind: Final[str] = kind
        self.file_info: Final = file_info
        self.docstring: Final = docstring

    @abstractmethod
    def to_representation(self) -> tuple[Any, ...]:
        """Returns the tuple representation of the node."""

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


class ExpressionAstNode(AstNode):
    def __init__(self, tokens: Sequence[ExprNode]) -> None:
        super().__init__("expression", tokens[0].token)
        self.tokens: Final[tuple[ExprNode, ...]] = tuple(tokens)

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
