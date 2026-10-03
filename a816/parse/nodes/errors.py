"""Node-level error types: NodeError + UnknownOpcodeError."""

from __future__ import annotations

from a816.errors import SourceLocation, format_error
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
        return format_error(self.message, node_source_location(self.file_info), code=self.code, hint=self.hint)


def node_source_location(file_info: Token | None) -> SourceLocation | None:
    """Build the caret-rendering location for a node's source token."""
    if file_info is None or file_info.position is None:
        return None
    pos = file_info.position
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
    return SourceLocation(
        filename=pos.file.filename,
        line=pos.line,
        column=pos.column,
        source_line=source_line,
        length=len(file_info.value) if file_info.value else 1,
        context_before=context_before,
        context_after=context_after,
    )


def format_node_warning(message: str, file_info: Token, *, code: str | None = None, hint: str | None = None) -> str:
    """Render a located warning with the same layout as `NodeError`."""
    return format_error(message, node_source_location(file_info), error_type="warning", code=code, hint=hint)
