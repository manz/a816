import logging
import re
import struct

from a816.cpu.cpu_65c816 import (
    NoOpcodeForOperandSize,
    guess_value_size,
    snes_opcode_table,
)
from a816.cpu.mapping import Address
from a816.cpu.types import AddressingMode, ValueSize
from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference, SymbolNotDefined
from a816.object_file import Region
from a816.parse.ast.expression import eval_expression, eval_expression_str
from a816.parse.ast.nodes import BlockAstNode, ExpressionAstNode
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol, OpcodeProtocol, ValueNodeProtocol
from a816.symbols import Resolver, Scope
from script import Table

logger = logging.getLogger("a816.nodes")


class ValueNode(ValueNodeProtocol):
    def __init__(self, value: str) -> None:
        self.value = value

    def get_value(self) -> int:
        return int(self.value, 16)

    def get_value_string_len(self) -> int:
        value_length = len(self.value)
        return value_length

    def __str__(self) -> str:
        return f"ValueNode({self.value})"


class ExpressionNode(ValueNodeProtocol):
    def __init__(self, expression: ExpressionAstNode, resolver: Resolver, file_info: Token) -> None:
        self.expression = expression
        self.resolver = resolver
        self.file_info = file_info

    def _compute_local_label_renames(self) -> tuple[dict[str, str], bool]:
        """Return (rename map, touches_any_label). Nested-scope label refs get mangled."""
        from a816.parse.tokens import TokenType

        rename: dict[str, str] = {}
        touches_label = False
        for t in self.expression.tokens:
            tok = getattr(t, "token", None)
            if tok is None or tok.type != TokenType.IDENTIFIER:
                continue
            owner = self.resolver.current_scope.find_label_scope(tok.value)
            if owner is None:
                continue
            touches_label = True
            if owner is self.resolver.scopes[0]:
                continue  # root labels keep their plain name; only nested-scope refs need mangling
            scope_idx = self.resolver.scopes.index(owner)
            rename[tok.value] = f"__sc{scope_idx}__{tok.value}"
        return rename, touches_label

    def _record_local_label_relocation(self) -> None:
        from a816.parse.ast.expression import _inline_aliases, reconstruct_expression

        rename, touches_label = self._compute_local_label_renames()
        if not touches_label:
            return
        expr_str = _inline_aliases(reconstruct_expression(self.expression), self.resolver)
        for short, mangled in rename.items():
            expr_str = re.sub(rf"\b{re.escape(short)}\b", mangled, expr_str)
        self._deferred_expression = expr_str
        self._local_label_renames = rename

    def get_value(self) -> int | str:  # type:ignore
        try:
            value = eval_expression(self.expression, self.resolver)
            if self.resolver.context.is_object_mode and isinstance(value, int):
                # Module-local label refs: record the original expression so the
                # linker can re-evaluate against the module's final placement.
                self._record_local_label_relocation()
            return value
        except ExternalExpressionReference as e:
            if self.resolver.context.is_object_mode:
                self._deferred_expression = e.expression_str
                self._external_symbols = e.external_symbols
                return 0
            raise NodeError(f"Expression contains external symbols: {e.expression_str}", self.file_info) from e
        except ExternalSymbolReference as e:
            if self.resolver.context.is_object_mode:
                self._deferred_expression = self.expression.to_representation()[0]
                return 0
            raise NodeError(f"{e} ({self}) is not defined in the current scope.", self.file_info) from e
        except SymbolNotDefined as e:
            raise NodeError(f"{e} ({self}) is not defined in the current scope.", self.file_info) from e

    def get_value_string_len(self) -> int:
        value = self.get_value()
        if not isinstance(value, int):
            raise TypeError(f"Expected int, got {type(value).__name__}")
        return len(hex(value)) - 2

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.expression.to_representation()[0]})"


class LabelNode(NodeProtocol):
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


class SymbolNode(NodeProtocol):
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

    def _register_alias(self, expr_str: str) -> None:
        self.resolver.current_scope.add_external_alias(self.symbol_name, expr_str)
        object_writer = self.resolver.context.object_writer
        if object_writer is not None:
            object_writer.add_alias(self.symbol_name, expr_str)

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
                    self.expression.file_info if hasattr(self.expression, "file_info") else current_pc,  # type: ignore[arg-type]
                ) from e
            self._register_alias(e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str)
        else:
            if self.resolver.context.is_object_mode and self._references_local_label():
                # RHS hits a module-local CODE label. Register an alias so refs
                # go through the relocation pipeline; baked value is
                # module-base-relative.
                from a816.parse.ast.expression import _inline_aliases, reconstruct_expression

                self._register_alias(_inline_aliases(reconstruct_expression(self.expression), self.resolver))
            else:
                self.resolver.current_scope.add_symbol(self.symbol_name, value)
        return current_pc

    def _references_local_label(self) -> bool:
        from a816.parse.tokens import TokenType

        if not isinstance(self.expression, ExpressionAstNode):
            return False
        for term in self.expression.tokens:
            tok = getattr(term, "token", None)
            if tok is None or tok.type != TokenType.IDENTIFIER:
                continue
            if self.resolver.current_scope.find_label_scope(tok.value) is not None:
                return True
        return False

    def __str__(self) -> str:
        return f"SymbolNode({self.symbol_name}, {self.expression})"


class ExternNode(NodeProtocol):
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


class LinkedModuleNode(NodeProtocol):
    """Emits a compiled module's regions and binds its symbols.

    Each region has a compile-time `base_address` (from `*=`) and a code
    blob. If the module is `relocatable` (no `*=` was present), region 0
    gets shifted by `delta = import_pc - regions[0].base_address` and
    every CODE symbol moves with it. Otherwise regions land at their
    declared absolute addresses, ignoring the import site.
    """

    def __init__(
        self,
        module_name: str,
        regions: list[Region],
        symbols: list[tuple[str, int, int, int]],  # (name, address, type, section)
        resolver: Resolver,
        relocatable: bool = True,
    ) -> None:
        self.module_name = module_name
        self.regions = regions
        self.symbols = symbols
        self.resolver = resolver
        self.relocatable = relocatable
        # Set by Program._mark_import_winners: True for every duplicate
        # `.import` of the same module except the last occurrence in
        # program order. Losers bind symbols (so the loser's source can
        # still reference them) but do NOT advance the PC and do NOT
        # emit bytes — the winner is the canonical placement.
        self.is_loser: bool = False
        self._delta = 0
        # Cache placed regions for emit_blocks; refreshed on every pc_after.
        self._placed: list[tuple[int, bytes]] = []

    def emit(self, current_addr: Address) -> bytes:
        # Single-region modules still flow through the legacy single-bytes
        # path used by writers that don't know about emit_blocks.
        del current_addr
        placed = self._compute_placement()
        if not placed:
            return b""
        if len(placed) == 1:
            return placed[0][1]
        # Multi-region modules must go through emit_blocks; returning the
        # concatenation here would silently corrupt the output.
        return b""

    def emit_blocks(self, current_addr: Address) -> list[tuple[int, bytes]]:
        del current_addr
        return self._compute_placement()

    def pc_after(self, current_pc: Address) -> Address:
        from a816.object_file import SymbolSection, SymbolType

        if not self.regions:
            return current_pc

        scope = self.resolver.current_scope
        if self.relocatable:
            self._delta = current_pc.logical_value - self.regions[0].base_address
        else:
            self._delta = 0

        # Shifted base of region 0 — used for the .sym/.adbg producer to
        # report where the module actually landed.
        self.base_address = self.regions[0].base_address + self._delta
        self._local_map: dict[str, int] = {}

        for name, address, sym_type, section in self.symbols:
            final = self._resolve_symbol_address(address, section)
            if sym_type == SymbolType.GLOBAL.value:
                scope.symbols[name] = final
                if section == SymbolSection.CODE.value:
                    scope.labels[name] = final
            elif sym_type == SymbolType.LOCAL.value:
                self._local_map[name] = final

        # Loser duplicates publish symbols (winner overwrites later via
        # last-pass) but must not consume PC space — otherwise inline
        # source surrounding the loser .import shifts forward by the
        # module's size and lands on top of unrelated ROM.
        if self.is_loser:
            return current_pc

        # Pinned modules (any explicit `*=`) land at their declared
        # absolute base addresses; the importer's PC stays where it was
        # because the module does not occupy linear space at the import
        # site. Only relocatable single-region modules advance the
        # importer's PC by their first-region size.
        if not self.relocatable:
            return current_pc

        first = self.regions[0]
        first_end = first.base_address + self._delta + len(first.code)
        return self.resolver.get_bus().get_address(first_end)

    def _resolve_symbol_address(self, address: int, section: int) -> int:
        from a816.object_file import SymbolSection

        if section != SymbolSection.CODE.value:
            return address
        return address + self._delta

    def _compute_placement(self) -> list[tuple[int, bytes]]:
        # Re-evaluate every call: cross-module symbols may have been bound
        # by other LinkedModuleNodes after this one's pc_after ran. Doing
        # the eval lazily at emit time avoids spurious "Failed to
        # evaluate" warnings during the first resolve_labels pass.
        placed: list[tuple[int, bytes]] = []
        for region in self.regions:
            base = region.base_address + self._delta
            patched = self._apply_region_relocations(region)
            placed.append((base, patched))
        self._placed = placed
        return placed

    def _apply_region_relocations(self, region: Region) -> bytes:
        if not region.expression_relocations:
            return region.code
        code_array = bytearray(region.code)
        root_scope = self.resolver.scopes[0]
        saved = self._inject_locals(root_scope)
        try:
            for offset, expr, size in region.expression_relocations:
                self._eval_one_relocation(expr, offset, size, code_array, region)
        finally:
            self._restore_locals(root_scope, saved)
        return bytes(code_array)

    def _reloc_context(self, offset: int, region: Region) -> str:
        return (
            f"module '{self.module_name}' region@0x{region.base_address:x} offset 0x{offset:x}/0x{len(region.code):x}"
        )

    def _write_reloc(
        self, code_array: bytearray, offset: int, value: int, size: int, expr: str, region: Region
    ) -> None:
        ctx = self._reloc_context(offset, region)
        if size not in (1, 2, 3):
            logger.warning(f"Unsupported relocation size {size} for expression '{expr}' [{ctx}]")
            return
        if offset + size > len(code_array):
            logger.warning(
                f"Relocation runs past region code: offset 0x{offset:x} + size {size} "
                f"> 0x{len(code_array):x} for expression '{expr}' [{ctx}]"
            )
            return
        for i in range(size):
            code_array[offset + i] = (value >> (8 * i)) & 0xFF

    def _eval_one_relocation(self, expr: str, offset: int, size: int, code_array: bytearray, region: Region) -> None:
        from a816.parse.ast.expression import eval_expression_str

        ctx = self._reloc_context(offset, region)
        try:
            value = eval_expression_str(expr, self.resolver)
        except (SymbolNotDefined, NodeError, ValueError) as e:
            logger.warning(f"Failed to evaluate expression '{expr}': {e} [{ctx}]")
            return
        if not isinstance(value, int):
            logger.warning(f"Expression '{expr}' did not evaluate to int: {value} [{ctx}]")
            return
        self._write_reloc(code_array, offset, value, size, expr, region)

    def _inject_locals(self, root_scope: Scope) -> dict[str, int | str]:
        local_map = getattr(self, "_local_map", {})
        saved: dict[str, int | str] = {}
        for name, value in local_map.items():
            if name in root_scope.symbols:
                saved[name] = root_scope.symbols[name]
            root_scope.symbols[name] = value
        return saved

    def _restore_locals(self, root_scope: Scope, saved: dict[str, int | str]) -> None:
        local_map = getattr(self, "_local_map", {})
        for name in local_map:
            if name in saved:
                root_scope.symbols[name] = saved[name]
            else:
                root_scope.symbols.pop(name, None)

    def __str__(self) -> str:
        total = sum(len(r.code) for r in self.regions)
        return (
            f"LinkedModuleNode({self.module_name}, {len(self.regions)} regions, "
            f"{total} bytes, {len(self.symbols)} symbols)"
        )


class LongNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        value = self.value_node.get_value()
        # Check for deferred expression (external symbols)
        if isinstance(self.value_node, ExpressionNode) and hasattr(self.value_node, "_deferred_expression"):
            resolver = self.value_node.resolver
            if resolver.context.is_object_mode and resolver.context.object_writer is not None:
                resolver.context.object_writer.add_expression_relocation(
                    resolver.context.object_writer.relocation_offset(),
                    self.value_node._deferred_expression,
                    3,
                )
        return struct.pack("<HB", value & 0xFFFF, (value >> 16) & 0xFF)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 3


class WordNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        value = self.value_node.get_value()
        # Check for deferred expression (external symbols)
        if isinstance(self.value_node, ExpressionNode) and hasattr(self.value_node, "_deferred_expression"):
            resolver = self.value_node.resolver
            if resolver.context.is_object_mode and resolver.context.object_writer is not None:
                resolver.context.object_writer.add_expression_relocation(
                    resolver.context.object_writer.relocation_offset(),
                    self.value_node._deferred_expression,
                    2,
                )
        return struct.pack("<H", value & 0xFFFF)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 2


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


class ByteNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        value = self.value_node.get_value()
        # Check for deferred expression (external symbols)
        if isinstance(self.value_node, ExpressionNode) and hasattr(self.value_node, "_deferred_expression"):
            resolver = self.value_node.resolver
            if resolver.context.is_object_mode and resolver.context.object_writer is not None:
                resolver.context.object_writer.add_expression_relocation(
                    resolver.context.object_writer.relocation_offset(),
                    self.value_node._deferred_expression,
                    1,
                )
        return struct.pack("B", value & 0xFF)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 1


class UnknownOpcodeError(Exception):
    pass


class NodeError(Exception):
    def __init__(self, message: str, file_info: Token) -> None:
        super().__init__(message)
        self.file_info = file_info
        self.message = message

    def __str__(self) -> str:
        return self.format()

    def format(self) -> str:
        """Format the error with source location and visual indicator."""
        # Late import: intentional to avoid circular dependency with errors module
        from a816.errors import SourceLocation, format_error

        location = None
        if self.file_info is not None and self.file_info.position is not None:
            pos = self.file_info.position
            try:
                source_line = pos.get_line()
            except (IndexError, AttributeError):
                source_line = ""
            location = SourceLocation(
                filename=pos.file.filename,
                line=pos.line,
                column=pos.column,
                source_line=source_line,
                length=len(self.file_info.value) if self.file_info.value else 1,
            )

        return format_error(self.message, location)


class OpcodeNode(NodeProtocol):
    def __init__(
        self,
        opcode: str,
        *,
        size: ValueSize | None = None,
        addressing_mode: AddressingMode,
        index: str | None = None,
        value_node: ValueNodeProtocol | None = None,
        file_info: Token,
        resolver: Resolver,
    ) -> None:
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        self.size = size
        self.file_info = file_info
        self.resolver = resolver

    def _get_emitter(self) -> OpcodeProtocol:
        try:
            opcode_emitter = snes_opcode_table[self.opcode][self.addressing_mode]
        except KeyError as e:
            raise NodeError(
                f"Addressing mode ({self.addressing_mode.name}) for opcode_def ({self.opcode}) is not defined.",
                file_info=self.file_info,
            ) from e

        if isinstance(opcode_emitter, dict):
            if self.index is not None:
                opcode_emitter = opcode_emitter[self.index]
            else:
                raise NodeError(
                    f"Addressing mode ({self.addressing_mode.name}) for opcode_def ({self.opcode}) needs an index.",
                    file_info=self.file_info,
                )
        return opcode_emitter

    def emit(self, current_pc: Address) -> bytes:
        opcode_emitter = self._get_emitter()
        try:
            return opcode_emitter.emit(self.value_node, self.resolver, self.size)
        except NoOpcodeForOperandSize as e:
            assert self.value_node is not None
            guessed_size = guess_value_size(self.value_node, self.size)
            raise NodeError(
                f"{self.opcode} does not supports size ({guessed_size}).",
                self.file_info,
            ) from e
        except SymbolNotDefined as e:
            raise NodeError(
                f"{e} ({self.value_node}) is not defined in the current scope.",
                self.file_info,
            ) from e

    def pc_after(self, current_pc: Address) -> Address:
        opcode_emitter = self._get_emitter()
        return current_pc + opcode_emitter.supposed_length(self.value_node, self.size)

    def __str__(self) -> str:
        return f"OpcodeNode({self.opcode}, {self.addressing_mode}, {self.index}, {self.value_node})"


class CodePositionNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol, resolver: Resolver):
        self.value_node = value_node
        self.resolver: Resolver = resolver

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.reloc = False
        return self.resolver.get_bus().get_address(self.value_node.get_value())

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.set_position(self.value_node.get_value())
        return b""

    def __str__(self) -> str:
        return f"CodePositionNode({self.value_node.get_value()})"


class RelocationAddressNode(NodeProtocol):
    def __init__(self, pc_value_node: ValueNodeProtocol, resolver: Resolver) -> None:
        self.pc_value_node = pc_value_node
        self.resolver = resolver

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.reloc = True
        return self.resolver.get_bus().get_address(self.pc_value_node.get_value())

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.set_position(self.pc_value_node.get_value())
        # self.resolver.set_position(self.pc_value_node.get_value(), reloc=True)
        return b""

    def __str__(self) -> str:
        return f"RelocationAddressNode({self.pc_value_node.get_value()})"


class IncludeIpsNode(NodeProtocol):
    def __init__(
        self,
        file_path: str,
        resolver: Resolver,
        delta_expression: ExpressionAstNode | None = None,
    ) -> None:
        self.ips_file_path = file_path
        self.delta = eval_expression(delta_expression, resolver) if delta_expression else 0
        self.blocks: list[tuple[int, bytes]] = []
        with open(self.ips_file_path, "rb") as ips_file:
            if ips_file.read(5) != b"PATCH":
                raise RuntimeError(f'{self.ips_file_path} is missing "PATCH" header')

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


class ScopeNode(NodeProtocol):
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.parent_scope = self.resolver.current_scope

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.use_next_scope()
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.use_next_scope()
        return b""


class PopScopeNode(NodeProtocol):
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def pc_after(self, current_pc: Address) -> Address:
        self.resolver.restore_scope(exports=True)
        return current_pc

    def emit(self, current_addr: Address) -> bytes:
        self.resolver.restore_scope()
        return b""


class TableNode(NodeProtocol):
    def __init__(self, path: str, resolver: Resolver) -> None:
        self.table_path = path
        self.resolver = resolver
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
