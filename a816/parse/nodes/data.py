"""Width-typed data emitters + register-size / binary / debug nodes."""

from __future__ import annotations

import re

from a816.cpu.mapping import Address
from a816.parse.ast.expression import eval_expression_str
from a816.parse.nodes.expr import ExpressionNode
from a816.protocols import NodeProtocol, ValueNodeProtocol
from a816.symbols import Resolver


class RegisterSizeNode(NodeProtocol):
    """Node for register size directives (.a8, .a16, .i8, .i16)"""

    def __init__(self, register: str, size: int, resolver: Resolver) -> None:
        self.register = register  # "a" for accumulator, "i" for index
        self.size = size  # 8 or 16
        self.resolver = resolver

    def emit(self, current_addr: Address) -> bytes:
        # Update resolver state during emission
        if self.register == "a":
            self.resolver.a_size = self.size
        else:
            self.resolver.i_size = self.size
        return b""

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc

    def __str__(self) -> str:
        return f"RegisterSizeNode({self.register}{self.size})"


class BinaryNode(NodeProtocol):
    def __init__(self, path: str, resolver: Resolver) -> None:
        with open(path, "rb") as binary_file:
            self.binary_content = binary_file.read()
        self.file_path = path
        self.symbol_base = path.replace("/", "_").replace(".", "_")
        self.resolver = resolver

    def emit(self, current_addr: Address) -> bytes:
        return self.binary_content

    def pc_after(self, current_pc: Address) -> Address:
        retval = current_pc + len(self.binary_content)
        self.resolver.current_scope.add_label(self.symbol_base, current_pc)
        self.resolver.current_scope.add_symbol(self.symbol_base + "__size", len(self.binary_content))
        return retval


class _SizedValueNode(NodeProtocol):
    """Emit `SIZE` little-endian bytes from a value node.

    Concrete subclasses set `SIZE` (1, 2, or 3). Deferred expressions
    on the value node register an expression relocation of the same
    width so the linker can fill it in once cross-module names resolve.
    """

    SIZE: int = 0

    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        value = self.value_node.get_value()
        if isinstance(self.value_node, ExpressionNode) and hasattr(self.value_node, "_deferred_expression"):
            resolver = self.value_node.resolver
            if resolver.context.is_object_mode and resolver.context.object_writer is not None:
                resolver.context.object_writer.add_expression_relocation(
                    resolver.context.object_writer.relocation_offset(),
                    self.value_node._deferred_expression,
                    self.SIZE,
                )
        mask = (1 << (8 * self.SIZE)) - 1
        return (value & mask).to_bytes(self.SIZE, "little")

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + self.SIZE


class LongNode(_SizedValueNode):
    SIZE = 3


class WordNode(_SizedValueNode):
    SIZE = 2


class ByteNode(_SizedValueNode):
    SIZE = 1


class DebugNode(NodeProtocol):
    def __init__(self, message: str, resolver: Resolver) -> None:
        self.message = message
        self.resolver = resolver

    def emit(self, current_address: Address) -> bytes:
        message = self.message
        matches = re.finditer(r"\{([^}]+)}", self.message)

        for match in matches:
            expression_str = match.group(1)

            _value = eval_expression_str(expression_str, self.resolver)

            match _value:
                case int():
                    value = hex(_value)
                case str():
                    value = _value

            message = message.replace(match.group(0), value)

        print(message)
        return b""

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc
