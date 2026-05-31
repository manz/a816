"""Structural rules: enforce well-formed section / alloc nesting.

`ST001` flags placement directives (`*=`, `.alloc`, `.relocate`, `@=`)
that sit inside an `.alloc` body. The codegen raises on these — the
lint surfaces them before the assembler runs and points at the inner
directive directly, with a hint to hoist it out as a sibling of the
outer alloc.
"""

from __future__ import annotations

from collections.abc import Iterable

from a816.fluff.core import Diagnostic, LintContext, Rule
from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    CodePositionAstNode,
    RelocateAstNode,
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
