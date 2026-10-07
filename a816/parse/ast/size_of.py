"""`sizeof(...)` / `countof(...)`: sizes read from struct layouts and reservations.

A struct, a struct field or a reservation declared in this module (or a
typed one from an import) has a size known now, an `int`. A flat
reservation whose size isn't known yet stands for its `NAME.__size`
symbol, returned as a `str` for the caller to look up (an import's
resolves at link).
"""

from __future__ import annotations

import difflib

from a816.error_codes import E_CODEGEN_SIZE_OPERAND
from a816.exceptions import A816Error
from a816.parse.ast.nodes import SizeofExprNode
from a816.symbols import Resolver

_COUNT_HINT = "countof counts the elements of a `TYPE[N]` field; sizeof gives bytes"


def size_of(node: SizeofExprNode, resolver: Resolver) -> int | str:
    """The operator's value, or the `NAME.__size` symbol standing for it."""
    split = _split_struct(node.path, resolver)
    if node.kind == "countof":
        return _count(node, split, resolver)
    if split is not None:
        type_name, field = split
        return resolver.struct_sizes[type_name] if not field else _field_size(node, type_name, field, resolver)
    if node.path in resolver.reservation_sizes:
        size = resolver.reservation_sizes[node.path]
        return f"{node.path}.__size" if size is None else size
    raise _error(node, f"`sizeof({node.path})`: `{node.path}` is not a struct, struct field or reservation", resolver)


def _split_struct(path: str, resolver: Resolver) -> tuple[str, str] | None:
    """`(struct, field path)` for the longest struct name `path` starts with."""
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        type_name = ".".join(parts[:end])
        if type_name in resolver.struct_sizes:
            return type_name, ".".join(parts[end:])
    return None


def _field_size(node: SizeofExprNode, type_name: str, field: str, resolver: Resolver) -> int:
    if field in resolver.struct_bitfields.get(type_name, {}):
        raise _error(
            node,
            f"`sizeof({node.path})`: `{field}` is a bit field and has no byte size",
            resolver,
            hint=f"read it with `{node.path}.mask` / `{node.path}.shift`",
        )
    array_size = resolver.struct_array_sizes.get(type_name, {}).get(field)
    if array_size is not None:
        return array_size
    return _entry_width(node, type_name, field, resolver)


def _count(node: SizeofExprNode, split: tuple[str, str] | None, resolver: Resolver) -> int:
    if split is None or not split[1]:
        raise _error(node, f"`countof({node.path})` needs an array field, `Type.field`", resolver, _COUNT_HINT)
    type_name, field = split
    array_size = resolver.struct_array_sizes.get(type_name, {}).get(field)
    if array_size is None:
        _entry_width(node, type_name, field, resolver)  # unknown field: report it as such
        raise _error(node, f"`countof({node.path})`: `{field}` is not an array field", resolver, _COUNT_HINT)
    return array_size // _entry_width(node, type_name, field, resolver)


def _entry_width(node: SizeofExprNode, type_name: str, field: str, resolver: Resolver) -> int:
    """Byte width of one element of `type_name.field` (a whole nested struct for a struct field)."""
    layout = resolver.struct_layouts.get(type_name, [])
    for path, _offset, width in layout:
        if path == field:
            return width
    fields = [path for path, _offset, _width in layout]
    close = difflib.get_close_matches(field, fields, n=1)
    hint = f"did you mean `{type_name}.{close[0]}`?" if close else None
    raise _error(node, f"`{node.kind}({node.path})`: struct `{type_name}` has no field `{field}`", resolver, hint)


def _error(node: SizeofExprNode, message: str, resolver: Resolver, hint: str | None = None) -> A816Error:
    # Late import: `a816.parse.nodes` imports the expression module, which imports this one.
    from a816.parse.nodes.errors import NodeError

    if hint is None:
        names = [*resolver.struct_sizes, *resolver.reservation_sizes]
        close = difflib.get_close_matches(node.path, names, n=1)
        hint = f"did you mean `{close[0]}`?" if close else "sizeof takes a struct, a struct field or a reservation"
    return NodeError(message, node.path_token, code=str(E_CODEGEN_SIZE_OPERAND), hint=hint)
