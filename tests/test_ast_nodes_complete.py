"""`AstNode` is a plain base class (no `ABCMeta`, for `isinstance` speed); this
keeps the guarantee its abstract method used to give."""

from __future__ import annotations

import pytest

import a816.parse.ast.nodes  # noqa: F401  (registers every node class)
from a816.parse.ast.nodes import AstNode
from a816.parse.tokens import Token, TokenType


def _node_classes() -> list[type[AstNode]]:
    found: list[type[AstNode]] = []
    pending = list(AstNode.__subclasses__())
    while pending:
        cls = pending.pop()
        found.append(cls)
        pending.extend(cls.__subclasses__())
    return found


def test_every_node_class_has_a_representation() -> None:
    missing = [cls.__name__ for cls in _node_classes() if cls.to_representation is AstNode.to_representation]

    assert missing == []


def test_the_base_is_not_an_abc() -> None:
    assert type(AstNode) is type


def test_the_check_sees_every_node_class() -> None:
    assert len(_node_classes()) >= 40


def test_the_base_representation_raises() -> None:
    node = AstNode("bare", Token(TokenType.IDENTIFIER, "x"))

    with pytest.raises(NotImplementedError):
        node.to_representation()
