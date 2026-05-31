"""Table / text / ascii / pointer / binary nodes."""

from __future__ import annotations

import re
import struct

from a816.cpu.mapping import Address
from a816.parse.nodes.errors import NodeError
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol, ValueNodeProtocol
from a816.symbols import Resolver
from script import Table


class TableNode(NodeProtocol):
    def __init__(self, path: str, resolver: Resolver) -> None:
        import os

        from a816.util import resolve_asset_path

        self.table_path = resolve_asset_path(path, resolver.context.include_paths)
        self.resolver = resolver
        resolver.dependency_files.add(os.path.abspath(self.table_path))
        resolver.current_scope.table = Table(self.table_path)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        return b""


class AbstractTextNode(NodeProtocol):
    def __init__(self, text: str, resolver: Resolver) -> None:
        self.text = text
        self.resolver = resolver

    @property
    def binary_text(self) -> bytes:
        raise NotImplementedError("You should implement binary_text property.")

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + len(self.binary_text)

    def emit(self, current_addr: Address) -> bytes:
        return self.binary_text


def variable_expansion(value: str, resolver: Resolver) -> str:
    if value is not None and isinstance(value, str):

        def replace_match(match: re.Match[str]) -> str:
            lookup = match.groups("lookup")[0]
            variable_value = resolver.current_scope.value_for(lookup)
            if variable_value is None:
                raise ValueError(f"Error while resolving variable {lookup} in {value} variable value is None.")
            # Convert to string for substitution
            if isinstance(variable_value, int):
                return str(variable_value)
            elif isinstance(variable_value, str):
                return variable_value
            else:
                # BlockAstNode - convert to string representation
                return str(variable_value)

        try:
            return re.sub(r"\${(?P<lookup>[^}]+)}", replace_match, value) or value
        except ValueError:
            return value


class TextNode(AbstractTextNode):
    def __init__(self, text: str, resolver: Resolver, file_info: Token) -> None:
        super().__init__(text, resolver)
        self.table = self.resolver.current_scope.get_table()
        self.file_info = file_info

    @property
    def binary_text(self) -> bytes:
        if self.table is None:
            raise NodeError(
                f"table_is_not_defined ({self}) is not defined in the current scope.",
                self.file_info,
            )
        return self.table.to_bytes(variable_expansion(self.text, self.resolver))


class AsciiNode(AbstractTextNode):
    @property
    def binary_text(self) -> bytes:
        return self.text.encode("ascii", errors="ignore")


class PointerNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 3

    def emit(self, current_addr: Address) -> bytes:
        value = self.value_node.get_value()
        return struct.pack("<HB", value & 0xFFFF, (value >> 16) & 0xFF)
