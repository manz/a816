"""Control-flow rules.

`W0002` flags an `.if` on a name the project defines nowhere. Such a name
reads as false (that is how `.if DEBUG` works without `-D DEBUG`), so a
deleted or misspelt flag silently drops what it gated: ff4 removed a config
flag and an `.if` on it kept building, its body gone.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from a816.fluff.core import Diagnostic, LintContext, Rule
from a816.fluff.project import walk_sources
from a816.parse.ast.expression import identifier_tokens
from a816.parse.ast.nodes import AstNode, ExpressionAstNode, IfAstNode, IncludeAstNode

# Names the assembler binds itself.
_BUILTINS = frozenset({"BUILD_DATE", "sizeof", "countof"})
_DEFINING_FIELDS = ("name", "symbol", "label", "pool_name")
# Project root -> (the files' (path, mtime) signature, the names they define).
_PROJECT_NAMES: dict[Path, tuple[tuple[tuple[str, int], ...], frozenset[str]]] = {}


class UndefinedIfName(Rule):
    code = "W0002"
    description = "`.if` on a name the project defines nowhere"
    rationale = (
        "An undefined name reads as false in an `.if`, which is how `.if DEBUG` works without "
        "`-D DEBUG`. It also means a deleted or misspelt flag silently drops the code it gated. "
        "Declare a name that only comes from `-D` under `[defines]` in `a816.toml`."
    )
    bad = '"""Module."""\n.if DEBUG_TYPO {\n    .db 1\n}\n'
    good = '"""Module."""\nDEBUG = 0\n.if DEBUG {\n    .db 1\n}\n'

    def check(self, ctx: LintContext) -> Iterable[Diagnostic]:
        assert ctx.nodes is not None
        defined = _project_names(ctx.path) | _names_in(ctx.nodes)
        for node in _ifs(ctx.nodes):
            for token in identifier_tokens(node.expression.tokens):
                name = token.value
                if name in _BUILTINS or name in defined or name.split(".")[0] in defined:
                    continue
                position = token.position
                assert position is not None
                yield Diagnostic(
                    path=ctx.path,
                    line=position.line + 1,
                    column=position.column + 1,
                    code=self.code,
                    message=(
                        f"`.if` on `{name}`, which the project defines nowhere: it reads as false; "
                        "if it only comes from `-D`, declare it under `[defines]` in a816.toml"
                    ),
                )


def _ifs(nodes: Iterable[AstNode]) -> Iterator[IfAstNode]:
    """Every `.if` in this file, nested ones included (not in included files:
    they are checked on their own)."""
    for node in nodes:
        if isinstance(node, IncludeAstNode):
            continue
        if isinstance(node, IfAstNode):
            yield node
        yield from _ifs(_children(node))


def _children(node: AstNode) -> Iterator[AstNode]:
    for value in vars(node).values():
        items = value if isinstance(value, list | tuple) else (value,)
        for item in items:
            if isinstance(item, AstNode) and not isinstance(item, ExpressionAstNode):
                yield item


def _names_in(nodes: Iterable[AstNode]) -> set[str]:
    """Every name `nodes` bind: constants, labels, allocs, reservations, pools,
    structs, scopes, macros and their parameters, `.for` variables, externs,
    and the names `.incbin` derives from its path."""
    from a816.incbin_names import path_names

    names: set[str] = set()
    stack = list(nodes)
    while stack:
        node = stack.pop()
        for field_name in _DEFINING_FIELDS:
            value = getattr(node, field_name, None)
            if isinstance(value, str):
                names.add(value)
        names.update(getattr(node, "args", None) or ())
        stack.extend(_children(node))
    names.update(path_names(list(nodes)))
    return names


def _project_names(path: Path) -> frozenset[str]:
    """Names defined in any source of the project `path` belongs to (its
    `a816.toml` root, else its directory) or declared in its `[defines]`,
    cached until a file changes."""
    from a816.config import find_a816_toml
    from a816.parse.mzparser import A816Parser

    toml = find_a816_toml(path.parent)
    # Without an `a816.toml` there's no project to scan: the file's own
    # directory, not below it (a stray path must not walk a whole tree).
    root = (toml.parent if toml is not None else path.parent).resolve()
    sources = sorted(walk_sources(root, recursive=toml is not None)) + ([toml] if toml is not None else [])
    signature = tuple((str(source), source.stat().st_mtime_ns) for source in sources)
    cached = _PROJECT_NAMES.get(root)
    if cached is not None and cached[0] == signature:
        return cached[1]
    names = set(_declared_defines(toml))
    for source in sources:
        if source == toml:
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        names |= _names_in(A816Parser.parse_as_ast(text, str(source)).nodes)
    _PROJECT_NAMES[root] = (signature, frozenset(names))
    return _PROJECT_NAMES[root][1]


def _declared_defines(toml: Path | None) -> list[str]:
    """`[defines]` names; a config the build would reject declares none here
    (the build reports it)."""
    from a816.config import load_a816_toml
    from a816.exceptions import A816ConfigError

    if toml is None:
        return []
    try:
        config = load_a816_toml(toml)
    except A816ConfigError:
        return []
    return list(config.defines) if config is not None else []
