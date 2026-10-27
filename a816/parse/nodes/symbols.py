"""Label / symbol / extern nodes."""

from __future__ import annotations

from a816.cpu.mapping import Address
from a816.error_codes import E_CODEGEN_BAD_LABEL_ADDRESS, E_SYMBOL_EXTERNAL_NOT_ALLOWED
from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference
from a816.link_expr import LinkExpr
from a816.object_file import LinkAssert
from a816.parse.ast.expression import deferred_link_expression, eval_expression, link_expression, resolve_link_names
from a816.parse.ast.nodes import BlockAstNode, ExpressionAstNode
from a816.parse.nodes.errors import NodeError
from a816.parse.tokens import Token
from a816.protocols import NodeBase
from a816.symbols import Resolver


class AssertNode(NodeBase):
    """An object build's `.assert`, at its place in the program. Once its
    scope is current again, it renames the labels it reads to their exported
    form, so an `.alloc` body's `_end` reaches the linker as the
    `__sc<idx>___end` the object exports. Labels defined after the assert
    get their name on a later walk; emit settles the final expression."""

    def __init__(self, check: LinkAssert, resolver: Resolver) -> None:
        self.check = check
        self.resolver = resolver
        self.source_expression = check.expression

    def _rename(self) -> None:
        self.check.expression = resolve_link_names(self.source_expression, self.resolver)

    def emit(self, current_addr: Address) -> bytes:
        self._rename()
        return b""

    def pc_after(self, current_pc: Address) -> Address:
        self._rename()
        return current_pc


class LabelNode(NodeBase):
    def __init__(self, symbol_name: str, resolver: Resolver) -> None:
        self.symbol_name = symbol_name
        self.resolver = resolver

    def emit(self, current_addr: Address) -> bytes:
        return b""

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.current_scope.add_label(self.symbol_name, current_pc)
        return current_pc

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return f"LabelNode({self.symbol_name})"


class LabelDeclNode(NodeBase):
    """`.label NAME = ADDR` — register NAME as a label at constant address ADDR.

    Position counter is untouched. The address binds via
    `scope.absolute_labels` (separate from `scope.labels` so the linker
    doesn't shift it by the module relocation delta) and lands in `.adbg`
    as `SymbolKind.LABEL`, so `lookup_label(addr)` resolves the name.
    The RHS must evaluate to an int at this resolution pass; external
    references are not supported (use `.extern` for that).
    """

    def __init__(
        self,
        symbol_name: str,
        expression: ExpressionAstNode,
        resolver: Resolver,
        file_info: Token,
    ) -> None:
        self.symbol_name = symbol_name
        self.expression = expression
        self.resolver = resolver
        self.file_info = file_info

    def emit(self, current_addr: Address) -> bytes:
        return b""

    def pc_after(self, current_pc: Address) -> Address:
        try:
            value = eval_expression(self.expression, self.resolver)
        except (ExternalExpressionReference, ExternalSymbolReference) as e:
            ref = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
            raise NodeError(
                f".label {self.symbol_name}: address must be a constant expression (got external reference '{ref}')",
                self.file_info,
                code=str(E_SYMBOL_EXTERNAL_NOT_ALLOWED),
            ) from e
        if not isinstance(value, int):
            raise NodeError(
                f".label {self.symbol_name}: address must evaluate to an int, got {type(value).__name__}",
                self.file_info,
                code=str(E_CODEGEN_BAD_LABEL_ADDRESS),
            )
        # The value is an absolute address the user supplied — not the
        # current PC. Record it under `absolute_labels` (separate from
        # `labels`) so the linker doesn't add the module's relocation delta
        # to it, and write it directly into `symbols` so resolution at
        # call sites returns the int. Skipping `add_symbol` avoids the
        # "Symbol already defined" warning on the second resolve pass.
        scope = self.resolver.current_scope
        scope.absolute_labels[self.symbol_name] = value
        scope.symbols[self.symbol_name] = value
        return current_pc

    def __str__(self) -> str:
        return f"LabelDeclNode({self.symbol_name}, {self.expression})"


class SymbolNode(NodeBase):
    def __init__(
        self,
        symbol_name: str,
        expression: ExpressionAstNode | BlockAstNode,
        resolver: Resolver,
    ) -> None:
        self.symbol_name = symbol_name
        self.expression = expression
        self.resolver = resolver

    def emit(self, current_addr: Address) -> bytes:
        return b""

    def _register_alias(self, expression: LinkExpr) -> None:
        self.resolver.register_external_alias(self.symbol_name, expression)

    def pc_after(self, current_pc: Address) -> Address:
        # SymbolNode emits no bytes; current_pc is returned unchanged. The
        # method exists to register the symbol's value (or alias) at resolution
        # time. Protocol contract requires returning the PC.
        assert isinstance(self.expression, ExpressionAstNode)
        try:
            value = eval_expression(self.expression, self.resolver)
        except (ExternalExpressionReference, ExternalSymbolReference) as e:
            if not self.resolver.context.is_object_mode:
                raise NodeError(
                    f"{self.symbol_name} = {self.expression.to_canonical()}: "
                    f"external symbols only allowed in object compilation mode.",
                    getattr(self.expression, "file_info", None),
                    code=str(E_SYMBOL_EXTERNAL_NOT_ALLOWED),
                ) from e
            self._register_alias(deferred_link_expression(e, self.resolver))
        else:
            if self.resolver.context.is_object_mode and self._references_local_label():
                # RHS hits a module-local CODE label. Register an alias so refs
                # go through the relocation pipeline; baked value is
                # module-base-relative.
                self._register_alias(link_expression(self.expression, self.resolver))
            else:
                self.resolver.current_scope.add_symbol(self.symbol_name, value)
        return current_pc

    def _references_local_label(self) -> bool:
        from a816.parse.ast.expression import identifier_tokens

        if not isinstance(self.expression, ExpressionAstNode):
            return False
        return any(
            self.resolver.current_scope.find_label_scope(tok.value) is not None
            for tok in identifier_tokens(self.expression.tokens)
        )

    def __str__(self) -> str:
        return f"SymbolNode({self.symbol_name}, {self.expression})"


class ExternNode(NodeBase):
    def __init__(self, symbol_name: str, resolver: Resolver) -> None:
        self.symbol_name = symbol_name
        self.resolver = resolver

    def emit(self, current_addr: Address) -> bytes:
        return b""

    def pc_after(self, current_pc: Address) -> Address:
        # Mark symbol as external in the current scope
        # Late import: intentional to avoid circular dependency with object_file module
        from a816.object_file import SymbolSection, SymbolType

        # Add external symbol to the resolver's scope
        self.resolver.current_scope.add_external_symbol(self.symbol_name)

        # Add external symbol to the object writer if we're in object compilation mode
        object_writer = self.resolver.context.object_writer
        if self.resolver.context.is_object_mode and object_writer is not None:
            object_writer.add_symbol(self.symbol_name, 0, SymbolType.EXTERNAL, SymbolSection.CODE)

        return current_pc

    def __str__(self) -> str:
        return f"ExternNode({self.symbol_name})"
