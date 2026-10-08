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

import textwrap
from collections.abc import Iterable
from typing import TYPE_CHECKING

from a816.fluff.core import (
    Applicability,
    Diagnostic,
    Fix,
    LintContext,
    Rule,
    TextEdit,
    line_col_to_offset,
)
from a816.parse.ast.nodes import (
    AstNode,
    CodePositionAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
)
from a816.parse.ast.placement import is_placement_boundary
from a816.parse.ast.visitor import walk
from a816.parse.tokens import Token

if TYPE_CHECKING:
    from a816.incbin_names import PathName

# Run content UP001 leaves for a manual migration (see `_run_has_skip_trigger`).
_SKIP_TRIGGERS: tuple[type[AstNode], ...] = (ImportAstNode, IncludeAstNode, IncludeBinaryAstNode)


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
        "in an anonymous alloc. Runs holding `.import`, `.include` or "
        "`.incbin` are flagged without an autofix: migrate those by hand."
    )
    bad = '"""Module."""\n*= 0x008000\n.db 0xEA\n'
    good = '"""Module."""\n.alloc at 0x008000 {\n    .db 0xEA\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        nodes = ctx.nodes or []
        yield from _scan_for_star_eq(self, ctx, nodes, body_end=len(ctx.text))


def _scan_for_star_eq(rule: Rule, ctx: LintContext, nodes: list[AstNode], *, body_end: int) -> Iterable[Diagnostic]:
    """Walk a flat node sequence, emitting UP001 for each `*=`.

    Recurses into containers that hold their own placement runs
    (`.scope`, `.macro`, `.if`, `.for`, bare `{}`, `.alloc`, `.include`)
    so a `*=` nested inside is flagged too. `body_end` is the offset
    where the parent body's closing `}` sits — the autofix clamps the
    `*=`-body span to that offset so it never swallows the brace and
    bricks the enclosing block.
    """
    from a816.parse.ast.nodes import (
        AllocAstNode,
        CompoundAstNode,
        ForAstNode,
        IfAstNode,
        MacroAstNode,
        ScopeAstNode,
    )

    for idx, node in enumerate(nodes):
        if isinstance(node, CodePositionAstNode):
            yield _emit_up001(rule, ctx, nodes, idx, body_end=body_end)
            continue
        if isinstance(node, IfAstNode):
            # IfAstNode has TWO brace pairs (true + else); compute each
            # branch's end independently rather than via the generic
            # single-brace helper.
            if_end, else_end = _if_branch_ends(ctx.text, node, body_end)
            yield from _scan_for_star_eq(rule, ctx, list(node.block.body), body_end=if_end)
            if node.else_block is not None:
                yield from _scan_for_star_eq(rule, ctx, list(node.else_block.body), body_end=else_end)
            continue
        child_body_end = _container_body_end(ctx.text, node, body_end)
        if isinstance(node, ScopeAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.body.body), body_end=child_body_end)
        elif isinstance(node, MacroAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.block.body), body_end=child_body_end)
        elif isinstance(node, ForAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.body.body), body_end=child_body_end)
        elif isinstance(node, CompoundAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.body), body_end=child_body_end)
        elif isinstance(node, AllocAstNode):
            yield from _scan_for_star_eq(rule, ctx, list(node.body.body), body_end=child_body_end)
        # Skip `.include`d AST: `included_nodes` carries positions from
        # the included file, but `_build_star_eq_to_alloc_fix` rewrites
        # `ctx.text` (the importer's text) at those offsets — garbage.
        # The included file is linted on its own pass when fluff walks
        # the directory; rewrites land in the correct file there.


def _match_brace(text: str, start: int, limit: int) -> int:
    """Starting at or before a `{`, advance to the matching `}` and
    return its offset. Returns `limit` when unmatched."""
    cursor = start
    depth = 0
    started = False
    while cursor < limit:
        ch = text[cursor]
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}" and started:
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return limit


def _if_branch_ends(text: str, node: IfAstNode, fallback: int) -> tuple[int, int]:
    """Brace-match the `.if` body and optional `else` body separately
    so each branch's `*=` autofix clamps to its own closing `}`."""
    pos = getattr(node.file_info, "position", None)
    if pos is None:
        return fallback, fallback
    cursor = line_col_to_offset(text, pos.line + 1, 1)
    if_end = _match_brace(text, cursor, fallback)
    else_end = fallback
    if node.else_block is not None and if_end < fallback:
        else_end = _match_brace(text, if_end + 1, fallback)
    return if_end, else_end


def _container_body_end(text: str, node: AstNode, fallback: int) -> int:
    """Brace-match forward from the container's start to find its
    closing `}` offset. Returns `fallback` when the container isn't
    brace-delimited (`.include`, root) or matching fails."""
    pos = getattr(node.file_info, "position", None)
    if pos is None:
        return fallback
    cursor = line_col_to_offset(text, pos.line + 1, 1)
    depth = 0
    started = False
    while cursor < fallback:
        ch = text[cursor]
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}" and started:
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return fallback


def _emit_up001(rule: Rule, ctx: LintContext, siblings: list[AstNode], idx: int, *, body_end: int) -> Diagnostic:
    star_eq = siblings[idx]
    assert isinstance(star_eq, CodePositionAstNode)
    addr_text = star_eq.expression.to_canonical()
    message = f"replace `*= {addr_text}` with `.alloc at {addr_text} {{ ... }}`"
    if _run_has_skip_trigger(siblings, idx):
        return rule.diagnose(ctx, star_eq, f"{message}; migrate by hand: the run holds `.import`/`.include`/`.incbin`")
    return rule.diagnose(
        ctx,
        star_eq,
        message,
        fix=_build_star_eq_to_alloc_fix(ctx.text, siblings, idx, addr_text, body_end),
    )


def _run_has_skip_trigger(siblings: list[AstNode], idx: int) -> bool:
    """Does the `*=` run after `siblings[idx]` hold `.import` / `.include` / `.incbin`?

    The run ends at the next placement boundary, same as the autofix
    span. Those directives don't wrap mechanically: `.import` belongs in
    the file prelude (the assembler rejects it inside `.alloc`),
    `.include` may carry its own placement, and `.incbin` may rely on
    the legacy silent bank wrap that `.alloc at` turns into an error.
    """
    for sibling in siblings[idx + 1 :]:
        if is_placement_boundary(sibling):
            return False
        if any(isinstance(node, _SKIP_TRIGGERS) for node in walk([sibling])):
            return True
    return False


def _build_star_eq_to_alloc_fix(
    text: str,
    siblings: list[AstNode],
    idx: int,
    addr_text: str,
    body_end: int,
) -> Fix | None:
    """Wrap `*=ADDR` and its body run in `.alloc at ADDR { ... }`.

    Body run = nodes from `siblings[idx + 1]` up to (but not
    including) the next `CodePositionAstNode` at this level, or end
    of `siblings`. The wrapper takes the `*=` line's indent; the body
    keeps its relative layout, dedented then re-indented one level
    deeper than the wrapper (an already-indented body is not pushed
    a second level in).

    Returns None when source positions can't be resolved (defensive).
    """
    star_eq = siblings[idx]
    pos = getattr(star_eq.file_info, "position", None)
    if pos is None:
        return None
    start = line_col_to_offset(text, pos.line + 1, 1)
    end = min(_next_placement_or_end(text, siblings, idx), body_end)
    if end <= start:
        return None
    snippet = text[start:end]
    base = snippet[: len(snippet) - len(snippet.lstrip(" \t"))]
    body_source = textwrap.dedent(_extract_body_source(snippet))
    body_indented = _indent_block(body_source, len(base) + 4)
    replacement = f"{base}.alloc at {addr_text} {{\n{body_indented}\n{base}}}\n"
    return Fix(
        edits=(TextEdit(start=start, end=end, replacement=replacement),),
        applicability=Applicability.UNSAFE,
        description=f"wrap `*= {addr_text}` body in `.alloc at {addr_text} {{ ... }}`",
    )


def _next_placement_or_end(text: str, siblings: list[AstNode], idx: int) -> int:
    """End offset for the body run starting after `siblings[idx]`.

    Stops at the next placement directive at the same level — either
    another `*=` (`CodePositionAstNode`) or a `.alloc … at/in …`
    (`AllocAstNode`). Both open their own placement context, so the
    wrap-into-`.alloc at` for the leading `*=` must end before them
    rather than swallowing them whole. Returns end of `text` if no
    such boundary follows."""
    for j in range(idx + 1, len(siblings)):
        next_node = siblings[j]
        if is_placement_boundary(next_node):
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


class IncbinPathName(Rule):
    """W0001, the build's warning, as a lint with the rewrite as its fix."""

    code = "W0001"
    description = "reference to a name `.incbin` derived from its file path"
    rationale = (
        '`.incbin "assets/vwf.bin"` binds `assets_vwf_bin` and `assets_vwf_bin__size`, '
        "named after the asset's path, so moving the file renames symbols in code that "
        "never mentions it. Name the blob instead: alone in `.alloc vwf_font { ... }` it "
        "is `vwf_font` and its size `sizeof(vwf_font)`, which the fix writes. A blob "
        "sharing its block needs a label. The path names go in 1.2."
    )
    bad = '"""Module."""\n.alloc font at 0x008000 {\n    .incbin "f.bin"\n}\n.dl f_bin\n'
    good = '"""Module."""\n.alloc font at 0x008000 {\n    .incbin "f.bin"\n}\n.dl font\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        from a816.incbin_names import path_names, references

        assert ctx.nodes is not None
        names = {**_imported_path_names(ctx), **path_names(ctx.nodes)}
        for token, name in references(ctx.nodes, names):
            position = token.position
            assert position is not None
            yield Diagnostic(
                path=ctx.path,
                line=position.line + 1,
                column=position.column + 1,
                code=self.code,
                message=f"`{name.name}` is named after the asset path {name.file_path!r}; {name.hint()}",
                fix=_rename_fix(ctx, token, name.replacement),
            )


def _imported_path_names(ctx: LintContext) -> dict[str, PathName]:
    """Path names the file's direct imports bind, parsed with its include paths."""
    from a816.fluff.rules_style import _resolve_import_for_lint
    from a816.incbin_names import imported_path_names
    from a816.parse.mzparser import A816Parser

    names: dict[str, PathName] = {}
    for node in ctx.flat_nodes:
        if not isinstance(node, ImportAstNode):
            continue
        source = _resolve_import_for_lint(node.module_name, ctx)
        if source is None:
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = A816Parser.parse_as_ast(text, str(source), include_paths=ctx.include_paths_for_lookup)
        names.update(imported_path_names(parsed.nodes))
    return names


def _rename_fix(ctx: LintContext, token: Token, replacement: str | None) -> Fix | None:
    if replacement is None or token.position is None:
        return None
    start = line_col_to_offset(ctx.text, token.position.line + 1, token.position.column + 1)
    end = start + len(token.value)
    if ctx.text[start:end] != token.value:
        return None  # source diverges from the token; leave it
    return Fix(
        edits=(TextEdit(start=start, end=end, replacement=replacement),),
        applicability=Applicability.SAFE,
        description=f"rewrite `{token.value}` as `{replacement}`",
    )
