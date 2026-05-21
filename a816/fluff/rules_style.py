"""Style rules: E501 (line length) + S001/S003/S004 (struct ergonomics)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from a816.fluff.core import (
    MAX_LINE_LENGTH,
    Diagnostic,
    LintContext,
    Rule,
    flatten_nodes,
)
from a816.parse.ast.nodes import (
    AssignAstNode,
    AstNode,
    CastAccessExprNode,
    CastValueExprNode,
    DataNode,
    ExpressionAstNode,
    ExprNode,
    ImportAstNode,
    IncludeIpsAstNode,
    LabelDeclAstNode,
    OpcodeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.mzparser import A816Parser
from a816.stdlib import resolve_stdlib_module


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


def _expressions_in_node(node: AstNode) -> Iterable[ExpressionAstNode]:
    """Yield every ExpressionAstNode reachable from `node` without descending
    into child blocks (the flat-node walker visits those separately)."""
    match node:
        case AssignAstNode() | SymbolAffectationAstNode() | LabelDeclAstNode():
            if isinstance(node.value, ExpressionAstNode):
                yield node.value
        case OpcodeAstNode():
            if node.operand is not None:
                yield node.operand
        case DataNode():
            for expr in node.data:
                if isinstance(expr, ExpressionAstNode):
                    yield expr
        case IncludeIpsAstNode():
            if isinstance(node.expression, ExpressionAstNode):
                yield node.expression


def _walk_cast_nodes(
    tokens: list[ExprNode],
) -> Iterable[CastAccessExprNode | CastValueExprNode]:
    """Yield Cast* terms in `tokens`, recursing into their inner expressions."""
    for tok in tokens:
        if isinstance(tok, CastAccessExprNode | CastValueExprNode):
            yield tok
            yield from _walk_cast_nodes(tok.inner)


def _collect_known_struct_types(ctx: LintContext) -> set[str]:
    """Struct types declared in this file plus those reachable via imports.

    Local `.struct` decls are always available. `.import` targets are
    resolved through the stdlib + module-path search order; their files
    get parsed once per lint and the discovered struct names cached on
    the context.
    """
    known = {n.name for n in ctx.flat_nodes if isinstance(n, StructAstNode)}
    known |= _collect_imported_struct_types(ctx)
    return known


def _collect_imported_struct_types(ctx: LintContext) -> set[str]:
    if ctx._imported_struct_types is not None:  # noqa: SLF001 — cache on ctx
        return ctx._imported_struct_types
    discovered: set[str] = set()
    seen_paths: set[str] = set()
    for node in ctx.flat_nodes:
        if isinstance(node, ImportAstNode):
            module_path = _resolve_import_for_lint(node.module_name, ctx)
            if module_path is not None:
                discovered |= _struct_names_in_file(module_path, seen_paths)
    ctx._imported_struct_types = discovered  # noqa: SLF001
    return discovered


def _resolve_import_for_lint(module_name: str, ctx: LintContext) -> Path | None:
    """Map a `.import` module name to an absolute path.

    Tries the stdlib `@std/...` mapping first, then the lint context's
    configured `module_paths`, then the document's own directory.
    """
    stdlib_path = resolve_stdlib_module(module_name, ".s")
    if stdlib_path is not None:
        return stdlib_path
    candidates: list[Path] = []
    if ctx.module_paths:
        candidates.extend(ctx.module_paths)
    candidates.append(ctx.path.parent)
    file_name = module_name + ".s"
    for base in candidates:
        candidate = base / file_name
        if candidate.exists():
            return candidate
    return None


def _struct_names_in_file(path: Path, seen_paths: set[str]) -> set[str]:
    """Parse `path` (recursive cycle-safe) and return every declared struct name."""
    canonical = str(path.resolve())
    if canonical in seen_paths:
        return set()
    seen_paths.add(canonical)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    result = A816Parser.parse_as_ast(text, str(path))
    names: set[str] = set()
    for node in flatten_nodes(list(result.nodes)):
        if isinstance(node, StructAstNode):
            names.add(node.name)
        elif isinstance(node, ImportAstNode):
            transitive = resolve_stdlib_module(node.module_name, ".s")
            if transitive is not None:
                names |= _struct_names_in_file(transitive, seen_paths)
    return names


def _collect_typed_instances(ctx: LintContext) -> dict[str, str]:
    """Map instance name → struct type for every `p := (... as T)` bind."""
    instances: dict[str, str] = {}
    for node in ctx.flat_nodes:
        if not isinstance(node, AssignAstNode) or not isinstance(node.value, ExpressionAstNode):
            continue
        tokens = node.value.tokens
        if len(tokens) == 1 and isinstance(tokens[0], CastValueExprNode):
            instances[node.symbol] = tokens[0].type_name
    return instances


def _inner_canonical(tokens: list[ExprNode]) -> str:
    return " ".join(tok.to_canonical() for tok in tokens)


def _iter_casts(
    ctx: LintContext,
) -> Iterable[tuple[AstNode, CastAccessExprNode | CastValueExprNode]]:
    """Yield `(parent_node, cast)` pairs for every cast in the program."""
    for parent in ctx.flat_nodes:
        for expr in _expressions_in_node(parent):
            for cast in _walk_cast_nodes(expr.tokens):
                yield parent, cast


def _single_identifier_name(cast: CastAccessExprNode | CastValueExprNode) -> str | None:
    """Return the bare identifier `cast` wraps, or None when it wraps a richer expression."""
    if len(cast.inner) != 1:
        return None
    inner = cast.inner[0]
    if not hasattr(inner, "token"):
        return None
    return inner.token.value


def _is_typed_bind_owner(
    parent: AstNode,
    cast: CastAccessExprNode | CastValueExprNode,
    instances: dict[str, str],
) -> bool:
    """True when `cast` is the very RHS of `parent`'s typed bind — counting that
    line against the user would penalize the `:=` we want them to write.
    """
    return isinstance(parent, AssignAstNode) and instances.get(parent.symbol) == cast.type_name


class UnknownStructTypeCast(Rule):
    code = "S001"
    description = "cast references an undeclared struct type"
    rationale = (
        "`(expr as T).field` and `p := (expr as T)` resolve `T` against the "
        "structs declared in the current translation unit. Casting to a "
        "type the file doesn't know about is almost always a typo — and "
        "would otherwise blow up at codegen instead of lint time."
    )
    bad = '"""Module."""\nlda.w (0x2100 as Unknown).x\n'
    good = '"""Module."""\n.struct PPU {\n    word OAMADDR\n}\nlda.w (0x2100 as PPU).OAMADDR\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        known = _collect_known_struct_types(ctx)
        for parent, cast in _iter_casts(ctx):
            if cast.type_name not in known:
                yield self.diagnose(
                    ctx,
                    parent,
                    f"cast targets unknown struct type '{cast.type_name}'",
                )


class RedundantTypedCast(Rule):
    code = "S003"
    description = "cast on an already-typed binding is redundant"
    rationale = (
        "When `p := (addr as Player)` is in scope, `p.field` already "
        "resolves through the typed binding. Re-casting `(p as Player)` "
        "adds noise and signals confusion about which binding is in "
        "effect."
    )
    bad = '"""Module."""\n.struct Pt {\n    word x\n}\np := (0x100 as Pt)\nlda.w (p as Pt).x\n'
    good = '"""Module."""\n.struct Pt {\n    word x\n}\np := (0x100 as Pt)\nlda.w p.x\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        instances = _collect_typed_instances(ctx)
        for parent, cast in _iter_casts(ctx):
            name = _single_identifier_name(cast)
            if name is None:
                continue
            if instances.get(name) == cast.type_name:
                yield self.diagnose(
                    ctx,
                    parent,
                    f"redundant cast: '{name}' is already bound as '{cast.type_name}'",
                )


class RepeatedInlineCast(Rule):
    code = "S004"
    description = "same cast expression used more than once — prefer `:=`"
    rationale = (
        "Repeating `(expr as T)` in several operands is a sign the "
        "address deserves a typed binding. `p := (expr as T)` lets the "
        "subsequent code say `p.field` and keeps the address change "
        "scoped to a single line if it ever moves."
    )
    bad = '"""Module."""\n.struct Pt {\n    word x\n    word y\n}\nlda.w (0x100 as Pt).x\nlda.w (0x100 as Pt).y\n'
    good = '"""Module."""\n.struct Pt {\n    word x\n    word y\n}\np := (0x100 as Pt)\nlda.w p.x\nlda.w p.y\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        instances = _collect_typed_instances(ctx)
        seen: dict[tuple[str, str], list[AstNode]] = {}
        for parent, cast in _iter_casts(ctx):
            if _is_typed_bind_owner(parent, cast, instances):
                continue
            sig = (_inner_canonical(cast.inner), cast.type_name)
            seen.setdefault(sig, []).append(parent)
        for (inner_str, type_name), parents in seen.items():
            if len(parents) < 2:
                continue
            message = f"cast `({inner_str} as {type_name})` repeated {len(parents)}× — prefer typed bind"
            for parent in parents:
                yield self.diagnose(ctx, parent, message)
