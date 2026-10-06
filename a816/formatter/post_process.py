"""Post-processing pipeline for formatted line lists.

Free functions operating on `list[str]` with `FormattingOptions` injected
where needed. The class hands a list of lines off here; this module owns
the line-level passes (wrap, collapse, separate labels, align comments,
strip blanks after position directives, finalize).
"""

from __future__ import annotations

import re

from a816.formatter.options import FormattingOptions

# Each line is rstripped before this regex sees it, so the trailing
# group is bounded — no nested overlapping `\s*` runs that could
# backtrack on adversarial input.
_PAREN_WRAP_RE: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)(?P<head>(?:\.macro[ \t]+)?[A-Za-z_][\w.]*)"
    r"\((?P<params>[^()]*)\)(?P<tail>[ \t]*\{?)$"
)


def docstring_mask(lines: list[str]) -> list[bool]:
    """Mark the lines that belong to a docstring, `\"\"\"` delimiters included.

    Docstring text is prose: the line passes below must not treat a `;` in it
    as a comment, a trailing `:` as a label, or its blank lines as spacing.
    """
    mask: list[bool] = []
    inside = False
    for line in lines:
        delimiters = line.count('"""')
        mask.append(inside or delimiters > 0)
        if delimiters % 2:
            inside = not inside
    return mask


def comment_start(line: str) -> int:
    """Index of the `;` that starts a comment, or -1. A `;` inside a string literal is text."""
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == ";":
            return index
    return -1


def collapse_empty_lines(lines: list[str], options: FormattingOptions) -> list[str]:
    mask = docstring_mask(lines)
    if not options.preserve_empty_lines:
        return [line for line, in_doc in zip(lines, mask, strict=True) if in_doc or line.strip()]
    result: list[str] = []
    empty_count = 0
    for line, in_doc in zip(lines, mask, strict=True):
        if in_doc or line.strip():
            empty_count = 0
            result.append(line)
        else:
            empty_count += 1
            if empty_count <= options.max_empty_lines:
                result.append("")
    return result


def separate_labels(lines: list[str]) -> list[str]:
    """Insert a blank line before top-level labels (function entries
    / scope boundaries) for breathing room.

    Inside `{ ... }` blocks, labels render flush-left as section
    markers (`_loop:`, `_skip:`) and the author tends to pack them
    tightly with surrounding code, so don't force blanks there.
    Track brace depth across the line stream to distinguish.
    """
    adjusted: list[str] = []
    depth = 0
    for line, in_doc in zip(lines, docstring_mask(lines), strict=True):
        if in_doc:
            adjusted.append(line)
            continue
        semicolon = comment_start(line)
        code = (line if semicolon < 0 else line[:semicolon]).strip()
        # Only a bare `name:` line; comment text ending in `:` is prose.
        is_label = semicolon < 0 and code.endswith(":") and not code.startswith((":", "."))
        at_top_level = depth == 0
        if is_label and at_top_level and adjusted and adjusted[-1].strip():
            adjusted.append("")
        adjusted.append(line)
        depth += code.count("{") - code.count("}")
        depth = max(depth, 0)
    return adjusted


def _collect_inline_comment_groups(lines: list[str]) -> dict[int, list[tuple[int, str, str]]]:
    groups: dict[int, list[tuple[int, str, str]]] = {}
    for index, (line, in_doc) in enumerate(zip(lines, docstring_mask(lines), strict=True)):
        if in_doc or line.lstrip().startswith(";"):
            continue
        semicolon_index = comment_start(line)
        if semicolon_index <= 0:
            continue
        indent = len(line) - len(line.lstrip())
        code_part = line[indent:semicolon_index].rstrip()
        if not code_part:
            continue
        comment_part = line[semicolon_index + 1 :].strip()
        groups.setdefault(indent, []).append((index, code_part, comment_part))
    return groups


def align_inline_comments(lines: list[str], options: FormattingOptions) -> None:
    """Normalize inline comments without forcing column alignment.

    Default policy: `code  ; comment` with two spaces. Set
    `comment_alignment > 0` to force a target column for groups of
    same-indent comments.
    """
    groups = _collect_inline_comment_groups(lines)
    force_column = options.comment_alignment
    for indent, entries in groups.items():
        if not entries:
            continue
        target_column: int | None = None
        if force_column > 0:
            max_code_len = max(len(code_part) for _, code_part, _ in entries)
            target_column = max(indent + max_code_len + 1, force_column)
        for index, code_part, comment_part in entries:
            comment_text = f"; {comment_part}" if comment_part else ";"
            if target_column is None:
                lines[index] = f"{' ' * indent}{code_part}  {comment_text}"
            else:
                padding = max(target_column - (indent + len(code_part)), 1)
                lines[index] = f"{' ' * indent}{code_part}{' ' * padding}{comment_text}"


def wrap_long_paren_lines(lines: list[str], options: FormattingOptions) -> list[str]:
    """Wrap macro defs / applies whose single-line form exceeds max_line_length.

    Match shape `[indent]head(params)tail`. `head` may be prefixed with
    `.macro `; `tail` is empty (macro apply) or ` {` (macro def). Lines
    with embedded comments, nested parens, or no params are left alone.
    """
    limit = options.max_line_length
    out: list[str] = []
    for line, in_doc in zip(lines, docstring_mask(lines), strict=True):
        if in_doc or len(line) <= limit:
            out.append(line)
            continue
        match = _PAREN_WRAP_RE.match(line)
        if not match:
            out.append(line)
            continue
        params_raw = match.group("params").strip()
        if not params_raw:
            out.append(line)
            continue
        params = [p.strip() for p in params_raw.split(",") if p.strip()]
        if any(("(" in p or ")" in p) for p in params):
            out.append(line)
            continue
        indent = match.group("indent")
        head = match.group("head")
        tail = match.group("tail").strip()
        inner_indent = indent + " " * options.indent_size
        out.append(f"{indent}{head}(")
        for param in params:
            out.append(f"{inner_indent}{param},")
        closing = f"{indent}){' ' + tail if tail else ''}".rstrip()
        out.append(closing)
    return out


def strip_blanks_after_position_directive(lines: list[str]) -> list[str]:
    """Collapse blank lines immediately after `*=` / `@=` directives.

    Position directives sit tight with the data they place — blank
    lines between `*= 0xADDR` and the following `.incbin` / opcode
    read as noise rather than separation. The author can still put
    blank lines *before* the directive to break up sections.
    """
    out: list[str] = []
    skip_blanks = False
    for line in lines:
        stripped = line.lstrip()
        if skip_blanks and not stripped:
            continue
        out.append(line)
        skip_blanks = stripped.startswith(("*=", "@="))
    return out


def finalize_formatting(lines: list[str], options: FormattingOptions) -> str:
    """Strip trailing whitespace, collapse blanks, separate labels, align inline comments."""
    lines = [line.rstrip() for line in lines]
    lines = wrap_long_paren_lines(lines, options)
    lines = collapse_empty_lines(lines, options)
    lines = separate_labels(lines)
    # Position directives sit tight with their data; strip blanks
    # after `separate_labels` because that pass would otherwise
    # re-insert a blank between `*=` / `@=` and the next label.
    lines = strip_blanks_after_position_directive(lines)
    align_inline_comments(lines, options)
    # Trailing blank lines would survive one pass but not the next.
    content = "\n".join(lines).rstrip("\n")
    return f"{content}\n" if content else content
