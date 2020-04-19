from a816.parse.tokens import Position, Token, TokenType


class ParserSyntaxError(Exception):
    def __init__(self, message, token: Token, expected_token_type: TokenType or None = None) -> None:
        super().__init__(message)
        self.token: Token = token
        self.expected_token_type: TokenType = expected_token_type


class ScannerException(Exception):
    def __init__(self, message, position: Position) -> None:
        super().__init__(message)
        self.position: Position = position
