from dataclasses import dataclass, field

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
    code: str | None = None
    hint: str | None = None
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)

    def format(self) -> str:
        """Format error for display."""
        # Late import: avoid circular dependency with a816.errors.
        from a816.errors import SourceLocation, format_error

        location = SourceLocation(
            filename=self.filename,
            line=self.line,
            column=self.column,
            source_line=self.source_line,
            length=self.length,
            context_before=list(self.context_before) or None,
            context_after=list(self.context_after) or None,
        )
        return format_error(self.message, location, code=self.code, hint=self.hint)


class ParserSyntaxError(Exception):
    """Raised by parser state functions when the token stream is invalid.

    `code` carries the stable error identifier and `hint` a fix
    suggestion; both flow through `ParseError` into the formatted user
    output so an `error[E0100]: ... hint: ...` block lands in the CLI.
    """

    def __init__(
        self,
        message: str,
        token: Token,
        expected_token_type: TokenType | None = None,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.token: Token = token
        self.expected_token_type: TokenType | None = expected_token_type
        self.code: str | None = code
        self.hint: str | None = hint


class ScannerException(Exception):
    def __init__(
        self,
        message: str,
        position: Position,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.position: Position = position
        self.code: str | None = code
        self.hint: str | None = hint
