"""Structural rules: enforce well-formed section / alloc nesting.

`ST001` flags placement directives (`*=`, `.alloc`, `.relocate`, `@=`)
that sit inside an `.alloc` body. The codegen raises on these — the
lint surfaces them before the assembler runs and points at the inner
directive directly, with a hint to hoist it out as a sibling of the
outer alloc.

`ST002` flags `.import` outside the file prelude. Modules own their
placement, so an import never belongs to a placement context: it sits
at the top of the file, before the first code / data / placement
statement.
"""

from __future__ import annotations

from collections.abc import Iterable

from a816.fluff.core import Diagnostic, LintContext, Rule
from a816.parse.ast.nodes import (
    AllocAstNode,
    AssignAstNode,
    AstNode,
    CodePositionAstNode,
    CommentAstNode,
    DocstringAstNode,
    ExternAstNode,
    ImportAstNode,
    IncludeAstNode,
    LabelDeclAstNode,
    MapAstNode,
    RelocateAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
)
from a816.parse.ast.visitor import walk

_NESTED_KIND: dict[type[AstNode], str] = {
    AllocAstNode: ".alloc",
    RelocateAstNode: ".relocate",
    CodePositionAstNode: "`*=`",
}


class NestedPlacementInAlloc(Rule):
    code = "ST001"
    description = "placement directive nested inside `.alloc` body"
    rationale = (
        "An `.alloc` body owns its placement context end-to-end. A "
        "nested `*= ADDR` / `.alloc ... at` / `.relocate` re-anchors "
        "the PC inside that body and silently corrupts layout — bytes "
        "before the inner directive still emit at the outer base, "
        "bytes after emit at the inner one, and the outer alloc's "
        "bounds check is computed off the wrong end address. Hoist "
        "the inner directive out as a sibling of the outer `.alloc` "
        "so each region carries its own placement."
    )
    bad = '"""Module."""\n.alloc outer at 0x008000 {\n    .db 0xEA\n    *= 0x009000\n    .db 0x01\n}\n'
    good = '"""Module."""\n.alloc outer at 0x008000 {\n    .db 0xEA\n}\n.alloc at 0x009000 {\n    .db 0x01\n}\n'
    accepts = (AllocAstNode,)

    def visit(self, ctx: LintContext, node: AstNode) -> Iterable[Diagnostic]:
        assert isinstance(node, AllocAstNode)
        outer = node.name or "<anonymous>"
        for child in walk(node.body.body):
            kind = _NESTED_KIND.get(type(child))
            if kind is None:
                continue
            yield self.diagnose(
                ctx,
                child,
                f"nested placement {kind} inside `.alloc {outer}`; hoist it out as a sibling of the alloc",
            )


# Top-level statements that may precede an `.import`: documentation,
# other dependency directives, constant declarations, and file-level
# configuration (`.table`, `.map`). None emits bytes or opens a placement.
_PRELUDE_TYPES: tuple[type[AstNode], ...] = (
    DocstringAstNode,
    CommentAstNode,
    ImportAstNode,
    IncludeAstNode,
    ExternAstNode,
    SymbolAffectationAstNode,
    AssignAstNode,
    LabelDeclAstNode,
    TableAstNode,
    MapAstNode,
)


class ImportOutsidePrelude(Rule):
    code = "ST002"
    description = "`.import` outside the file prelude"
    rationale = (
        "The prelude is the leading run of top-level docstrings, "
        "comments, `.import`, `.include`, `.extern`, constant "
        "declarations (`NAME = x`, `NAME := x`, `.label NAME = x`) and "
        "file-level configuration (`.table`, `.map`). The first other "
        "statement ends it. An `.import` further down reads "
        "as if the module landed at that point; it never does, because "
        "modules own their placement (`.alloc at` / `.alloc in POOL`). "
        "After `*=` or inside an `.alloc` body the assembler rejects it "
        "outright; elsewhere it only misleads. Move it to the top."
    )
    bad = '"""Module."""\n.alloc at 0x008000 {\n    nop\n}\n.import "@std/snes/ppu"\n'
    good = '"""Module."""\n.import "@std/snes/ppu"\n.alloc at 0x008000 {\n    nop\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        nodes = ctx.nodes or []
        end = _prelude_end(nodes)
        if end == len(nodes):
            return
        ended_at = nodes[end].file_info.position
        where = f" (it ends at line {ended_at.line + 1})" if ended_at is not None else ""
        for node in walk(nodes[end:], into_includes=False):
            if isinstance(node, ImportAstNode):
                yield self.diagnose(ctx, node, f"`.import` outside the file prelude{where}; move it to the top")


def _prelude_end(nodes: list[AstNode]) -> int:
    """Index of the first top-level statement that is not prelude material."""
    for idx, node in enumerate(nodes):
        if not isinstance(node, _PRELUDE_TYPES):
            return idx
    return len(nodes)
