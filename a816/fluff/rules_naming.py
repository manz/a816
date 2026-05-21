"""Naming rules: N801 (labels) / N802 (constants)."""

from __future__ import annotations

from collections.abc import Iterable

from a816.fluff.core import (
    Diagnostic,
    LintContext,
    Rule,
    is_screaming_snake_case,
    is_snake_case,
)
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    LabelAstNode,
    SymbolAffectationAstNode,
)


class LabelNaming(Rule):
    code = "N801"
    description = "label name should be snake_case"
    rationale = (
        "Labels are snake_case (`reset_counter`, `_loop`). Mixed case "
        "and SCREAMING_SNAKE are reserved for constants (see N802)."
    )
    bad = '"""Module."""\nMyLabel:\n    rts\n'
    good = '"""Module."""\nmy_label:\n    rts\n'
    accepts = (LabelAstNode,)

    def visit(self, ctx: LintContext, node: AstNode) -> Iterable[Diagnostic]:
        assert isinstance(node, LabelAstNode)
        name = node.label
        if not name or is_snake_case(name):
            return
        yield self.diagnose(ctx, node, f"label '{name}' should be snake_case")


class ConstantNaming(Rule):
    code = "N802"
    description = "constant name should be snake_case or SCREAMING_SNAKE_CASE"
    rationale = (
        "Constants accept either snake_case or SCREAMING_SNAKE_CASE — "
        "use SCREAMING for tunables / feature flags, snake_case for "
        "computed offsets and addresses. Anything else fails the lint."
    )
    bad = '"""Module."""\nMixedThing = 0x10\n'
    good = '"""Module."""\nfoo_bar = 0x10\nMAX_HP = 0xFF\n'
    accepts = (AssignAstNode, SymbolAffectationAstNode)

    def visit(self, ctx: LintContext, node: AstNode) -> Iterable[Diagnostic]:
        assert isinstance(node, AssignAstNode | SymbolAffectationAstNode)
        name = node.symbol
        if not name or is_snake_case(name) or is_screaming_snake_case(name):
            return
        yield self.diagnose(ctx, node, f"constant '{name}' should be snake_case or SCREAMING_SNAKE_CASE")
