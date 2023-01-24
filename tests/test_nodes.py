from unittest import TestCase

from a816.parse.ast.nodes import LabelAstNode
from a816.parse.tokens import Token, TokenType


def make_token(value: str, token_type: TokenType) -> Token:
    return Token(token_type, value)


class NodesTest(TestCase):
    def test_label_node(self) -> None:
        label_node = LabelAstNode("a_label", make_token("a_label", TokenType.LABEL))

        self.assertEqual(("label", "a_label"), label_node.to_representation())
