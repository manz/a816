"""Node-level error types: NodeError + UnknownOpcodeError."""

from __future__ import annotations

from a816.exceptions import A816Error
from a816.parse.tokens import Token


class UnknownOpcodeError(Exception):
    pass


class NodeError(A816Error):
    def __init__(
        self,
        message: str,
        file_info: Token,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.file_info = file_info
        self.message = message
        self.code = code
        self.hint = hint

    def __str__(self) -> str:
        return self.format()

    def format(self) -> str:
        """Format the error with source location, visual indicator, code, and hint."""
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import SourceLocation, format_error

        location = None
        if self.file_info is not None and self.file_info.position is not None:
            pos = self.file_info.position
            try:
                source_line = pos.get_line()
            except (IndexError, AttributeError):
                source_line = ""
            file = getattr(pos, "file", None)
            lines = getattr(file, "lines", None)
            context_before: list[str] | None = None
            context_after: list[str] | None = None
            if lines:
                line_idx = pos.line
                context_before = [lines[i] for i in range(max(0, line_idx - 1), line_idx)] or None
                context_after = [lines[i] for i in range(line_idx + 1, min(len(lines), line_idx + 2))] or None
            location = SourceLocation(
                filename=pos.file.filename,
                line=pos.line,
                column=pos.column,
                source_line=source_line,
                length=len(self.file_info.value) if self.file_info.value else 1,
                context_before=context_before,
                context_after=context_after,
            )

        return format_error(self.message, location, code=self.code, hint=self.hint)
