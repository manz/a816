"""Error display utilities for consistent, readable error messages."""

import os
import sys
from dataclasses import dataclass

# ANSI color codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"


def _should_use_colors() -> bool:
    """Determine if colors should be used based on environment and terminal."""
    # NO_COLOR convention: https://no-color.org/
    if os.environ.get("NO_COLOR"):
        return False
    # FORCE_COLOR forces colors even without TTY
    if os.environ.get("FORCE_COLOR"):
        return True
    # CLICOLOR_FORCE also forces colors
    if os.environ.get("CLICOLOR_FORCE"):
        return True
    # Check if stderr is a TTY
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


# Check if output supports colors
_USE_COLORS = _should_use_colors()


def _color(text: str, *codes: str) -> str:
    """Apply ANSI color codes to text if colors are enabled."""
    if not _USE_COLORS:
        return text
    return "".join(codes) + text + _RESET


def _bold(text: str) -> str:
    return _color(text, _BOLD)


def _red(text: str) -> str:
    return _color(text, _RED)


def _bold_red(text: str) -> str:
    return _color(text, _BOLD, _RED)


def _cyan(text: str) -> str:
    return _color(text, _CYAN)


def _dim(text: str) -> str:
    return _color(text, _DIM)


def _yellow(text: str) -> str:
    return _color(text, _YELLOW)


def _blue(text: str) -> str:
    return _color(text, _BLUE)


def _magenta(text: str) -> str:
    return _color(text, _MAGENTA)


@dataclass
class SourceLocation:
    """Represents a location in source code.

    `context_before` / `context_after` carry up to N source lines for
    context-aware error rendering. Both default to empty so existing
    callers keep working; populate when you have a Position with a
    `file.lines` handle.
    """

    filename: str
    line: int
    column: int
    source_line: str
    length: int = 1
    context_before: list[str] | None = None
    context_after: list[str] | None = None

    def __str__(self) -> str:
        return f"{self.filename}:{self.line + 1}:{self.column + 1}"


def _gutter(n: int, width: int) -> str:
    """Right-align line number `n` in a field of `width` chars, colored cyan."""
    return _cyan(str(n).rjust(width))


def _empty_gutter(width: int) -> str:
    return " " * width


def _render_source_block(location: SourceLocation, gutter_width: int) -> list[str]:
    """Render the `| source` block with context lines and a caret pointer."""
    out: list[str] = []
    out.append(f"{_empty_gutter(gutter_width)} {_cyan('|')}")

    main_line = location.line + 1
    for offset, ctx in enumerate(location.context_before or [], start=1):
        ln = main_line - len(location.context_before or []) + offset - 1
        out.append(f"{_gutter(ln, gutter_width)} {_cyan('|')} {ctx.rstrip()}")

    source = location.source_line.rstrip() if location.source_line else ""
    out.append(f"{_gutter(main_line, gutter_width)} {_cyan('|')} {source}")

    caret_padding = " " * location.column
    caret = "^" * max(1, location.length)
    out.append(f"{_empty_gutter(gutter_width)} {_cyan('|')} {caret_padding}{_bold_red(caret)}")

    for offset, ctx in enumerate(location.context_after or [], start=1):
        ln = main_line + offset
        out.append(f"{_gutter(ln, gutter_width)} {_cyan('|')} {ctx.rstrip()}")

    return out


def _max_line_number(location: SourceLocation) -> int:
    after_count = len(location.context_after or [])
    return location.line + 1 + after_count


def format_error(
    message: str,
    location: SourceLocation | None = None,
    error_type: str = "error",
    hint: str | None = None,
    note: str | None = None,
    code: str | None = None,
) -> str:
    """Format an error message with source location and visual indicators.

    Args:
        message: The main error message
        location: Source location (file, line, column, source text)
        error_type: Type of error (e.g., "error", "warning", "note")
        hint: Optional hint for fixing the error
        note: Optional additional note
        code: Stable error code (e.g. "E0042"). Renders as `error[E0042]: ...`.

    Returns:
        Formatted error string with colors and visual indicators.
    """
    lines: list[str] = []

    if error_type == "error":
        type_str = _bold_red("error")
    elif error_type == "warning":
        type_str = _color("warning", _BOLD, _YELLOW)
    else:
        type_str = _bold(error_type)

    code_str = f"[{code}]" if code else ""
    if code_str:
        type_str = f"{type_str}{_bold_red(code_str)}"
    lines.append(f"{type_str}{_bold(':')}{_bold(' ' + message)}")

    if location is not None:
        location_str = f"{location.filename}:{location.line + 1}:{location.column + 1}"
        gutter_width = len(str(_max_line_number(location)))

        lines.append(f"{_empty_gutter(gutter_width)} {_cyan('-->')} {location_str}")
        lines.extend(_render_source_block(location, gutter_width))

        if hint:
            lines.append(f"{_empty_gutter(gutter_width)} {_cyan('=')} {_cyan('hint:')} {hint}")
        if note:
            lines.append(f"{_empty_gutter(gutter_width)} {_cyan('=')} {_cyan('note:')} {note}")
    else:
        if hint:
            lines.append(f"  {_cyan('hint:')} {hint}")
        if note:
            lines.append(f"  {_cyan('note:')} {note}")

    return "\n".join(lines)


def format_error_simple(
    error_type: str,
    message: str,
    details: list[tuple[str, str]] | None = None,
) -> str:
    """Format a simple error without source location.

    Args:
        error_type: Type of error (e.g., "linker error", "io error")
        message: The main error message
        details: Optional list of (label, value) tuples for additional info

    Returns:
        Formatted error string
    """
    lines: list[str] = []

    # Header
    lines.append(f"{_bold_red(error_type + ':')}{_bold(' ' + message)}")

    # Details
    if details:
        for label, value in details:
            lines.append(f"  {_cyan(label + ':')} {value}")

    return "\n".join(lines)


def format_linker_error(
    message: str,
    symbol: str | None = None,
    symbols: set[str] | None = None,
    hint: str | None = None,
) -> str:
    """Format a linker error with symbol information.

    Args:
        message: The main error message
        symbol: Single symbol involved in the error
        symbols: Multiple symbols involved in the error
        hint: Optional hint for fixing

    Returns:
        Formatted error string
    """
    lines: list[str] = []

    # Header
    lines.append(f"{_bold_red('linker error:')}{_bold(' ' + message)}")

    # Symbol list
    if symbol:
        lines.append(f"  {_cyan('symbol:')} {_yellow(symbol)}")
    elif symbols:
        lines.append(f"  {_cyan('symbols:')}")
        for sym in sorted(symbols):
            lines.append(f"    {_yellow('- ' + sym)}")

    # Hint
    if hint:
        lines.append(f"  {_cyan('hint:')} {hint}")

    return "\n".join(lines)


def format_multiple_errors(errors: list[str], summary: str | None = None) -> str:
    """Format multiple errors with a summary.

    Args:
        errors: List of pre-formatted error strings
        summary: Optional summary line

    Returns:
        Combined formatted string
    """
    result = "\n\n".join(errors)
    if summary:
        error_word = "error" if len(errors) == 1 else "errors"
        result += f"\n\n{_bold_red(f'{len(errors)} {error_word}')}: {summary}"
    return result
