import logging
from typing import Callable, List

from a816.parse.ast.nodes import AstNode
from a816.parse.errors import ParserSyntaxError
from a816.parse.tokens import Token, TokenType

logger = logging.getLogger("a816.parser")


class Parser:
    def __init__(self, tokens: List[Token], initial_state: "StateFunc"):
        self.tokens: List[Token] = tokens
        self.pos = 0
        self.initial_state: StateFunc = initial_state

    def parse(self) -> List[AstNode]:
        try:
            return self.initial_state(self)
        except ParserSyntaxError as e:
            logger.exception(e)
            e.token.display()
            raise e

    def current(self) -> Token:
        try:
            return self.tokens[self.pos]
        except IndexError:
            return Token(TokenType.EOF, "")

    def peek(self) -> Token:
        try:
            token: Token = self.tokens[self.pos + 1]
            return token
        except IndexError:
            return Token(TokenType.EOF, "")

    def next(self) -> Token:
        token = self.current()
        self.pos += 1
        return token

    def backup(self) -> Token:
        token = self.current()
        self.pos -= 1
        return token


def expect_token(token: Token, token_type: TokenType) -> None:
    if token.type != token_type:
        raise ParserSyntaxError(f"Expected {token_type} but got {token}", token, token_type)


def expect_tokens(token: Token, token_types: List[TokenType]) -> None:
    if token.type not in token_types:
        raise ParserSyntaxError(f"Expected {token_types} but got {token}", token, token_types[0])


def accept_tokens(token: Token, token_types: List[TokenType]) -> bool:
    return token.type in token_types


def accept_token(token: Token, token_type: TokenType) -> bool:
    return token.type == token_type


StateFunc = Callable[[Parser], List[AstNode]]
