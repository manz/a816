"""Lint rules for `a816 fluff check`.

Each rule is an instance of `Rule` registered in `_REGISTRY`. A rule
declares its `code`, a one-line `description`, and a `check` callable
that turns a `LintContext` into diagnostics. Add a rule by appending
a new `Rule(...)` to the registry.

Categories follow ruff's convention:
- `DOC*` — docstring coverage / placement.
- `E***` — physical-layout (line length, etc.).
- `N***` — naming.

A trailing `; noqa` comment silences every rule on that line. Pass
codes to suppress selectively, ruff-style: `; noqa: E501,N801`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
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
    LabelAstNode,
    MacroAstNode,
    ScopeAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.mzparser import MZParser

MAX_LINE_LENGTH = 120

_SNAKE_CASE_RE = re.compile(r"^_?[a-z][a-z0-9_]*$")
_SCREAMING_SNAKE_CASE_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
_NOQA_RE = re.compile(r";\s*noqa(?:\s*:\s*([A-Za-z0-9_, ]+))?\s*$", re.IGNORECASE)


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
    _doc_placement: dict[str, list[Diagnostic]] | None = field(default=None, init=False, repr=False)

    @property
    def flat_nodes(self) -> list[AstNode]:
        """All AST nodes flattened through scopes/blocks. Cached."""
        if self.nodes is None:
            return []
        if self._flat_nodes is None:
            self._flat_nodes = _walk_nodes(self.nodes)
        return self._flat_nodes

    def doc_placement_hits(self, code: str) -> list[Diagnostic]:
        """DOC004 / DOC005 / DOC006 share one walker; results are cached per code."""
        if self._doc_placement is None:
            self._doc_placement = _doc_placement_scan(self)
        return self._doc_placement.get(code, [])


@dataclass(frozen=True)
class Rule:
    """One lint rule.

    `accepts` non-empty → `handler(ctx, node)` runs once per matching
    AST node (post-flatten through scopes/blocks). Empty → `handler(ctx)`
    runs once for the whole file. Rules that only need text (E501, etc.)
    set `needs_ast=False` so they still run when parsing fails.
    """

    code: str
    description: str
    handler: Callable[..., Iterable[Diagnostic]]
    accepts: tuple[type[AstNode], ...] = ()
    needs_ast: bool = True

    Registry: ClassVar[dict[str, Rule]] = {}


def _build_noqa_map(text: str) -> dict[int, set[str] | None]:
    """Map 1-based line number → suppressed codes (None means silence all)."""
    out: dict[int, set[str] | None] = {}
    for idx, line in enumerate(text.splitlines(), start=1):
        match = _NOQA_RE.search(line)
        if not match:
            continue
        codes_group = match.group(1)
        if codes_group is None:
            out[idx] = None
            continue
        codes = {chunk.strip().upper() for chunk in codes_group.split(",") if chunk.strip()}
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
    """Return the public name of a documentable node, or None when private/N/A."""
    if not isinstance(node, MacroAstNode | ScopeAstNode | LabelAstNode):
        return None
    raw = getattr(node, "name", None) or getattr(node, "label", "") or ""
    name = str(raw)
    return name if _is_public(name) else None


def _walk_nodes(nodes: list[AstNode]) -> list[AstNode]:
    """Flatten nodes through scopes/blocks so nested labels/constants are visited."""
    out: list[AstNode] = []
    for node in nodes:
        out.append(node)
        match node:
            case ScopeAstNode():
                out.extend(_walk_nodes(list(node.body.body)))
            case BlockAstNode() | CompoundAstNode():
                out.extend(_walk_nodes(list(node.body)))
    return out


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _check_doc001(ctx: LintContext) -> Iterable[Diagnostic]:
    nodes = ctx.nodes or []
    for node in nodes:
        if isinstance(node, CommentAstNode):
            continue
        if isinstance(node, DocstringAstNode):
            return
        yield Diagnostic(
            path=ctx.path,
            line=1,
            column=1,
            code="DOC001",
            message="module is missing a leading docstring",
        )
        return


def _label_has_below_docstring(nodes: list[AstNode], idx: int) -> bool:
    """True when the first non-comment node *after* `nodes[idx]` is a docstring."""
    j = idx + 1
    while j < len(nodes) and isinstance(nodes[j], CommentAstNode):
        j += 1
    return j < len(nodes) and isinstance(nodes[j], DocstringAstNode)


def _check_doc002(ctx: LintContext) -> Iterable[Diagnostic]:
    """Public macro / scope / label needs a docstring.

    Macros and scopes have an inside-body slot.
    Labels are flat jump targets — by convention the docstring sits as
    the first statement *after* the label, like Python's function-body
    docstring.
    """
    nodes = ctx.nodes or []
    pending_doc = False
    module_doc_consumed = False
    for idx, node in enumerate(nodes):
        if isinstance(node, CommentAstNode):
            continue
        if isinstance(node, DocstringAstNode):
            if module_doc_consumed:
                pending_doc = True
            else:
                module_doc_consumed = True
            continue
        module_doc_consumed = True
        name = _public_target_name(node)
        if name is not None and _doc002_target_undocumented(nodes, idx, node, pending_doc):
            line, col = _node_position(node)
            yield Diagnostic(
                path=ctx.path,
                line=line,
                column=col,
                code="DOC002",
                message=f"public {_kind_label(node)} '{name}' is missing a docstring",
            )
        pending_doc = False


def _doc002_target_undocumented(
    nodes: list[AstNode],
    idx: int,
    node: AstNode,
    pending_doc_above: bool,
) -> bool:
    if isinstance(node, LabelAstNode):
        return not _label_has_below_docstring(nodes, idx)
    return not pending_doc_above and not getattr(node, "docstring", None)


def _check_doc003(ctx: LintContext) -> Iterable[Diagnostic]:
    """Docstring directly above a public target is misplaced.

    For macros / scopes the canonical spot is the first statement
    *inside* the body, right after the opening brace. For labels it
    is the first statement *after* the label — labels are flat jump
    targets with no body of their own.
    """
    pending_doc: DocstringAstNode | None = None
    module_doc_consumed = False
    for node in ctx.nodes or []:
        if isinstance(node, CommentAstNode):
            continue
        if isinstance(node, DocstringAstNode):
            if module_doc_consumed:
                pending_doc = node
            else:
                module_doc_consumed = True
            continue
        module_doc_consumed = True
        if pending_doc is not None:
            target = _public_target_name(node)
            if target is not None and isinstance(node, MacroAstNode | ScopeAstNode):
                yield _doc003_for_block(ctx, pending_doc, node, target)
            elif target is not None and isinstance(node, LabelAstNode):
                yield _doc003_for_label(ctx, pending_doc, target)
        pending_doc = None


def _doc003_for_block(
    ctx: LintContext, doc: DocstringAstNode, node: MacroAstNode | ScopeAstNode, target: str
) -> Diagnostic:
    line, col = _node_position(doc)
    return Diagnostic(
        path=ctx.path,
        line=line,
        column=col,
        code="DOC003",
        message=(
            f"docstring above {_kind_label(node)} '{target}' should "
            "be moved inside the body (first statement after `{`)"
        ),
    )


def _doc003_for_label(ctx: LintContext, doc: DocstringAstNode, target: str) -> Diagnostic:
    line, col = _node_position(doc)
    return Diagnostic(
        path=ctx.path,
        line=line,
        column=col,
        code="DOC003",
        message=(f"docstring above label '{target}' should sit below it (first statement after the colon)"),
    )


def _is_comment_block(comments: list[CommentAstNode]) -> bool:
    """A comment 'block' is ≥2 consecutive lines, or one block with embedded newlines."""
    if len(comments) >= 2:
        return True
    return len(comments) == 1 and "\n" in (comments[0].comment or "")


@dataclass
class _PlacementState:
    """Scratch state for `_doc_placement_scan`'s per-block walk."""

    comment_run: list[CommentAstNode] = field(default_factory=list)
    pending_doc: DocstringAstNode | None = None
    saw_first_non_comment: bool = False
    # True when the previous node was a public label, so the next
    # docstring is the label's "below" attach point and not orphan.
    expecting_label_doc: bool = False


_DOC004_MSG = "orphan docstring; convert to a `;` comment or attach to a target"


def _emit_doc(out: dict[str, list[Diagnostic]], path: Path, code: str, node: AstNode, message: str) -> None:
    line, col = _node_position(node)
    out[code].append(Diagnostic(path=path, line=line, column=col, code=code, message=message))


def _flush_orphan_doc(out: dict[str, list[Diagnostic]], path: Path, state: _PlacementState) -> None:
    if state.pending_doc is not None:
        _emit_doc(out, path, "DOC004", state.pending_doc, _DOC004_MSG)
        state.pending_doc = None


def _handle_docstring_node(
    out: dict[str, list[Diagnostic]],
    path: Path,
    state: _PlacementState,
    node: DocstringAstNode,
    inside_body: bool,
) -> None:
    if not state.saw_first_non_comment and not inside_body:
        # Module-leading docstring: consumed, not orphan.
        state.saw_first_non_comment = True
        state.comment_run = []
        return
    state.saw_first_non_comment = True
    if state.expecting_label_doc:
        # Docstring sits right below a public label — that's the
        # canonical attach point. Consume silently.
        state.expecting_label_doc = False
        state.pending_doc = None
        state.comment_run = []
        return
    _flush_orphan_doc(out, path, state)
    state.pending_doc = node
    state.comment_run = []


def _handle_target_node(
    out: dict[str, list[Diagnostic]],
    path: Path,
    state: _PlacementState,
    node: MacroAstNode | ScopeAstNode,
    target_name: str,
) -> None:
    inside_doc = bool(getattr(node, "docstring", None))
    has_above_doc = state.pending_doc is not None
    comment_block = _is_comment_block(state.comment_run)
    if inside_doc and comment_block:
        _emit_doc(
            out,
            path,
            "DOC006",
            node,
            f"{_kind_label(node)} '{target_name}' has both a leading comment block and a docstring; pick one",
        )
    elif not inside_doc and not has_above_doc and comment_block:
        _emit_doc(
            out,
            path,
            "DOC005",
            state.comment_run[0],
            f"comment block above {_kind_label(node)} '{target_name}' should be a docstring (move inside the body)",
        )
    state.pending_doc = None
    state.comment_run = []
    state.expecting_label_doc = False


def _placement_walk(out: dict[str, list[Diagnostic]], path: Path, nodes: list[AstNode], inside_body: bool) -> None:
    state = _PlacementState()
    for node in nodes:
        match node:
            case CommentAstNode():
                state.comment_run.append(node)
                continue
            case DocstringAstNode():
                _handle_docstring_node(out, path, state, node, inside_body)
                continue
        state.saw_first_non_comment = True
        public_name = _public_target_name(node)
        match node:
            case MacroAstNode() | ScopeAstNode() if public_name is not None:
                _handle_target_node(out, path, state, node, public_name)
            case LabelAstNode() if public_name is not None:
                # Labels carry their docstring *below*. Flush any
                # docstring-above as orphan (DOC003 covers the misplacement
                # diagnostic separately) and arm the post-label slot so the
                # next docstring is consumed silently.
                state.pending_doc = None
                state.comment_run = []
                state.expecting_label_doc = True
                # Skip the post-target reset further down so the flag
                # survives until the next iteration.
                continue
            case _:
                _flush_orphan_doc(out, path, state)
                state.comment_run = []
                state.expecting_label_doc = False
        match node:
            case ScopeAstNode():
                _placement_walk(out, path, list(node.body.body), inside_body=True)
            case MacroAstNode():
                _placement_walk(out, path, list(node.block.body), inside_body=True)
    _flush_orphan_doc(out, path, state)


def _doc_placement_scan(ctx: LintContext) -> dict[str, list[Diagnostic]]:
    """Walk the AST once and produce DOC004 / DOC005 / DOC006 hits."""
    out: dict[str, list[Diagnostic]] = {"DOC004": [], "DOC005": [], "DOC006": []}
    _placement_walk(out, ctx.path, ctx.nodes or [], inside_body=False)
    return out


def _check_doc004(ctx: LintContext) -> Iterable[Diagnostic]:
    return ctx.doc_placement_hits("DOC004")


def _check_doc005(ctx: LintContext) -> Iterable[Diagnostic]:
    return ctx.doc_placement_hits("DOC005")


def _check_doc006(ctx: LintContext) -> Iterable[Diagnostic]:
    return ctx.doc_placement_hits("DOC006")


def _check_e501(ctx: LintContext) -> Iterable[Diagnostic]:
    for index, line in enumerate(ctx.text.splitlines(), start=1):
        length = len(line)
        if length > MAX_LINE_LENGTH:
            yield Diagnostic(
                path=ctx.path,
                line=index,
                column=MAX_LINE_LENGTH + 1,
                code="E501",
                message=f"line too long ({length} > {MAX_LINE_LENGTH} characters)",
            )


def _docstring_open_column(text: str, node: DocstringAstNode) -> int | None:
    """Column of the opening `\"\"\"` in source.

    The parser stores the *end* of the token in `position`, so we walk
    back by the number of newlines inside the docstring content and
    look up `\"\"\"` on the resulting source line. Returns `None` if
    we can't locate it (defensive).
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


def _docstring_content_indents(text: str) -> list[int]:
    """Leading-space count of every non-blank line *after* the first line.

    The first line sits right next to the opening `\"\"\"` so its
    leading whitespace is meaningless for alignment. Pure-blank lines
    don't constrain indent either.
    """
    raw = text.split("\n")
    if not raw:
        return []
    body = raw[1:]
    return [len(line) - len(line.lstrip(" ")) for line in body if line.strip()]


def _visit_doc007(ctx: LintContext, node: DocstringAstNode) -> Iterable[Diagnostic]:
    if "\n" not in node.text:
        return
    open_col = _docstring_open_column(ctx.text, node)
    if open_col is None:
        return
    indents = _docstring_content_indents(node.text)
    if not indents:
        return
    smallest = min(indents)
    if smallest == open_col:
        return
    direction = "under-indented" if smallest < open_col else "over-indented"
    line, col = _node_position(node)
    yield Diagnostic(
        path=ctx.path,
        line=line,
        column=col,
        code="DOC007",
        message=(
            f'docstring {direction}: content starts at column {smallest + 1}, opening `"""` at column {open_col + 1}'
        ),
    )


def _visit_n801(ctx: LintContext, node: LabelAstNode) -> Iterable[Diagnostic]:
    name = node.label
    if not name or _is_snake_case(name):
        return
    line, col = _node_position(node)
    yield Diagnostic(
        path=ctx.path,
        line=line,
        column=col,
        code="N801",
        message=f"label '{name}' should be snake_case",
    )


def _visit_n802(ctx: LintContext, node: AssignAstNode | SymbolAffectationAstNode) -> Iterable[Diagnostic]:
    name = node.symbol
    if not name or _is_snake_case(name) or _is_screaming_snake_case(name):
        return
    line, col = _node_position(node)
    yield Diagnostic(
        path=ctx.path,
        line=line,
        column=col,
        code="N802",
        message=f"constant '{name}' should be snake_case or SCREAMING_SNAKE_CASE",
    )


_REGISTRY: dict[str, Rule] = {
    rule.code: rule
    for rule in [
        Rule("DOC001", "module is missing a leading docstring", _check_doc001),
        Rule("DOC002", "public macro/scope/label is missing a docstring", _check_doc002),
        Rule("DOC003", "docstring above macro/scope should live inside the body", _check_doc003),
        Rule("DOC004", "orphan docstring used as a comment", _check_doc004),
        Rule("DOC005", "comment block where a docstring is expected", _check_doc005),
        Rule("DOC006", "redundant comment block + docstring on a single target", _check_doc006),
        Rule(
            "DOC007",
            "docstring content not aligned with its opening triple quote",
            _visit_doc007,
            accepts=(DocstringAstNode,),
        ),
        Rule(
            "E501",
            f"line longer than {MAX_LINE_LENGTH} characters",
            _check_e501,
            needs_ast=False,
        ),
        Rule("N801", "label name should be snake_case", _visit_n801, accepts=(LabelAstNode,)),
        Rule(
            "N802",
            "constant name should be snake_case or SCREAMING_SNAKE_CASE",
            _visit_n802,
            accepts=(AssignAstNode, SymbolAffectationAstNode),
        ),
    ]
}
Rule.Registry = _REGISTRY


def all_rule_codes() -> list[str]:
    return sorted(_REGISTRY)


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
    for rule in _REGISTRY.values():
        if rule.needs_ast and parse_failed:
            continue
        if rule.accepts:
            for node in ctx.flat_nodes:
                if isinstance(node, rule.accepts):
                    diagnostics.extend(rule.handler(ctx, node))
        else:
            diagnostics.extend(rule.handler(ctx))

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
