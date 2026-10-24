"""Opcode rules: opcode-size suffix hygiene.

`OP001` flags an explicit `.w` / `.l` width suffix on an opcode whose
literal address operand already forces the assembler to pick that
same width. The suffix is noise; dropping it doesn't change the
emitted bytes.

`.b` is intentionally not flagged, and neither are immediates. An
immediate's width is the M/X register width at run time, which fluff
doesn't track: `lda.w #0x1234` states the 16-bit accumulator the code
expects, and `.b` may be the only way to force an 8-bit-form opcode
under `.a16` / `rep #$20`.
"""

from __future__ import annotations

from collections.abc import Iterable

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

# Addressing modes whose width is purely a function of the operand
# value width, which we can reason about without simulating the
# resolver. Immediate is left out: its width follows M/X.
_WIDTH_DRIVEN_MODES = frozenset(
    {
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


# A bare 24-bit `jmp` / `jsr` target in the caller's bank takes the
# absolute form (and another bank's is an error), so their `.l` always
# picks JML / JSL where inference would not.
_TRANSFERS = frozenset({"jmp", "jsr"})


def _suffix_is_redundant(suffix: str, value: int, opcode: str = "") -> bool:
    """Inference alone picks `suffix` for `value`.

    `.l` when the value needs 3 bytes. `.w` when it needs exactly 2:
    above 0xFFFF, `.w` truncates (`jsr.w 0xC006AC` is an in-bank call)
    and dropping it widens the operand."""
    if suffix == "l":
        return value > 0xFFFF and opcode not in _TRANSFERS
    if suffix == "w":
        return 0xFF < value <= 0xFFFF
    return False


class RedundantOpcodeSizeSuffix(Rule):
    code = "OP001"
    description = "explicit `.w` / `.l` size suffix is redundant"
    rationale = (
        "When the literal address needs exactly that width (`0x100` to "
        "`0xFFFF` for `.w`, above `0xFFFF` for `.l`), the assembler picks "
        "it from the value "
        "alone. Writing the suffix anyway adds visual noise and "
        "lies about why the width was chosen. Immediates are left alone "
        "because their width is the M/X register width, which the suffix "
        "documents; `.b` because under `rep #$20` / `.a16` it's the only way "
        "to force an 8-bit immediate opcode; and `jmp.l` / `jsr.l` because "
        "a bare 24-bit target in the caller's bank is a 16-bit jump."
    )
    bad = '"""Module."""\nlda.w 0x1234\n'
    good = '"""Module."""\nlda 0x1234\n'
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
        if not _suffix_is_redundant(suffix, value, node.opcode):
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
