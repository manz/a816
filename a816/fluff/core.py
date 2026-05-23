"""Fluff lint framework: Diagnostic, LintContext, Rule, shared helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar

from a816.parse.ast.nodes import (
    AstNode,
    BlockAstNode,
    CommentAstNode,
    CompoundAstNode,
    DocstringAstNode,
    ForAstNode,
    IfAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroAstNode,
    ScopeAstNode,
)


class Applicability(Enum):
    """How safe a fix is.

    SAFE — guaranteed behaviour-preserving. Applied unconditionally by
    `a816 fix`.

    UNSAFE — might change runtime semantics, drop comments / whitespace,
    or surface latent bugs as new errors. Requires `--unsafe-fixes` to
    apply. Reported as fixable in the listing either way.

    Ruff uses the same split; mirroring keeps the mental model aligned
    for anyone who already lints Python with ruff.
    """

    SAFE = "safe"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class TextEdit:
    """One contiguous text replacement, expressed in byte offsets so the
    driver can apply edits in reverse order without needing to maintain
    a running line/column shift."""

    start: int  # inclusive byte offset into source text
    end: int  # exclusive byte offset
    replacement: str


@dataclass(frozen=True)
class Fix:
    """One or more text edits that resolve a diagnostic.

    Edits target the source `text` the diagnostic was produced against;
    applying multiple fixes to the same file requires the driver to sort
    by descending offset so earlier edits don't invalidate later ones.
    """

    edits: tuple[TextEdit, ...]
    applicability: Applicability
    description: str

MAX_LINE_LENGTH = 120

_SNAKE_CASE_RE = re.compile(r"^_?[a-z][a-z0-9_]*$")
_SCREAMING_SNAKE_CASE_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
# Deterministic match: locate `noqa` once, parse the codes text by hand
# below. Avoids nested overlapping quantifiers that can spook ReDoS scanners.
_NOQA_RE = re.compile(r";[ \t]*noqa\b(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Diagnostic:
    """One lint hit. `path:line:col code message`, ruff-style.

    `fix` is the optional autofix. `None` means the rule has no
    mechanical resolution; the user has to edit the source themselves.
    """

    path: Path
    line: int  # 1-based for human output
    column: int  # 1-based
    code: str
    message: str
    fix: Fix | None = None

    def format(self) -> str:
        marker = ""
        if self.fix is not None:
            marker = " [*]" if self.fix.applicability is Applicability.SAFE else " [!]"
        return f"{self.path}:{self.line}:{self.column} {self.code}{marker} {self.message}"


@dataclass
class LintContext:
    """Inputs every rule sees. AST is `None` when the parser failed."""

    path: Path
    text: str
    nodes: list[AstNode] | None
    parse_failed: bool
    # Search paths forwarded by the lint entry-points. `module_paths` lets
    # struct-type rules (S001) follow `.import` chains so cross-module
    # struct names resolve. `include_paths` is reserved for future rules
    # that need to chase `.include` outside the already-inlined AST.
    module_paths: list[Path] | None = None
    include_paths_for_lookup: list[Path] | None = None

    _flat_nodes: list[AstNode] | None = field(default=None, init=False, repr=False)
    _expanded_top_level: list[AstNode] | None = field(default=None, init=False, repr=False)
    _placement_hits: dict[str, list[Diagnostic]] | None = field(default=None, init=False, repr=False)
    _imported_struct_types: set[str] | None = field(default=None, init=False, repr=False)

    @property
    def flat_nodes(self) -> list[AstNode]:
        """All AST nodes flattened through scopes / blocks / control flow. Cached."""
        if self.nodes is None:
            return []
        if self._flat_nodes is None:
            self._flat_nodes = flatten_nodes(self.nodes)
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
            self._expanded_top_level = expand_through_control_flow(self.nodes)
        return self._expanded_top_level

    def placement_hits(self, code: str) -> list[Diagnostic]:
        """DOC004 / DOC005 / DOC006 share one walker; results are cached per code."""
        if self._placement_hits is None:
            self._placement_hits = _PlacementWalker(self).scan()
        return self._placement_hits.get(code, [])


def build_noqa_map(text: str) -> dict[int, set[str] | None]:
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


def is_suppressed(line: int, code: str, noqa_map: dict[int, set[str] | None]) -> bool:
    if line not in noqa_map:
        return False
    entry = noqa_map[line]
    return entry is None or code in entry


def is_public(name: str) -> bool:
    return not name.startswith("_")


def is_snake_case(name: str) -> bool:
    return bool(_SNAKE_CASE_RE.match(name))


def is_screaming_snake_case(name: str) -> bool:
    return bool(_SCREAMING_SNAKE_CASE_RE.match(name))


def node_position(node: AstNode) -> tuple[int, int]:
    pos = getattr(node.file_info, "position", None)
    if pos is None:
        return 1, 1
    return pos.line + 1, pos.column + 1


def line_col_to_offset(text: str, line_1based: int, column_1based: int) -> int:
    """Convert a 1-based (line, column) pair into a 0-based byte offset
    into `text`. Used by fix builders that need a TextEdit range from
    a Token's position.

    Columns past the end of the line clamp to the line's length —
    diagnostics that point one past the visible content (typical
    end-of-token marker) still produce a valid offset."""
    line_idx = max(line_1based - 1, 0)
    col_idx = max(column_1based - 1, 0)
    cursor = 0
    current_line = 0
    while current_line < line_idx:
        nl = text.find("\n", cursor)
        if nl == -1:
            return len(text)
        cursor = nl + 1
        current_line += 1
    next_nl = text.find("\n", cursor)
    line_end = len(text) if next_nl == -1 else next_nl
    return min(cursor + col_idx, line_end)


def kind_label(node: AstNode) -> str:
    match node:
        case MacroAstNode():
            return "macro"
        case ScopeAstNode():
            return "scope"
        case LabelAstNode() | LabelDeclAstNode():
            return "label"
        case _:
            return "symbol"


def target_raw_name(node: AstNode) -> str:
    raw = getattr(node, "name", None) or getattr(node, "label", None) or getattr(node, "symbol", None) or ""
    return str(raw)


def public_target_name(node: AstNode) -> str | None:
    """Public name of a documentable node, or None when private / not a target."""
    if not isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode | LabelDeclAstNode):
        return None
    name = target_raw_name(node)
    return name if is_public(name) else None


def target_name(node: AstNode) -> str | None:
    """Name of a documentable target (any visibility) or None."""
    if not isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode | LabelDeclAstNode):
        return None
    return target_raw_name(node) or None


def flatten_nodes(nodes: list[AstNode]) -> list[AstNode]:
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
                out.extend(flatten_nodes(list(node.body.body)))
            case BlockAstNode() | CompoundAstNode():
                out.extend(flatten_nodes(list(node.body)))
            case IfAstNode():
                out.extend(flatten_nodes(list(node.block.body)))
                if node.else_block is not None:
                    out.extend(flatten_nodes(list(node.else_block.body)))
            case ForAstNode():
                out.extend(flatten_nodes(list(node.body.body)))
            case MacroAstNode():
                out.extend(flatten_nodes(list(node.block.body)))
    return out


def expand_through_control_flow(nodes: list[AstNode]) -> list[AstNode]:
    """Inline `.if` / `.for` bodies into the surrounding sequence.

    Conditionals and loops don't introduce a new docstring scope —
    public targets inside them sit at the same logical level as their
    siblings outside. Both branches of an `.if` are inlined.
    """
    out: list[AstNode] = []
    for node in nodes:
        match node:
            case IfAstNode():
                out.extend(expand_through_control_flow(list(node.block.body)))
                if node.else_block is not None:
                    out.extend(expand_through_control_flow(list(node.else_block.body)))
            case ForAstNode():
                out.extend(expand_through_control_flow(list(node.body.body)))
            case _:
                out.append(node)
    return out


def is_comment_block(comments: list[CommentAstNode]) -> bool:
    """A comment 'block' is ≥2 consecutive lines, or one block with embedded newlines."""
    if len(comments) >= 2:
        return True
    return len(comments) == 1 and "\n" in (comments[0].comment or "")


def label_has_below_docstring(nodes: list[AstNode], idx: int) -> bool:
    """True when the first non-comment node *after* `nodes[idx]` is a docstring."""
    j = idx + 1
    while j < len(nodes) and isinstance(nodes[j], CommentAstNode):
        j += 1
    return j < len(nodes) and isinstance(nodes[j], DocstringAstNode)


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

    def check(self, _ctx: LintContext) -> Iterable[Diagnostic]:
        """Whole-file rules override this. Visitor rules use `accepts` + `visit`."""
        return iter(())

    def visit(self, _ctx: LintContext, _node: AstNode) -> Iterable[Diagnostic]:
        """Per-node rules override this when `accepts` is non-empty."""
        return iter(())

    def diagnose(
        self, ctx: LintContext, node: AstNode, message: str, fix: Fix | None = None
    ) -> Diagnostic:
        line, col = node_position(node)
        return Diagnostic(path=ctx.path, line=line, column=col, code=self.code, message=message, fix=fix)


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

    def _emit(self, code: str, node: AstNode, message: str) -> None:
        line, col = node_position(node)
        self.out[code].append(Diagnostic(path=self._path, line=line, column=col, code=code, message=message))

    def _flush_orphan(self, state: _PlacementState) -> None:
        if state.pending_doc is not None:
            self._emit("DOC004", state.pending_doc, _DOC004_MSG)
            state.pending_doc = None

    def _on_docstring(self, state: _PlacementState, node: DocstringAstNode, inside_body: bool) -> None:
        if not state.saw_first_non_comment and not inside_body:
            state.saw_first_non_comment = True
            state.comment_run = []
            return
        state.saw_first_non_comment = True
        if state.expecting_label_doc:
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
        target_name_str: str,
    ) -> None:
        inside_doc = bool(getattr(node, "docstring", None))
        has_above_doc = state.pending_doc is not None
        comment_block = is_comment_block(state.comment_run)
        if inside_doc and comment_block:
            self._emit(
                "DOC006",
                node,
                f"{kind_label(node)} '{target_name_str}' has both a leading comment block and a docstring; pick one",
            )
        elif not inside_doc and not has_above_doc and comment_block:
            self._emit(
                "DOC005",
                state.comment_run[0],
                f"comment block above {kind_label(node)} '{target_name_str}' should be a docstring (move inside the body)",
            )
        state.pending_doc = None
        state.comment_run = []
        state.expecting_label_doc = False

    def _dispatch_target(self, state: _PlacementState, node: AstNode) -> bool:
        """Apply target-specific handling. Returns True when the caller should
        skip the recursion match block (label cases keep `expecting_label_doc`
        live across the next iteration)."""
        public_name = public_target_name(node)
        match node:
            case MacroAstNode() | ScopeAstNode() if public_name is not None:
                self._on_block_target(state, node, public_name)
            case MacroAstNode() | ScopeAstNode():
                state.pending_doc = None
                state.comment_run = []
            case LabelAstNode():
                state.pending_doc = None
                state.comment_run = []
                state.expecting_label_doc = True
                return True
            case LabelDeclAstNode():
                state.pending_doc = None
                state.comment_run = []
                state.expecting_label_doc = False
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
