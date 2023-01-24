import logging
import struct
from io import BufferedReader
from typing import List, Optional, Protocol, Tuple, Union, cast

from a816.cpu.cpu_65c816 import (
    AddressingMode,
    NoOpcodeForOperandSize,
    OpcodeProtocol,
    snes_opcode_table,
)
from a816.cpu.mapping import Address
from a816.exceptions import SymbolNotDefined
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import BlockAstNode, ExpressionAstNode
from a816.parse.tokens import Token
from a816.symbols import Resolver
from script import Table

logger = logging.getLogger("a816.nodes")


class ValueNodeProtocol(Protocol):
    def get_value(self) -> int:
        """Returns the value of the node as an int."""

    def get_value_string_len(self) -> int:
        """Returns the length in bytes of the value."""

    def get_operand_size(self) -> str:
        """Returns the operand size as b w l"""
        value_length = self.get_value_string_len()
        if value_length <= 2:
            retval = "b"
        elif value_length <= 4:
            retval = "w"
        else:
            retval = "l"

        return retval


class ValueNode(ValueNodeProtocol):
    def __init__(self, value: str) -> None:
        self.value = value

    def get_value(self) -> int:
        return int(self.value, 16)

    def get_value_string_len(self) -> int:
        value_length = len(self.value)
        return value_length

    def __str__(self) -> str:
        return "ValueNode(%s)" % self.value


class ExpressionNode(ValueNodeProtocol):
    def __init__(self, expression: ExpressionAstNode, resolver: Resolver, file_info: Token) -> None:
        self.expression = expression
        self.resolver = resolver
        self.file_info = file_info

    def get_value(self) -> int:
        try:
            return eval_expression(self.expression, self.resolver)
        except SymbolNotDefined as e:
            raise NodeError(f"{e} ({self}) is not defined in the current scope.", self.file_info)

    def get_value_string_len(self) -> int:
        return len(hex(self.get_value())) - 2

    def __str__(self) -> str:
        return "%s(%s)" % (self.__class__.__name__, self.expression.to_representation()[0])


class LabelNode:
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
        return "LabelNode(%s)" % self.symbol_name


class NodeProtocol(Protocol):
    def emit(self, current_addr: Address) -> bytes:
        """Emits the node as bytes"""

    def pc_after(self, current_pc: Address) -> Address:
        """Returns the program counter address after the node was emitted."""


class SymbolNode(NodeProtocol):
    def __init__(
        self, symbol_name: str, expression: Union[ExpressionAstNode, BlockAstNode], resolver: Resolver
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
        return "SymbolNode(%s, %s)" % (self.symbol_name, self.expression)


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


class LongNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        value = self.value_node.get_value()
        return struct.pack("<HB", value & 0xFFFF, (value >> 16) & 0xFF)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 3


class WordNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        return struct.pack("<H", self.value_node.get_value() & 0xFFFF)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 2


class ByteNode(NodeProtocol):
    def __init__(self, value_node: ValueNodeProtocol) -> None:
        self.value_node = value_node

    def emit(self, current_address: Address) -> bytes:
        return struct.pack("B", self.value_node.get_value() & 0xFF)

    def pc_after(self, current_pc: Address) -> Address:
        return current_pc + 1


class UnkownOpcodeError(Exception):
    pass


class NodeError(Exception):
    def __init__(self, message: str, file_info: Token) -> None:
        super().__init__(message)
        self.file_info = file_info
        self.message = message

    def __str__(self) -> str:
        error_message = '"{message}"'.format(message=self.message)
        if self.file_info is not None and self.file_info.position is not None:
            error_message += " at \n{file}:{line} {data}".format(
                file=self.file_info.position.file.filename,
                line=self.file_info.position.line,
                data=self.file_info.position.get_line(),
            )

        return error_message


class OpcodeNode(NodeProtocol):
    def __init__(
        self,
        opcode: str,
        *,
        size: Optional[str] = None,
        addressing_mode: AddressingMode,
        index: Optional[str] = None,
        value_node: Optional[ValueNodeProtocol] = None,
        file_info: Token,
        resolver: Resolver,
    ) -> None:
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        self.size = size.lower() if size else None
        self.file_info = file_info
        self.resolver = resolver

    def _get_emitter(self) -> OpcodeProtocol:
        try:
            opcode_emitter = snes_opcode_table[self.opcode][self.addressing_mode]
        except KeyError:
            raise NodeError(
                f"Addressing mode ({self.addressing_mode.name}) for opcode_def ({self.opcode}) is not defined.",
                file_info=self.file_info,
            )

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
            guessed_size = opcode_emitter.guess_value_size(self.value_node, self.size)
            raise NodeError(f"{self.opcode} does not supports size ({guessed_size}).", self.file_info) from e
        except SymbolNotDefined as e:
            raise NodeError(f"{e} ({self.value_node}) is not defined in the current scope.", self.file_info)

    def pc_after(self, current_pc: Address) -> Address:
        opcode_emitter = self._get_emitter()
        return current_pc + opcode_emitter.supposed_length(self.value_node, self.size)

    def __str__(self) -> str:
        return "OpcodeNode(%s, %s, %s, %s)" % (self.opcode, self.addressing_mode, self.index, self.value_node)


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
        return "CodePositionNode(%s)" % self.value_node.get_value()


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
        return "RelocationAddressNode(%s)" % self.pc_value_node.get_value()


class IncludeIpsNode(NodeProtocol):
    def __init__(
        self, file_path: str, resolver: Resolver, delta_expression: Optional[ExpressionAstNode] = None
    ) -> None:
        self.ips_file_path = file_path
        self.delta = eval_expression(delta_expression, resolver) if delta_expression else 0
        self.blocks: List[Tuple[int, bytes]] = []
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


class TextNode(AbstractTextNode):
    def __init__(self, text: str, resolver: Resolver, file_info: Token) -> None:
        super().__init__(text, resolver)
        self.table = self.resolver.current_scope.get_table()
        self.file_info = file_info

    @property
    def binary_text(self) -> bytes:
        if self.table is None:
            raise NodeError(f"table_is_not_defined ({self}) is not defined in the current scope.", self.file_info)
        return self.table.to_bytes(self.text)


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
