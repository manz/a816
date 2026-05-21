"""Formatter configuration knobs."""

from __future__ import annotations


class FormattingOptions:
    """Configuration for code formatting"""

    def __init__(
        self,
        *,
        indent_size: int = 4,
        opcode_indent: int | None = None,
        operand_alignment: int = 16,
        comment_alignment: int = 0,
        preserve_empty_lines: bool = True,
        max_empty_lines: int = 2,
        align_labels: bool = True,
        space_after_comma: bool = True,
        max_line_length: int = 120,
    ):
        self.indent_size = indent_size
        self.opcode_indent = opcode_indent if opcode_indent is not None else indent_size
        self.operand_alignment = operand_alignment
        self.comment_alignment = comment_alignment
        self.preserve_empty_lines = preserve_empty_lines
        self.max_empty_lines = max_empty_lines
        self.align_labels = align_labels
        self.space_after_comma = space_after_comma
        self.max_line_length = max_line_length
