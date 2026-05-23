"""Migration rules: lift legacy syntax to its modern equivalent.

`UP001` deprecates the bare `*= ADDR` placement directive in favour
of the explicit `.alloc at ADDR { ... }` block. Behaviour intent is
identical for the common case; the legacy form silently allowed
emission to cross bank boundaries, the new form surfaces that as a
build error pointing at the offending byte. Marked unsafe because of
that one semantic shift: source that secretly relied on bank wrap
will now fail to assemble after the fix.
"""

from __future__ import annotations

from collections.abc import Iterable

from a816.fluff.core import (
    Applicability,
    Diagnostic,
    Fix,
    LintContext,
    Rule,
    TextEdit,
    line_col_to_offset,
)
from a816.parse.ast.nodes import AstNode, CodePositionAstNode


class StarEqualToAllocAt(Rule):
    code = "UP001"
    description = "legacy `*= ADDR` should be `.alloc at ADDR { ... }`"
    rationale = (
        "`*= ADDR` opens an unbounded pinned section whose body runs "
        "until the next placement directive. `.alloc at ADDR { ... }` "
        "is the same thing with explicit body delimiters, plus a "
        "hard error on cross-bank overflow instead of the silent wrap "
        "the legacy form allows. Migration is mechanical; the autofix "
        "wraps the run between `*=` and the next placement boundary "
        "in an anonymous alloc."
    )
    bad = '"""Module."""\n*= 0x008000\n.db 0xEA\n'
    good = '"""Module."""\n.alloc at 0x008000 {\n    .db 0xEA\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        nodes = ctx.nodes or []
        yield from _scan_for_star_eq(self, ctx, nodes)


def _scan_for_star_eq(rule: Rule, ctx: LintContext, nodes: list[AstNode]) -> Iterable[Diagnostic]:
    """Walk a flat node sequence, emitting UP001 for each `*=`.

    Recurses into containers that hold their own placement runs
    (`.scope`, `.macro`, `.if`, `.for`) so a `*=` nested inside is
    flagged too. Each emitted diagnostic carries a fix that wraps
    `*=ADDR` plus its body run in `.alloc at ADDR { ... }`.
    """
    from a816.parse.ast.nodes import (
        ForAstNode,
        IfAstNode,
        MacroAstNode,
        ScopeAstNode,
    )

    for idx, node in enumerate(nodes):
        if isinstance(node, CodePositionAstNode):
            yield _emit_up001(rule, ctx, nodes, idx)
        elif isinstance(node, ScopeAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.body.body))
        elif isinstance(node, MacroAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.block.body))
        elif isinstance(node, IfAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.block.body))
            if node.else_block is not None:
                yield from _scan_for_star_eq(rule, ctx, list(node.else_block.body))
        elif isinstance(node, ForAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.body.body))


def _emit_up001(rule: Rule, ctx: LintContext, siblings: list[AstNode], idx: int) -> Diagnostic:
    star_eq = siblings[idx]
    assert isinstance(star_eq, CodePositionAstNode)
    addr_text = star_eq.expression.to_canonical()
    return rule.diagnose(
        ctx,
        star_eq,
        f"replace `*= {addr_text}` with `.alloc at {addr_text} {{ ... }}`",
        fix=_build_star_eq_to_alloc_fix(ctx.text, siblings, idx, addr_text),
    )


def _build_star_eq_to_alloc_fix(
    text: str,
    siblings: list[AstNode],
    idx: int,
    addr_text: str,
) -> Fix | None:
    """Wrap `*=ADDR` and its body run in `.alloc at ADDR { ... }`.

    Body run = nodes from `siblings[idx + 1]` up to (but not
    including) the next `CodePositionAstNode` at this level, or end
    of `siblings`. Original source bytes for the body are preserved
    verbatim and indented by 4 spaces inside the new braces.

    Returns None when source positions can't be resolved (defensive).
    """
    star_eq = siblings[idx]
    pos = getattr(star_eq.file_info, "position", None)
    if pos is None:
        return None
    start = line_col_to_offset(text, pos.line + 1, 1)
    end = _next_placement_or_end(text, siblings, idx)
    if end <= start:
        return None
    snippet = text[start:end]
    body_source = _extract_body_source(snippet)
    body_indented = _indent_block(body_source, 4)
    replacement = f".alloc at {addr_text} {{\n{body_indented}\n}}\n"
    return Fix(
        edits=(TextEdit(start=start, end=end, replacement=replacement),),
        applicability=Applicability.UNSAFE,
        description=f"wrap `*= {addr_text}` body in `.alloc at {addr_text} {{ ... }}`",
    )


def _next_placement_or_end(text: str, siblings: list[AstNode], idx: int) -> int:
    """End offset for the body run starting after `siblings[idx]`.

    Stops at the next `CodePositionAstNode` at the same level, or at
    end of `text` if none follows. Includes the trailing newline so
    the run leaves no blank line behind it."""
    for j in range(idx + 1, len(siblings)):
        next_node = siblings[j]
        if isinstance(next_node, CodePositionAstNode):
            next_pos = getattr(next_node.file_info, "position", None)
            if next_pos is None:
                continue
            return line_col_to_offset(text, next_pos.line + 1, 1)
    return len(text)


def _extract_body_source(snippet: str) -> str:
    """Drop the leading `*=ADDR` line from a captured run, returning
    the body lines that follow. Trims trailing newlines so the
    wrapper's closing `}` lands on a fresh line without extra blanks."""
    lines = snippet.split("\n")
    if not lines:
        return ""
    body = lines[1:]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


def _indent_block(text: str, spaces: int) -> str:
    """Prefix every non-blank line with `spaces` spaces, leaving blank
    lines blank so the rewrap doesn't introduce trailing whitespace."""
    pad = " " * spaces
    return "\n".join(f"{pad}{line}" if line.strip() else "" for line in text.split("\n"))
