from typing import Optional

from a816.parse.tokens import Position, Token, TokenType


class ParserSyntaxError(Exception):
    def __init__(self, message: str, token: Token, expected_token_type: Optional[TokenType] = None) -> None:
        super().__init__(message)
        self.token: Token = token
        self.expected_token_type: Optional[TokenType] = expected_token_type


class ScannerException(Exception):
    def __init__(self, message: str, position: Position) -> None:
        super().__init__(message)
        self.position: Position = position
