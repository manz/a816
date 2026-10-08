"""The names `.incbin` derives from its file path, and the references to them.

`.incbin "assets/vwf.bin"` binds `assets_vwf_bin` (where the bytes start)
and `assets_vwf_bin__size`. Those names follow the asset's path, so moving
or renaming a file renames symbols in code that never mentions the file.
They are deprecated (W0001): the blob's alloc names it, `NAME` and
`sizeof(NAME)`, or a label does.

The build (`code_gen`) and `a816 check` share this walk, so both warn on
the same references.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from a816.parse.ast.expression import identifier_tokens
from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    CommentAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
)
from a816.parse.tokens import Token

SIZE_SUFFIX = "__size"


def incbin_symbol_base(file_path: str) -> str:
    """`assets/vwf.bin` -> `assets_vwf_bin`, the label `.incbin` binds."""
    return file_path.replace("/", "_").replace(".", "_")


@dataclass(frozen=True)
class PathName:
    """One path-derived name and what to write instead, when there is one."""

    name: str
    file_path: str
    replacement: str | None
    # Bound by more than one `.incbin` in the unit: a reference would read
    # whichever came first, so it's E0347 rather than a warning.
    ambiguous: bool = False

    @property
    def is_size(self) -> bool:
        return self.name.endswith(SIZE_SUFFIX)

    def hint(self) -> str:
        if self.ambiguous:
            return "two `.incbin`s bind it, and it names whichever came first: label each blob"
        if self.replacement is not None:
            return f"write `{self.replacement}`; `a816 fix` rewrites it"
        if self.is_size:
            return f'move `.incbin "{self.file_path}"` into its own named `.alloc NAME` and use `sizeof(NAME)`'
        return f'put a label before `.incbin "{self.file_path}"` and use it'


def path_names(nodes: Sequence[AstNode]) -> dict[str, PathName]:
    """Every path-derived name the `.incbin`s in `nodes` bind, includes followed."""
    names: dict[str, PathName] = {}
    for incbin, alloc_name in _incbins(nodes, None):
        base = incbin_symbol_base(incbin.file_path)
        size = f"sizeof({alloc_name})" if alloc_name is not None else None
        for name, replacement in ((base, alloc_name), (base + SIZE_SUFFIX, size)):
            if name in names:
                names[name] = PathName(name, names[name].file_path, None, ambiguous=True)
            else:
                names[name] = PathName(name, incbin.file_path, replacement)
    return names


def references(nodes: Sequence[AstNode], names: dict[str, PathName]) -> Iterator[tuple[Token, PathName]]:
    """Each identifier in `nodes` that names one of `names`, in source order.

    Included files are not entered: their references are reported where
    they are written, once, by whoever checks that file.
    """
    if not names:
        return
    for expression in _expressions(nodes):
        for token in identifier_tokens(expression.tokens):
            found = names.get(token.value)
            if found is not None:
                yield token, found


def _incbins(nodes: Sequence[AstNode], alloc_name: str | None) -> Iterator[tuple[IncludeBinaryAstNode, str | None]]:
    """(incbin, name of the alloc it alone fills, if it does) for every `.incbin`."""
    for node in nodes:
        if isinstance(node, IncludeBinaryAstNode):
            yield node, alloc_name
        elif isinstance(node, AllocAstNode):
            body = list(node.body.body)
            alone = node.name is not None and _only_incbin(body)
            yield from _incbins(body, node.name if alone else None)
        elif isinstance(node, IncludeAstNode):
            yield from _incbins(list(node.included_nodes or ()), None)
        else:
            yield from _incbins(list(_child_nodes(node)), None)


def _only_incbin(body: list[AstNode]) -> bool:
    content = [node for node in body if not isinstance(node, CommentAstNode | DocstringAstNode)]
    return len(content) == 1 and isinstance(content[0], IncludeBinaryAstNode)


def _expressions(nodes: Sequence[AstNode]) -> Iterator[ExpressionAstNode]:
    for node in nodes:
        if isinstance(node, IncludeAstNode):
            continue
        yield from _node_expressions(node)


def _node_expressions(node: AstNode) -> Iterator[ExpressionAstNode]:
    """Every expression in `node` and its children, whatever field holds it."""
    for value in vars(node).values():
        yield from _value_expressions(value)


def _value_expressions(value: object) -> Iterator[ExpressionAstNode]:
    if isinstance(value, ExpressionAstNode):
        yield value
    elif isinstance(value, IncludeAstNode):
        return
    elif isinstance(value, AstNode):
        yield from _node_expressions(value)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _value_expressions(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _value_expressions(item)


def _child_nodes(node: AstNode) -> Iterator[AstNode]:
    """The AST nodes held by `node`'s fields (bodies, branches), one level down."""
    for value in vars(node).values():
        yield from _value_nodes(value)


def _value_nodes(value: object) -> Iterator[AstNode]:
    if isinstance(value, AstNode) and not isinstance(value, ExpressionAstNode):
        yield value
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _value_nodes(item)
