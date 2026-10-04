"""Position / relocation / include-IPS / scope-push / scope-pop nodes."""

from __future__ import annotations

import struct

from a816.cpu.mapping import Address
from a816.error_codes import E_IO_FILE_NOT_FOUND, E_IO_NOT_IPS
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import ExpressionAstNode
from a816.parse.nodes.errors import NodeError
from a816.parse.tokens import Token
from a816.protocols import ValueNodeProtocol
from a816.symbols import Resolver


class CodePositionNode:
    def __init__(self, value_node: ValueNodeProtocol, resolver: Resolver, file_info: Token | None = None):
        self.value_node = value_node
        self.resolver: Resolver = resolver
        self.file_info = file_info

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.reloc = False
        return self.resolver.get_bus().get_address(self.value_node.get_value())

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.set_position(self.value_node.get_value())
        # A new placement starts a new routine: drop asserted A/X sizes.
        self.resolver.forget_register_sizes()
        return b""

    def __str__(self) -> str:
        return f"CodePositionNode({self.value_node.get_value()})"


class RelocationAddressNode:
    def __init__(self, pc_value_node: ValueNodeProtocol, resolver: Resolver, file_info: Token | None = None) -> None:
        self.pc_value_node = pc_value_node
        self.resolver = resolver
        self.file_info = file_info

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.reloc = True
        return self.resolver.get_bus().get_address(self.pc_value_node.get_value())

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.set_position(self.pc_value_node.get_value())
        return b""

    def __str__(self) -> str:
        return f"RelocationAddressNode({self.pc_value_node.get_value()})"


class IncludeIpsNode:
    def __init__(
        self,
        file_path: str,
        resolver: Resolver,
        delta_expression: ExpressionAstNode | None = None,
        file_info: Token | None = None,
    ) -> None:
        self.ips_file_path = file_path
        self.file_info = file_info
        self.delta = eval_expression(delta_expression, resolver) if delta_expression else 0
        self.blocks: list[tuple[int, bytes]] = []
        try:
            ips_file = open(self.ips_file_path, "rb")  # noqa: SIM115 - closed by the `with` below
        except OSError as exc:
            raise NodeError(
                f"cannot read IPS patch {self.ips_file_path!r}: {exc.strerror}",
                file_info,
                code=str(E_IO_FILE_NOT_FOUND),
            ) from exc
        with ips_file:
            if ips_file.read(5) != b"PATCH":
                raise NodeError(
                    f'{self.ips_file_path} is missing "PATCH" header',
                    file_info,
                    code=str(E_IO_NOT_IPS),
                )

            while ips_file.peek(3)[:3] != b"EOF":
                block_addr_bytes = struct.unpack(">BH", ips_file.read(3))
                block_addr = (block_addr_bytes[0] << 16) | block_addr_bytes[1]
                block_size_word = struct.unpack(">H", ips_file.read(2))
                block_size = block_size_word[0]
                block = ips_file.read(block_size)

                if self.delta is not None:
                    block_addr += self.delta

                self.blocks.append((block_addr, block))

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        return b""


class ScopeNode:
    """Enter the scope this node was created in.

    Captures `resolver.current_scope` at codegen time (when the scope
    is freshly pushed) so `pc_after` / `emit` can re-enter that exact
    scope by direct assignment instead of advancing a global cursor.
    Idempotent: callers can walk the same body any number of times
    (alloc body measure + bind, repeated resolve passes) without the
    cursor running off the end of `resolver.scopes`.
    """

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.parent_scope = self.resolver.current_scope.parent
        self.target_scope = self.resolver.current_scope

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.current_scope = self.target_scope
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.current_scope = self.target_scope
        return b""


class PopScopeNode:
    """Restore the parent scope captured at codegen time.

    Idempotent for the same reason as `ScopeNode`: direct assignment
    of a captured reference, not a parent-chain walk that errors when
    the chain is already at root. Also republishes / bubbles the
    leaving scope's exportable names into the parent on each call —
    bubble + publish are both `if name not in parent` / dict-union
    idempotent, so repeated walks (alloc body measure + bind, multi-
    pass resolve) don't multi-write, but late-bound labels (e.g. ones
    that only resolve at `pc_after` time inside an alloc body) still
    surface in the parent's dotted view.
    """

    def __init__(self, resolver: Resolver, *, exports: bool = False) -> None:
        self.resolver = resolver
        self.exports = exports
        self.leaving_scope = self.resolver.current_scope
        self.parent_scope = self.resolver.current_scope.parent

    def _apply(self) -> None:
        from a816.symbols import (
            NamedScope,
            _bubble_anon_exportables,
            _publish_named_dotted,
        )

        parent = self.parent_scope
        if parent is None:
            return
        scope = self.leaving_scope
        if not isinstance(scope, NamedScope) and (self.exports or isinstance(parent, NamedScope)):
            _bubble_anon_exportables(scope, parent)
        if self.exports and isinstance(scope, NamedScope):
            _publish_named_dotted(scope, parent)
        self.resolver.current_scope = parent

    def pc_after(self, current_pc: Address) -> Address:
        self._apply()
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        self._apply()
        return b""
