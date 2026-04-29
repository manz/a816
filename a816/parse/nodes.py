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
from a816.parse.ast.expression import eval_expression, eval_expression_str
from a816.parse.ast.nodes import BlockAstNode, ExpressionAstNode
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol, OpcodeProtocol, ValueNodeProtocol
from a816.symbols import Resolver
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

    def get_value(self) -> int | str:  # type:ignore
        try:
            return eval_expression(self.expression, self.resolver)
        except ExternalExpressionReference as e:
            # Expression contains external symbols, defer to link time
            if self.resolver.context.is_object_mode:
                # Store the deferred expression info for use by the caller
                self._deferred_expression = e.expression_str
                self._external_symbols = e.external_symbols
                return 0  # Placeholder value - caller will generate expression relocation
            else:
                # Not compiling to object file, can't resolve external symbols
                raise NodeError(f"Expression contains external symbols: {e.expression_str}", self.file_info) from e
        except ExternalSymbolReference as e:
            # Single external symbol reference
            if self.resolver.context.is_object_mode:
                # Store the expression for later evaluation at link time
                expression_str = self.expression.to_representation()[0]  # Get string representation
                self._deferred_expression = expression_str
                return 0  # Placeholder value
            else:
                # Not compiling to object file, can't resolve external symbol
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

    def pc_after(self, current_pc: Address) -> Address:
        assert isinstance(self.expression, ExpressionAstNode)
        value = eval_expression(self.expression, self.resolver)
        self.resolver.current_scope.add_symbol(self.symbol_name, value)
        return current_pc

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
    """Node that emits code from a compiled module and binds its symbols.

    This is used by .import for position-dependent code (like ROM patches).
    It loads the module's compiled code and adjusts symbol addresses based
    on the current PC position.
    """

    def __init__(
        self,
        module_name: str,
        code: bytes,
        symbols: list[tuple[str, int, int, int]],  # (name, offset, type, section)
        resolver: Resolver,
        expression_relocations: list[tuple[int, str, int]] | None = None,  # (offset, expr, size)
    ) -> None:
        self.module_name = module_name
        self.code = code
        self.symbols = symbols
        self.resolver = resolver
        self.expression_relocations = expression_relocations or []
        self._symbols_bound = False
        self._patched_code: bytes | None = None

    def emit(self, current_addr: Address) -> bytes:
        # Apply expression relocations during emit when all symbols are resolved
        if self._patched_code is None and self.expression_relocations:
            self._patched_code = self._apply_expression_relocations()

        if self._patched_code is not None:
            return self._patched_code
        return self.code

    def pc_after(self, current_pc: Address) -> Address:
        # Bind symbols with addresses adjusted for current position
        if not self._symbols_bound:
            from a816.object_file import SymbolSection, SymbolType

            base_address = current_pc.logical_value
            for name, offset, sym_type, section in self.symbols:
                if sym_type == SymbolType.GLOBAL.value:
                    if section == SymbolSection.CODE.value:
                        # CODE symbols: add base address to offset
                        symbol_address = base_address + offset
                        self.resolver.current_scope.add_symbol(name, symbol_address)
                    else:
                        # DATA symbols (constants): use raw value
                        self.resolver.current_scope.add_symbol(name, offset)
            self._symbols_bound = True

        return current_pc + len(self.code)

    def _apply_expression_relocations(self) -> bytes:
        """Apply expression relocations to the module's code."""
        from a816.parse.ast.expression import eval_expression_str

        code_array = bytearray(self.code)

        for offset, expr, size in self.expression_relocations:
            try:
                value = eval_expression_str(expr, self.resolver)
                if not isinstance(value, int):
                    logger.warning(f"Expression '{expr}' did not evaluate to int: {value}")
                    continue

                # Write the value at the offset with the specified size
                if size == 1:
                    code_array[offset] = value & 0xFF
                elif size == 2:
                    code_array[offset] = value & 0xFF
                    code_array[offset + 1] = (value >> 8) & 0xFF
                elif size == 3:
                    code_array[offset] = value & 0xFF
                    code_array[offset + 1] = (value >> 8) & 0xFF
                    code_array[offset + 2] = (value >> 16) & 0xFF
                else:
                    logger.warning(f"Unsupported relocation size {size} for expression '{expr}'")

            except Exception as e:
                logger.warning(f"Failed to evaluate expression '{expr}': {e}")

        return bytes(code_array)

    def __str__(self) -> str:
        return f"LinkedModuleNode({self.module_name}, {len(self.code)} bytes, {len(self.symbols)} symbols)"


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
                    resolver.pc, self.value_node._deferred_expression, 3
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
                    resolver.pc, self.value_node._deferred_expression, 2
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
                    resolver.pc, self.value_node._deferred_expression, 1
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

    def format(self, use_colors: bool = True) -> str:
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
