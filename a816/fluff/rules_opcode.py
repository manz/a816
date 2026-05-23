"""Opcode rules: opcode-size suffix hygiene.

`OP001` flags an explicit `.w` / `.l` width suffix on an opcode whose
literal numeric operand already forces the assembler to pick that
same width. The suffix is noise; dropping it doesn't change the
emitted bytes.

`.b` is intentionally not flagged. Its meaning depends on the
runtime M/X register width (which fluff doesn't track), so a `.b`
on a small immediate may be the user's only way to force an
8-bit-form opcode under `.a16` / `rep #$20`.
"""

from __future__ import annotations

from collections.abc import Iterable

# Addressing modes whose width is purely a function of the operand
# value width — what we can safely reason about without simulating
# the resolver. Immediate is the obvious case; absolute / long share
# the same value-width inference.
from a816.cpu.types import AddressingMode
from a816.fluff.core import (
    Applicability,
    Diagnostic,
    Fix,
    LintContext,
    Rule,
    TextEdit,
    line_col_to_offset,
)
from a816.parse.ast.nodes import AstNode, OpcodeAstNode
from a816.parse.ast.nodes.base import Term

_WIDTH_DRIVEN_MODES = frozenset(
    {
        AddressingMode.immediate,
        AddressingMode.direct,
        AddressingMode.direct_indexed,
    }
)


def _literal_value(node: OpcodeAstNode) -> int | None:
    """Numeric value of `node`'s operand when it is a single literal,
    else None. Skips symbol refs, expressions, and casts since their
    width can shift between passes / link time."""
    if node.operand is None:
        return None
    tokens = node.operand.tokens
    if len(tokens) != 1 or not isinstance(tokens[0], Term):
        return None
    text = tokens[0].token.value
    try:
        return int(text, 0)
    except ValueError:
        return None


def _suffix_is_redundant(suffix: str, value: int) -> bool:
    # `.l` is redundant when the value already needs 3 bytes — no
    # shorter form can hold it, so inference also picks `.l`.
    # `.w` is redundant when the value needs ≥ 2 bytes for the same
    # reason; the shorter `.b` would truncate.
    if suffix == "l":
        return value > 0xFFFF
    if suffix == "w":
        return value > 0xFF
    return False


class RedundantOpcodeSizeSuffix(Rule):
    code = "OP001"
    description = "explicit `.w` / `.l` size suffix is redundant"
    rationale = (
        "When the literal operand already exceeds the next-smaller "
        "addressing form's range (`> 0xFF` for `.w`, `> 0xFFFF` for "
        "`.l`), the assembler picks that exact width from the value "
        "alone. Writing the suffix anyway adds visual noise and "
        "lies about why the width was chosen. `.b` is left alone "
        "because under `rep #$20` / `.a16` it's the only way to "
        "force an 8-bit immediate opcode."
    )
    bad = '"""Module."""\nlda.w #0x1234\n'
    good = '"""Module."""\nlda #0x1234\n'
    accepts = (OpcodeAstNode,)

    def visit(self, ctx: LintContext, node: AstNode) -> Iterable[Diagnostic]:
        assert isinstance(node, OpcodeAstNode)
        suffix = node.value_size or ""
        if suffix not in ("w", "l"):
            return
        if node.addressing_mode not in _WIDTH_DRIVEN_MODES:
            return
        value = _literal_value(node)
        if value is None:
            return
        if not _suffix_is_redundant(suffix, value):
            return
        fix = _strip_suffix_fix(ctx, node, suffix)
        yield self.diagnose(
            ctx,
            node,
            f"`{node.opcode}.{suffix}` is redundant — value 0x{value:X} forces width by itself",
            fix=fix,
        )


def _strip_suffix_fix(ctx: LintContext, node: OpcodeAstNode, suffix: str) -> Fix | None:
    # `file_info` is the opcode token (e.g. `lda`). The suffix sits
    # immediately after it as `.w` / `.l` — 2 source chars. Compute
    # its byte span and replace with empty.
    pos = node.file_info.position
    if pos is None:
        return None
    opcode_start = line_col_to_offset(ctx.text, pos.line + 1, pos.column + 1)
    # Find `.suffix` in source starting at the opcode position. The
    # opcode-name length is `len(node.opcode)`; the dot+suffix follows.
    dot_offset = opcode_start + len(node.opcode)
    if ctx.text[dot_offset : dot_offset + 2] != f".{suffix}":
        return None  # source diverges from AST shape; bail
    return Fix(
        edits=(TextEdit(start=dot_offset, end=dot_offset + 2, replacement=""),),
        applicability=Applicability.SAFE,
        description=f"remove redundant `.{suffix}` suffix",
    )
