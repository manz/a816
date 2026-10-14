"""Goto-definition on struct members: `Type.field`, `thing.field` (a
`.reserve NAME as T`), `view.field` (a typed bind), `(expr as T).field`,
nested `Outer.head.field`, and a field named in an `.istruct` / `.patch`
initializer. Each lands on the field's own line in its `.struct`."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lsprotocol.types import Location, Position, Range

from a816.parse.ast.nodes import StructInstanceAstNode, StructPatchAstNode
from a816.parse.ast.visitor import walk

if TYPE_CHECKING:
    from a816.lsp.document import A816Document
    from a816.lsp.workspace import WorkspaceIndex
    from a816.parse.ast.nodes import StructInitAstNode

_CAST_TYPE_RE = re.compile(r"\bas\s+([A-Za-z_]\w*)\s*\)$")


def _is_name_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _name_start(line: str, end: int) -> int:
    start = end
    while start > 0 and _is_name_char(line[start - 1]):
        start -= 1
    return start


def _name_end(line: str, start: int) -> int:
    end = start
    while end < len(line) and _is_name_char(line[end]):
        end += 1
    return end


class DottedPath:
    """The `a.b.c` around a cursor: the segments before the one under it,
    that segment, and the struct type a cast in front of them names."""

    def __init__(self, line: str, column: int) -> None:
        self.start = _name_start(line, column)
        self.end = _name_end(line, column)
        self.name = line[self.start : self.end]
        path_start = self.start
        while path_start > 1 and line[path_start - 1] == "." and _is_name_char(line[path_start - 2]):
            path_start = _name_start(line, path_start - 1)
        self.parents = [part for part in line[path_start : self.start].split(".") if part]
        self.text = line[path_start : self.end]
        cast = None
        if path_start > 1 and line[path_start - 1] == ".":
            cast = _CAST_TYPE_RE.search(line[: path_start - 1])
        self.cast_type = cast.group(1) if cast else None


class MemberDefinitionMixin:
    """Mixed into the server: needs `documents`, `_ensure_workspace_index`,
    `_location_for` and hover's `_istruct_field_at`."""

    documents: dict[str, A816Document]

    def _ensure_workspace_index(self) -> WorkspaceIndex | None:
        raise NotImplementedError

    def _istruct_field_at(self, type_name: str, init: StructInitAstNode, line: int, column: int) -> str | None:
        raise NotImplementedError

    def _member_definition(self, doc: A816Document, line_num: int, column: int) -> Location | None:
        """The field a member reference names, or None when the cursor is on
        no member (a plain name, a path whose head holds no struct)."""
        key = self._initializer_member(doc, line_num, column) or self._path_member(doc, line_num, column)
        return self._member_location(doc, key) if key else None

    def _initializer_member(self, doc: A816Document, line_num: int, column: int) -> str | None:
        for node in walk(doc.ast_nodes):
            if isinstance(node, StructInstanceAstNode | StructPatchAstNode):
                found = self._istruct_field_at(node.type_name, node.init, line_num, column)
                if found is not None:
                    return found
        return None

    def _path_member(self, doc: A816Document, line_num: int, column: int) -> str | None:
        path = DottedPath(doc.lines[line_num], column)
        if not path.name:
            return None
        parents = list(path.parents)
        struct = path.cast_type
        if struct is None:
            if not parents:
                return None
            struct = self._member_type(doc, parents.pop(0))
        for parent in parents:
            if struct is None:
                return None
            struct = self._member_type(doc, f"{struct}.{parent}")
        return f"{struct}.{path.name}" if struct else None

    def _member_type(self, doc: A816Document, name: str) -> str | None:
        found = doc.member_types.get(name)
        if found is None:
            workspace = self._ensure_workspace_index()
            found = workspace.member_types.get(name) if workspace else None
        return found

    def _member_location(self, doc: A816Document, key: str) -> Location | None:
        entry = doc.symbols.get(key)
        if entry is None:
            workspace = self._ensure_workspace_index()
            entry = workspace.symbols.get(key) if workspace else None
        if entry is None:
            return None
        position, uri = entry
        length = len(key.rsplit(".", 1)[-1])
        end = Position(line=position.line, character=position.character + length)
        return Location(uri=uri, range=Range(start=position, end=end))

    def _dotted_definition(self, doc: A816Document, line_num: int, column: int) -> Location | None:
        """The whole dotted name (`sc.fd`, `Type.field.mask`) as one symbol."""
        text = DottedPath(doc.lines[line_num], column).text
        if "." not in text:
            return None
        return self._member_location(doc, text)
