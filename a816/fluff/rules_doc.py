"""Docstring rules: DOC001 / DOC002 / DOC003 / DOC004 / DOC005 / DOC006 / DOC007."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from a816.fluff.core import (
    Applicability,
    Diagnostic,
    Fix,
    LintContext,
    Rule,
    TextEdit,
    _block_brace_offset,
    _detect_body_indent,
    kind_label,
    label_has_below_docstring,
    line_col_to_offset,
    public_target_name,
    target_name,
)
from a816.parse.ast.nodes import (
    AstNode,
    CommentAstNode,
    DocstringAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroAstNode,
    ScopeAstNode,
)


class MissingModuleDocstring(Rule):
    code = "DOC001"
    description = "module is missing a leading docstring"
    rationale = (
        "Every source file should open with a docstring describing what "
        "the module is for. Tooling (LSP hover on `.import` targets, "
        "documentation generators) reads this leading docstring to "
        "summarise the module without forcing a reader to chase the "
        "first comment block."
    )
    bad = "main:\n    rts\n"
    good = '"""Top-level patch entry."""\nmain:\n    rts\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        for node in ctx.nodes or []:
            if isinstance(node, CommentAstNode):
                continue
            if isinstance(node, DocstringAstNode):
                return
            yield Diagnostic(
                path=ctx.path,
                line=1,
                column=1,
                code=self.code,
                message=self.description,
            )
            return


class MissingTargetDocstring(Rule):
    code = "DOC002"
    description = "public macro/scope/label is missing a docstring"
    rationale = (
        "Public targets (anything not prefixed with `_`) cross module "
        "boundaries. Without a docstring the LSP has nothing to surface "
        "on hover and downstream callers have to read the body to learn "
        "what they're calling."
    )
    bad = '"""Module."""\n.macro setup_counter() {\n    ldx.w #0\n}\n'
    good = '"""Module."""\n.macro setup_counter() {\n    """Reset the loop counter to zero."""\n    ldx.w #0\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        nodes = ctx.expanded_top_level
        pending_doc_above = False
        module_doc_consumed = False
        for idx, node in enumerate(nodes):
            if isinstance(node, CommentAstNode):
                continue
            if isinstance(node, DocstringAstNode):
                if module_doc_consumed:
                    pending_doc_above = True
                else:
                    module_doc_consumed = True
                continue
            module_doc_consumed = True
            name = public_target_name(node)
            if name is not None and self._target_undocumented(nodes, idx, node, pending_doc_above):
                yield self.diagnose(ctx, node, f"public {kind_label(node)} '{name}' is missing a docstring")
            pending_doc_above = False

    @staticmethod
    def _target_undocumented(nodes: list[AstNode], idx: int, node: AstNode, pending_doc_above: bool) -> bool:
        if isinstance(node, LabelAstNode):
            return not label_has_below_docstring(nodes, idx)
        if isinstance(node, LabelDeclAstNode):
            return not pending_doc_above
        return not pending_doc_above and not getattr(node, "docstring", None)


@dataclass
class _MisplacementState:
    pending_doc: DocstringAstNode | None = None
    module_doc_consumed: bool = False
    last_was_label: bool = False


class MisplacedDocstring(Rule):
    code = "DOC003"
    description = "docstring above macro/scope should live inside the body"
    rationale = (
        "For block-bodied targets (`.macro`, `.scope`) the canonical "
        "attach point is the first statement inside the body. A "
        "docstring above the opening line is parsed as a free-floating "
        "string and isn't picked up by tooling that walks the body for "
        "documentation. Labels are flat — their docstring sits as the "
        "first statement *below* the label, like a Python function-body "
        "docstring."
    )
    bad = '"""Module."""\n"""Setup loop counter."""\n.macro setup_counter() {\n    ldx.w #0\n}\n'
    good = '"""Module."""\n.macro setup_counter() {\n    """Setup loop counter."""\n    ldx.w #0\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        state = _MisplacementState()
        for node in ctx.expanded_top_level:
            if isinstance(node, CommentAstNode):
                continue
            if isinstance(node, DocstringAstNode):
                self._track_docstring(state, node)
                continue
            state.module_doc_consumed = True
            if state.pending_doc is not None and not isinstance(node, LabelDeclAstNode):
                hit = self._diagnostic_for(ctx, state.pending_doc, node)
                if hit is not None:
                    yield hit
            state.pending_doc = None
            state.last_was_label = isinstance(node, LabelAstNode)

    @staticmethod
    def _track_docstring(state: _MisplacementState, node: DocstringAstNode) -> None:
        """Module-leading → consume; below-label → consume; otherwise → pending above."""
        if not state.module_doc_consumed:
            state.module_doc_consumed = True
        elif not state.last_was_label:
            state.pending_doc = node
        state.last_was_label = False

    def _diagnostic_for(self, ctx: LintContext, doc: DocstringAstNode, target: AstNode) -> Diagnostic | None:
        name = target_name(target)
        if name is None:
            return None
        if isinstance(target, MacroAstNode | ScopeAstNode):
            return self.diagnose(
                ctx,
                doc,
                f"docstring above {kind_label(target)} '{name}' should "
                "be moved inside the body (first statement after `{`)",
                fix=_build_move_docstring_into_body_fix(ctx.text, doc, target),
            )
        if isinstance(target, LabelAstNode):
            return self.diagnose(
                ctx,
                doc,
                f"docstring above label '{name}' should sit below it (first statement after the colon)",
            )
        return None


def _build_docstring_alignment_fix(
    text: str,
    doc: DocstringAstNode,
    open_col: int,
    current_indent: int,
) -> Fix | None:
    """DOC007: shift every non-blank body line of `doc` so its
    leading whitespace lands at `open_col`. Preserves relative
    indentation between lines (a deeper line stays deeper than its
    siblings) and leaves blank lines untouched.

    Safe: only whitespace inside the docstring's content lines
    changes; the prose and the surrounding code are byte-identical.
    """
    token = doc.file_info
    pos = getattr(token, "position", None)
    end_pos = token.end_position if pos is not None else None
    if pos is None or end_pos is None:
        return None
    start = line_col_to_offset(text, pos.line + 1, pos.column + 1)
    end = line_col_to_offset(text, end_pos.line + 1, end_pos.column + 1)
    snippet = text[start:end]
    delta = open_col - current_indent
    new_snippet = _reindent_docstring_body(snippet, delta)
    if new_snippet == snippet:
        return None
    return Fix(
        edits=(TextEdit(start=start, end=end, replacement=new_snippet),),
        applicability=Applicability.SAFE,
        description=f'align docstring body to column {open_col + 1}',
    )


def _reindent_docstring_body(snippet: str, delta: int) -> str:
    """Adjust every non-blank line (except the first, which sits next
    to the opening `\"\"\"`) by `delta` spaces. Negative `delta`
    dedents; positive indents. Lines whose existing indent is less
    than `abs(delta)` when dedenting clamp to column 0."""
    if delta == 0:
        return snippet
    lines = snippet.split("\n")
    if len(lines) <= 1:
        return snippet
    rewritten = [lines[0]]
    for line in lines[1:]:
        if not line.strip():
            rewritten.append(line)
            continue
        stripped = line.lstrip(" ")
        existing = len(line) - len(stripped)
        new_indent = max(existing + delta, 0)
        rewritten.append(" " * new_indent + stripped)
    return "\n".join(rewritten)


def _docstring_span(text: str, doc: DocstringAstNode) -> tuple[int, int] | None:
    """Byte range covering a docstring's `\"\"\"...\"\"\"` token plus
    the trailing newline so removing it doesn't leave a blank line."""
    token = doc.file_info
    pos = getattr(token, "position", None)
    end_pos = token.end_position if pos is not None else None
    if pos is None or end_pos is None:
        return None
    start = line_col_to_offset(text, pos.line + 1, 1)
    end = line_col_to_offset(text, end_pos.line + 1, end_pos.column + 1)
    if end < len(text) and text[end] == "\n":
        end += 1
    return (start, end) if start < end else None


def _build_move_docstring_into_body_fix(
    text: str,
    doc: DocstringAstNode,
    target: MacroAstNode | ScopeAstNode,
) -> Fix | None:
    """DOC003: move a docstring that sits above `target` into the
    first statement of `target`'s body.

    Safe: the docstring's textual content is preserved exactly; only
    its position shifts. Tooling that walks the body for documentation
    (LSP hover, doc generators) now finds it where it expects.
    """
    span = _docstring_span(text, doc)
    if span is None:
        return None
    block = target.block if isinstance(target, MacroAstNode) else target.body
    block_pos = getattr(block.file_info, "position", None)
    if block_pos is None:
        return None
    after_brace = _block_brace_offset(text, target)
    if after_brace is None:
        return None
    indent = _detect_body_indent(text, after_brace, fallback_column=block_pos.column)
    lines = doc.text.splitlines() or [""]
    if len(lines) == 1:
        moved = f'{indent}"""{lines[0]}"""'
    else:
        body_lines = "\n".join(f"{indent}{line}".rstrip() for line in lines)
        moved = f'{indent}"""\n{body_lines}\n{indent}"""'
    insertion = f"\n{moved}"
    start, end = span
    return Fix(
        edits=(
            TextEdit(start=start, end=end, replacement=""),
            TextEdit(start=after_brace, end=after_brace, replacement=insertion),
        ),
        applicability=Applicability.SAFE,
        description=f"move docstring inside {kind_label(target)} body",
    )


class OrphanDocstring(Rule):
    code = "DOC004"
    description = "orphan docstring used as a comment"
    rationale = (
        "A docstring sitting between instructions, with no documentable "
        "target on either side, is being used as a comment. Use a `;` "
        "comment instead — docstrings are a structural feature the parser "
        "attaches to specific nodes, and inline orphans confuse downstream "
        "consumers."
    )
    bad = '"""Module."""\nmain:\n    rts\n    """orphan note used as comment"""\n    nop\n'
    good = '"""Module."""\nmain:\n    rts\n    ; orphan note used as comment\n    nop\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        return ctx.placement_hits(self.code)


class CommentInsteadOfDocstring(Rule):
    code = "DOC005"
    description = "comment block where a docstring is expected"
    rationale = (
        "A comment block (≥2 consecutive `;` lines or a block comment "
        "with embedded newlines) sitting directly above a public macro "
        "or scope that has no docstring is almost always intended to be "
        "the docstring. Promote it so tooling can find it."
    )
    bad = '"""Module."""\n; first banner line\n; second banner line\n.macro setup_counter() {\n    ldx.w #0\n}\n'
    good = (
        '"""Module."""\n.macro setup_counter() {\n'
        '    """\n    first banner line\n    second banner line\n    """\n'
        "    ldx.w #0\n}\n"
    )

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        return ctx.placement_hits(self.code)


class RedundantCommentAndDocstring(Rule):
    code = "DOC006"
    description = "redundant comment block + docstring on a single target"
    rationale = (
        "A public target that carries both a leading comment block AND "
        "a docstring is duplicating its description in two places. Pick "
        "one — typically the inside-body docstring — so updates only "
        "have to land in one spot."
    )
    bad = (
        '"""Module."""\n; banner line one\n; banner line two\n'
        '.macro setup_counter() {\n    """Reset the loop counter."""\n'
        "    ldx.w #0\n}\n"
    )
    good = '"""Module."""\n.macro setup_counter() {\n    """Reset the loop counter."""\n    ldx.w #0\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        return ctx.placement_hits(self.code)


class DocstringAlignment(Rule):
    code = "DOC007"
    description = "docstring content not aligned with its opening triple quote"
    rationale = (
        "A multi-line docstring's content should align with the column "
        'of its opening `"""`. Under-indented content reads as leaking '
        "out of the docstring; over-indented content suggests the author "
        "copy-pasted from a deeper scope. Mirrors pydocstyle's D207 / "
        "D208 for Python."
    )
    bad = '"""Module."""\nmy_label:\n    """\n        over-indented body\n    """\n    rts\n'
    good = '"""Module."""\nmy_label:\n    """\n    aligned body\n    """\n    rts\n'
    accepts = (DocstringAstNode,)

    def visit(self, ctx: LintContext, node: AstNode) -> Iterable[Diagnostic]:
        assert isinstance(node, DocstringAstNode)
        if "\n" not in node.text:
            return
        open_col = self._open_column(ctx.text, node)
        if open_col is None:
            return
        indents = self._content_indents(node.text)
        if not indents:
            return
        smallest = min(indents)
        if smallest == open_col:
            return
        direction = "under-indented" if smallest < open_col else "over-indented"
        yield self.diagnose(
            ctx,
            node,
            f'docstring {direction}: content starts at column {smallest + 1}, opening `"""` at column {open_col + 1}',
            fix=_build_docstring_alignment_fix(ctx.text, node, open_col, smallest),
        )

    @staticmethod
    def _open_column(text: str, node: DocstringAstNode) -> int | None:
        """Column of the opening `\"\"\"` in source.

        Returns None defensively when the position can't be located.
        """
        pos = getattr(node.file_info, "position", None)
        if pos is None:
            return None
        source_lines = text.split("\n")
        if pos.line < 0 or pos.line >= len(source_lines):
            return None
        idx = source_lines[pos.line].find('"""')
        return idx if idx >= 0 else None

    @staticmethod
    def _content_indents(text: str) -> list[int]:
        """Leading-space count of every non-blank line *after* the first.

        The first line sits next to the opening `\"\"\"` so its leading
        whitespace is meaningless; pure-blank lines don't constrain
        indent either.
        """
        raw = text.split("\n")
        if not raw:
            return []
        return [len(line) - len(line.lstrip(" ")) for line in raw[1:] if line.strip()]
