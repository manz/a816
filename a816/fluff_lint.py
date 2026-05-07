"""Lint rules for `a816 fluff check`.

Each rule is a `Rule` subclass; module-level `RULES` lists every
instance and `Rule.registry` indexes them by code. Adding a rule means
writing a new subclass and appending it to `RULES`.

Categories follow ruff's convention:
- `DOC*` — docstring coverage / placement.
- `E***` — physical-layout (line length, etc.).
- `N***` — naming.

A trailing `; noqa` comment silences every rule on that line. Pass
codes to suppress selectively, ruff-style: `; noqa: E501,N801`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from a816.config import discover_a816_config
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    BlockAstNode,
    CommentAstNode,
    CompoundAstNode,
    DocstringAstNode,
    ForAstNode,
    IfAstNode,
    LabelAstNode,
    MacroAstNode,
    ScopeAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.mzparser import MZParser

MAX_LINE_LENGTH = 120

_SNAKE_CASE_RE = re.compile(r"^_?[a-z][a-z0-9_]*$")
_SCREAMING_SNAKE_CASE_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
# Deterministic match: locate `noqa` once, parse the codes text by hand
# below. Avoids nested overlapping quantifiers that can spook ReDoS scanners.
_NOQA_RE = re.compile(r";[ \t]*noqa\b(.*)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Diagnostic + context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnostic:
    """One lint hit. `path:line:col code message`, ruff-style."""

    path: Path
    line: int  # 1-based for human output
    column: int  # 1-based
    code: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.column} {self.code} {self.message}"


@dataclass
class LintContext:
    """Inputs every rule sees. AST is `None` when the parser failed."""

    path: Path
    text: str
    nodes: list[AstNode] | None
    parse_failed: bool

    _flat_nodes: list[AstNode] | None = field(default=None, init=False, repr=False)
    _expanded_top_level: list[AstNode] | None = field(default=None, init=False, repr=False)
    _placement_hits: dict[str, list[Diagnostic]] | None = field(default=None, init=False, repr=False)

    @property
    def flat_nodes(self) -> list[AstNode]:
        """All AST nodes flattened through scopes / blocks / control flow. Cached."""
        if self.nodes is None:
            return []
        if self._flat_nodes is None:
            self._flat_nodes = _flatten(self.nodes)
        return self._flat_nodes

    @property
    def expanded_top_level(self) -> list[AstNode]:
        """Top-level nodes with `.if` / `.for` bodies inlined into the sequence.

        Conditionals don't introduce a docstring scope; rules that walk
        the top-level want to see public targets inside them at the
        same logical depth as their siblings outside. Cached.
        """
        if self.nodes is None:
            return []
        if self._expanded_top_level is None:
            self._expanded_top_level = _expand_through_control_flow(self.nodes)
        return self._expanded_top_level

    def placement_hits(self, code: str) -> list[Diagnostic]:
        """DOC004 / DOC005 / DOC006 share one walker; results are cached per code."""
        if self._placement_hits is None:
            self._placement_hits = _PlacementWalker(self).scan()
        return self._placement_hits.get(code, [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_noqa_map(text: str) -> dict[int, set[str] | None]:
    """Map 1-based line number → suppressed codes (None means silence all)."""
    out: dict[int, set[str] | None] = {}
    for idx, line in enumerate(text.splitlines(), start=1):
        match = _NOQA_RE.search(line)
        if not match:
            continue
        tail = match.group(1).strip()
        if not tail:
            out[idx] = None
            continue
        if not tail.startswith(":"):
            continue  # `noqaXYZ` etc. — not a suppression marker
        codes = {chunk.strip().upper() for chunk in tail[1:].split(",") if chunk.strip()}
        out[idx] = codes or None
    return out


def _is_suppressed(line: int, code: str, noqa_map: dict[int, set[str] | None]) -> bool:
    if line not in noqa_map:
        return False
    entry = noqa_map[line]
    return entry is None or code in entry


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _is_snake_case(name: str) -> bool:
    return bool(_SNAKE_CASE_RE.match(name))


def _is_screaming_snake_case(name: str) -> bool:
    return bool(_SCREAMING_SNAKE_CASE_RE.match(name))


def _node_position(node: AstNode) -> tuple[int, int]:
    pos = getattr(node.file_info, "position", None)
    if pos is None:
        return 1, 1
    return pos.line + 1, pos.column + 1


def _kind_label(node: AstNode) -> str:
    match node:
        case MacroAstNode():
            return "macro"
        case ScopeAstNode():
            return "scope"
        case LabelAstNode():
            return "label"
        case _:
            return "symbol"


def _public_target_name(node: AstNode) -> str | None:
    """Public name of a documentable node, or None when private / not a target."""
    if not isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode):
        return None
    raw = getattr(node, "name", None) or getattr(node, "label", "") or ""
    name = str(raw)
    return name if _is_public(name) else None


def _target_name(node: AstNode) -> str | None:
    """Name of a documentable target (any visibility) or None."""
    if not isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode):
        return None
    raw = getattr(node, "name", None) or getattr(node, "label", "") or ""
    return str(raw) or None


def _flatten(nodes: list[AstNode]) -> list[AstNode]:
    """Flatten nodes through scopes / blocks / control-flow.

    `.if` / `.for` bodies are part of the AST regardless of whether
    the condition resolves at parse time, so the lint sees both
    branches even when fluff has no symbol resolution against the
    prelude.
    """
    out: list[AstNode] = []
    for node in nodes:
        out.append(node)
        match node:
            case ScopeAstNode():
                out.extend(_flatten(list(node.body.body)))
            case BlockAstNode() | CompoundAstNode():
                out.extend(_flatten(list(node.body)))
            case IfAstNode():
                out.extend(_flatten(list(node.block.body)))
                if node.else_block is not None:
                    out.extend(_flatten(list(node.else_block.body)))
            case ForAstNode():
                out.extend(_flatten(list(node.body.body)))
            case MacroAstNode():
                out.extend(_flatten(list(node.block.body)))
    return out


def _expand_through_control_flow(nodes: list[AstNode]) -> list[AstNode]:
    """Inline `.if` / `.for` bodies into the surrounding sequence.

    Conditionals and loops don't introduce a new docstring scope —
    public targets inside them sit at the same logical level as their
    siblings outside. Both branches of an `.if` are inlined.
    """
    out: list[AstNode] = []
    for node in nodes:
        match node:
            case IfAstNode():
                out.extend(_expand_through_control_flow(list(node.block.body)))
                if node.else_block is not None:
                    out.extend(_expand_through_control_flow(list(node.else_block.body)))
            case ForAstNode():
                out.extend(_expand_through_control_flow(list(node.body.body)))
            case _:
                out.append(node)
    return out


def _is_comment_block(comments: list[CommentAstNode]) -> bool:
    """A comment 'block' is ≥2 consecutive lines, or one block with embedded newlines."""
    if len(comments) >= 2:
        return True
    return len(comments) == 1 and "\n" in (comments[0].comment or "")


def _label_has_below_docstring(nodes: list[AstNode], idx: int) -> bool:
    """True when the first non-comment node *after* `nodes[idx]` is a docstring."""
    j = idx + 1
    while j < len(nodes) and isinstance(nodes[j], CommentAstNode):
        j += 1
    return j < len(nodes) and isinstance(nodes[j], DocstringAstNode)


# ---------------------------------------------------------------------------
# Rule base + registry
# ---------------------------------------------------------------------------


class Rule:
    """Lint rule. Subclass and either override `check(ctx)` for whole-file
    rules or set `accepts` + override `visit(ctx, node)` for per-node rules.

    Class attributes:
      `code`: ruff-style identifier (`DOC003`, `E501`, `N801`).
      `description`: one-line summary used in diagnostics.
      `rationale`, `bad`, `good`: docs surfaced by `a816 explain <CODE>`;
        examples are round-tripped through the linter in tests so a
        rule that drifts from its docs fails CI.
      `accepts`: per-node rules; tuple of AST node types the visitor wants.
      `needs_ast`: rules that can run on raw text (E501) set this to False
        so they keep working when parsing fails.
    """

    code: ClassVar[str] = ""
    description: ClassVar[str] = ""
    rationale: ClassVar[str] = ""
    bad: ClassVar[str] = ""
    good: ClassVar[str] = ""
    accepts: ClassVar[tuple[type[AstNode], ...]] = ()
    needs_ast: ClassVar[bool] = True

    registry: ClassVar[dict[str, Rule]] = {}

    def applies_to(self, ctx: LintContext) -> bool:
        """A rule that needs the AST sits out when parsing failed."""
        return not (self.needs_ast and ctx.parse_failed)

    def run(self, ctx: LintContext) -> Iterable[Diagnostic]:
        """Engine entry point: dispatch by visitor vs whole-file shape.

        Rules with a non-empty `accepts` get `visit(ctx, node)` per
        matching flat node. Rules without `accepts` get `check(ctx)`.
        Subclasses override `visit` or `check` — not `run`.
        """
        if self.accepts:
            for node in ctx.flat_nodes:
                if isinstance(node, self.accepts):
                    yield from self.visit(ctx, node)
            return
        yield from self.check(ctx)

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:  # noqa: ARG002
        """Whole-file rules override this. Visitor rules use `accepts` + `visit`."""
        return iter(())

    def visit(self, ctx: LintContext, node: AstNode) -> Iterable[Diagnostic]:  # noqa: ARG002
        """Per-node rules override this when `accepts` is non-empty."""
        return iter(())

    def diagnose(self, ctx: LintContext, node: AstNode, message: str) -> Diagnostic:
        line, col = _node_position(node)
        return Diagnostic(path=ctx.path, line=line, column=col, code=self.code, message=message)


# ---------------------------------------------------------------------------
# DOC001 — module is missing a leading docstring
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DOC002 — public target is missing a docstring
# ---------------------------------------------------------------------------


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
            name = _public_target_name(node)
            if name is not None and self._target_undocumented(nodes, idx, node, pending_doc_above):
                yield self.diagnose(ctx, node, f"public {_kind_label(node)} '{name}' is missing a docstring")
            pending_doc_above = False

    @staticmethod
    def _target_undocumented(nodes: list[AstNode], idx: int, node: AstNode, pending_doc_above: bool) -> bool:
        if isinstance(node, LabelAstNode):
            return not _label_has_below_docstring(nodes, idx)
        return not pending_doc_above and not getattr(node, "docstring", None)


# ---------------------------------------------------------------------------
# DOC003 — docstring directly above a target is misplaced
# ---------------------------------------------------------------------------


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
            if state.pending_doc is not None:
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
        name = _target_name(target)
        if name is None:
            return None
        if isinstance(target, MacroAstNode | ScopeAstNode):
            return self.diagnose(
                ctx,
                doc,
                f"docstring above {_kind_label(target)} '{name}' should "
                "be moved inside the body (first statement after `{`)",
            )
        if isinstance(target, LabelAstNode):
            return self.diagnose(
                ctx,
                doc,
                f"docstring above label '{name}' should sit below it (first statement after the colon)",
            )
        return None


# ---------------------------------------------------------------------------
# DOC004 / DOC005 / DOC006 — shared placement walker
# ---------------------------------------------------------------------------


_DOC004_MSG = "orphan docstring; convert to a `;` comment or attach to a target"


@dataclass
class _PlacementState:
    comment_run: list[CommentAstNode] = field(default_factory=list)
    pending_doc: DocstringAstNode | None = None
    saw_first_non_comment: bool = False
    # Set after a public / private label so the next docstring is its
    # below-attach point (consumed silently, not flagged orphan).
    expecting_label_doc: bool = False


class _PlacementWalker:
    """Walks the AST once and emits DOC004 / DOC005 / DOC006 hits.

    DOC004 catches orphan docstrings used as comments. DOC005 catches a
    leading comment block that should have been a docstring. DOC006
    catches the redundant case (target carries both).
    """

    def __init__(self, ctx: LintContext) -> None:
        self._path = ctx.path
        self._nodes = ctx.nodes or []
        self.out: dict[str, list[Diagnostic]] = {"DOC004": [], "DOC005": [], "DOC006": []}

    def scan(self) -> dict[str, list[Diagnostic]]:
        self._walk(self._nodes, inside_body=False)
        return self.out

    # ----- emit helpers ----------------------------------------------------

    def _emit(self, code: str, node: AstNode, message: str) -> None:
        line, col = _node_position(node)
        self.out[code].append(Diagnostic(path=self._path, line=line, column=col, code=code, message=message))

    def _flush_orphan(self, state: _PlacementState) -> None:
        if state.pending_doc is not None:
            self._emit("DOC004", state.pending_doc, _DOC004_MSG)
            state.pending_doc = None

    # ----- per-node handlers ----------------------------------------------

    def _on_docstring(self, state: _PlacementState, node: DocstringAstNode, inside_body: bool) -> None:
        if not state.saw_first_non_comment and not inside_body:
            # Module-leading docstring: consumed, not orphan.
            state.saw_first_non_comment = True
            state.comment_run = []
            return
        state.saw_first_non_comment = True
        if state.expecting_label_doc:
            # Below-label attach point: consume silently.
            state.expecting_label_doc = False
            state.pending_doc = None
            state.comment_run = []
            return
        self._flush_orphan(state)
        state.pending_doc = node
        state.comment_run = []

    def _on_block_target(
        self,
        state: _PlacementState,
        node: MacroAstNode | ScopeAstNode,
        target_name: str,
    ) -> None:
        inside_doc = bool(getattr(node, "docstring", None))
        has_above_doc = state.pending_doc is not None
        comment_block = _is_comment_block(state.comment_run)
        if inside_doc and comment_block:
            self._emit(
                "DOC006",
                node,
                f"{_kind_label(node)} '{target_name}' has both a leading comment block and a docstring; pick one",
            )
        elif not inside_doc and not has_above_doc and comment_block:
            self._emit(
                "DOC005",
                state.comment_run[0],
                f"comment block above {_kind_label(node)} '{target_name}' should be a docstring (move inside the body)",
            )
        state.pending_doc = None
        state.comment_run = []
        state.expecting_label_doc = False

    # ----- core walk -------------------------------------------------------

    def _dispatch_target(self, state: _PlacementState, node: AstNode) -> bool:
        """Apply target-specific handling. Returns True when the caller should
        skip the recursion match block (label cases keep `expecting_label_doc`
        live across the next iteration)."""
        public_name = _public_target_name(node)
        match node:
            case MacroAstNode() | ScopeAstNode() if public_name is not None:
                self._on_block_target(state, node, public_name)
            case MacroAstNode() | ScopeAstNode():
                # Private (`_`-prefixed) macro / scope. Consume any leading
                # docstring without flagging — DOC003 still applies, but
                # DOC005 / DOC006 are public-API hygiene.
                state.pending_doc = None
                state.comment_run = []
            case LabelAstNode():
                state.pending_doc = None
                state.comment_run = []
                state.expecting_label_doc = True
                return True
            case _:
                self._flush_orphan(state)
                state.comment_run = []
                state.expecting_label_doc = False
        return False

    def _recurse(self, node: AstNode) -> None:
        match node:
            case ScopeAstNode():
                self._walk(list(node.body.body), inside_body=True)
            case MacroAstNode():
                self._walk(list(node.block.body), inside_body=True)
            case IfAstNode():
                self._walk(list(node.block.body), inside_body=True)
                if node.else_block is not None:
                    self._walk(list(node.else_block.body), inside_body=True)
            case ForAstNode():
                self._walk(list(node.body.body), inside_body=True)

    def _walk(self, nodes: list[AstNode], inside_body: bool) -> None:
        state = _PlacementState()
        for node in nodes:
            match node:
                case CommentAstNode():
                    state.comment_run.append(node)
                    continue
                case DocstringAstNode():
                    self._on_docstring(state, node, inside_body)
                    continue
            state.saw_first_non_comment = True
            skip_recurse = self._dispatch_target(state, node)
            if skip_recurse:
                continue
            self._recurse(node)
        self._flush_orphan(state)


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


# ---------------------------------------------------------------------------
# DOC007 — docstring content alignment (D207 / D208)
# ---------------------------------------------------------------------------


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
        )

    @staticmethod
    def _open_column(text: str, node: DocstringAstNode) -> int | None:
        """Column of the opening `\"\"\"` in source.

        The parser stores the *end* of the token in `position`, so we
        walk back by the docstring's newline count and look up `\"\"\"`
        on the resulting source line. Returns None defensively when the
        position can't be located.
        """
        pos = getattr(node.file_info, "position", None)
        if pos is None:
            return None
        open_line_no = pos.line - node.text.count("\n")
        source_lines = text.split("\n")
        if open_line_no < 0 or open_line_no >= len(source_lines):
            return None
        idx = source_lines[open_line_no].find('"""')
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


# ---------------------------------------------------------------------------
# E501 — line longer than the configured limit
# ---------------------------------------------------------------------------


class LineTooLong(Rule):
    code = "E501"
    description = f"line longer than {MAX_LINE_LENGTH} characters"
    rationale = (
        f"Lines longer than {MAX_LINE_LENGTH} characters are hard to "
        "review in side-by-side diffs and rarely improve readability. "
        "Wrap, reflow, or — for `.dw` / `.db` data lines that are long "
        "for a structural reason — silence with `; noqa: E501`."
    )
    needs_ast = False

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        for index, line in enumerate(ctx.text.splitlines(), start=1):
            length = len(line)
            if length > MAX_LINE_LENGTH:
                yield Diagnostic(
                    path=ctx.path,
                    line=index,
                    column=MAX_LINE_LENGTH + 1,
                    code=self.code,
                    message=f"line too long ({length} > {MAX_LINE_LENGTH} characters)",
                )


# ---------------------------------------------------------------------------
# N801 / N802 — naming
# ---------------------------------------------------------------------------


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
        if not name or _is_snake_case(name):
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
        if not name or _is_snake_case(name) or _is_screaming_snake_case(name):
            return
        yield self.diagnose(ctx, node, f"constant '{name}' should be snake_case or SCREAMING_SNAKE_CASE")


# ---------------------------------------------------------------------------
# Registry + entry points
# ---------------------------------------------------------------------------


RULES: list[Rule] = [
    MissingModuleDocstring(),
    MissingTargetDocstring(),
    MisplacedDocstring(),
    OrphanDocstring(),
    CommentInsteadOfDocstring(),
    RedundantCommentAndDocstring(),
    DocstringAlignment(),
    LineTooLong(),
    LabelNaming(),
    ConstantNaming(),
]
Rule.registry = {rule.code: rule for rule in RULES}


def all_rule_codes() -> list[str]:
    return sorted(Rule.registry)


def lint_text(
    text: str,
    path: Path,
    *,
    include_paths: list[Path] | None = None,
) -> list[Diagnostic]:
    """Run every registered rule against in-memory source text.

    `include_paths` is forwarded to the parser so `.include` directives
    resolve the same way they do under the assembler. The fluff CLI
    fills it from the project's `a816.toml`; callers without a config
    can pass it explicitly.
    """
    result = MZParser.parse_as_ast(text, str(path), include_paths=include_paths)
    parse_failed = bool(result.error)
    nodes = None if parse_failed else list(result.nodes)
    ctx = LintContext(path=path, text=text, nodes=nodes, parse_failed=parse_failed)

    diagnostics: list[Diagnostic] = []
    for rule in RULES:
        if not rule.applies_to(ctx):
            continue
        diagnostics.extend(rule.run(ctx))

    noqa_map = _build_noqa_map(text)
    return [d for d in diagnostics if not _is_suppressed(d.line, d.code, noqa_map)]


def lint_file(path: Path) -> list[Diagnostic]:
    """Run every registered rule against a single source file.

    Discovers the project's `a816.toml` by walking up from `path` and
    forwards its `include-paths` to the parser.
    """
    config = discover_a816_config(path)
    include_paths = config.include_paths if config is not None else None
    return lint_text(path.read_text(encoding="utf-8"), path, include_paths=include_paths)
