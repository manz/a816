"""Code-mask helpers.

Build a per-character True/False mask marking which bytes of a source
file are real code vs string/comment payload. Used by the LSP server's
reference scanner to skip identifier matches that fall inside a string,
triple-quoted docstring, C-style block comment, or `;` line comment —
those bytes are not source identifiers and shouldn't count as references.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _MaskState:
    """Cross-line scanner state for `build_code_mask`. The
    triple-quoted-string flag and the C-style block-comment flag both
    survive a newline; single-quoted strings don't span lines in a816
    syntax.
    """

    in_triple: str | None = None
    in_block_comment: bool = False


def _consume_block_comment(raw: str, i: int, mask: list[bool], state: _MaskState) -> int:
    """Mask bytes inside a `/* ... */` block comment. Closes the block
    on `*/`. Returns the new cursor position.
    """
    mask[i] = False
    if i + 1 < len(raw) and raw[i] == "*" and raw[i + 1] == "/":
        mask[i + 1] = False
        state.in_block_comment = False
        return i + 2
    return i + 1


def _consume_triple(raw: str, i: int, mask: list[bool], state: _MaskState) -> int:
    """Mask one byte of a triple-quoted string, closing it on the
    matching delimiter. Returns the new cursor position.
    """
    mask[i] = False
    if state.in_triple is not None and raw[i : i + 3] == state.in_triple:
        mask[i + 1] = False
        mask[i + 2] = False
        state.in_triple = None
        return i + 3
    return i + 1


def _consume_string(raw: str, i: int, mask: list[bool], in_string: str) -> tuple[int, str | None]:
    """Mask one byte of a single-line string, handling backslash escapes
    and the closing delimiter. Returns (new cursor, new state).
    """
    mask[i] = False
    if raw[i] == "\\" and i + 1 < len(raw):
        mask[i + 1] = False
        return i + 2, in_string
    if raw[i] == in_string:
        return i + 1, None
    return i + 1, in_string


def _consume_line_comment(raw: str, i: int, mask: list[bool]) -> int:
    """Mask the rest of the line after a `;`. Returns a sentinel position
    past the line end so `_mask_line`'s loop terminates.
    """
    for j in range(i, len(raw)):
        mask[j] = False
    return len(raw)


def _open_region(raw: str, i: int, mask: list[bool], state: _MaskState) -> tuple[int, str | None]:
    """Examine the current character and open a comment/string region
    if one starts here. Returns `(new_i, in_string_delim)` where the
    delimiter is non-None when a single-line string just opened.
    """
    ch = raw[i]
    if ch == ";":
        return _consume_line_comment(raw, i, mask), None
    if raw[i : i + 2] == "/*":
        state.in_block_comment = True
        mask[i] = mask[i + 1] = False
        return i + 2, None
    if raw[i : i + 3] in ('"""', "'''"):
        state.in_triple = raw[i : i + 3]
        mask[i] = mask[i + 1] = mask[i + 2] = False
        return i + 3, None
    if ch in ('"', "'"):
        mask[i] = False
        return i + 1, ch
    return i + 1, None


def _mask_line(raw: str, state: _MaskState) -> list[bool]:
    """Build the code-mask for a single line, advancing `state` for
    triple-quoted strings and `/* ... */` block comments that may span
    multiple lines.
    """
    mask = [True] * len(raw)
    i = 0
    in_string: str | None = None
    while i < len(raw):
        if state.in_block_comment:
            i = _consume_block_comment(raw, i, mask, state)
            continue
        if state.in_triple is not None:
            i = _consume_triple(raw, i, mask, state)
            continue
        if in_string is not None:
            i, in_string = _consume_string(raw, i, mask, in_string)
            continue
        i, in_string = _open_region(raw, i, mask, state)
    return mask


def build_code_mask(lines: list[str]) -> list[list[bool]]:
    """Build per-line code masks across a whole document. State (triple-
    quoted strings, block comments) carries across line boundaries.
    """
    state = _MaskState()
    masks: list[list[bool]] = []
    for raw in lines:
        masks.append(_mask_line(raw, state))
    return masks
