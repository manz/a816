from dataclasses import dataclass

from a816.parse.tokens import Position, Token, TokenType


@dataclass
class ParseError:
    """Structured parse error with location information."""

    message: str
    filename: str
    line: int  # 0-indexed
    column: int  # 0-indexed
    length: int = 1
    source_line: str = ""

    def format(self) -> str:
        """Format error for display."""
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import SourceLocation, format_error

        location = SourceLocation(
            filename=self.filename,
            line=self.line,
            column=self.column,
            source_line=self.source_line,
            length=self.length,
        )
        return format_error(self.message, location)


class ParserSyntaxError(Exception):
    def __init__(
        self,
        message: str,
        token: Token,
        expected_token_type: TokenType | None = None,
    ) -> None:
        super().__init__(message)
        self.token: Token = token
        self.expected_token_type: TokenType | None = expected_token_type


class ScannerException(Exception):
    def __init__(self, message: str, position: Position) -> None:
        super().__init__(message)
        self.position: Position = position
