import logging
import os
import struct

from a816.exceptions import SymbolNotDefined
from a816.expressions import eval_expr
from a816.cpu.cpu_65c816 import snes_opcode_table, snes_to_rom, RelativeJumpOpcode, NoOpcodeForOperandSize
from a816.symbols import Resolver
from script import Table

logger = logging.getLogger('a816.nodes')


class ValueNode(object):
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return int(self.value, 16)

    def get_value_string_len(self):
        value_length = len(self.value)
        return value_length

    def get_operand_size(self):
        value_length = self.get_value_string_len()
        if value_length <= 2:
            retval = 'b'
        elif value_length <= 4:
            retval = 'w'
        else:
            retval = 'l'

        return retval

    def __str__(self):
        return 'ValueNode(%s)' % self.value


class ExpressionNode(object):
    def __init__(self, expression, resolver):
        self.expression = expression
        self.resolver = resolver

    def get_value(self):
        try:
            return eval_expr(self.expression, self.resolver)
        except KeyError:
            raise SyntaxError('The label %s is not defined.' % self.expression)

    def get_value_string_len(self):
        return len(hex(self.get_value())) - 2

    def get_operand_size(self):
        value_length = self.get_value_string_len()
        if value_length <= 2:
            retval = 'b'
        elif value_length <= 4:
            retval = 'w'
        else:
            retval = 'l'

        return retval

    def __str__(self):
        return '%s(%s)' % (self.__class__.__name__, self.expression)


class LabelNode(object):
    def __init__(self, symbol_name, resolver):
        self.symbol_name = symbol_name
        self.resolver = resolver

    def emit(self, current_addr):
        return []

    def pc_after(self, current_pc):
        self.resolver.current_scope.add_label(self.symbol_name, current_pc)
        return current_pc

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return 'LabelNode(%s)' % self.symbol_name


class SymbolNode(object):
    def __init__(self, symbol_name, expression, resolver):
        self.symbol_name = symbol_name
        self.expression = expression
        self.resolver = resolver

    def emit(self, current_addr):
        return []

    def pc_after(self, current_pc):
        value = eval_expr(self.expression, self.resolver)
        self.resolver.current_scope.add_symbol(self.symbol_name, value)
        return current_pc

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return 'SymbolNode(%s, %s)' % (self.symbol_name, self.expression)


class BinaryNode(object):
    def __init__(self, path, resolver):
        with open(path, 'rb') as binary_file:
            self.binary_content = binary_file.read()
        self.file_path = path
        self.symbol_base = path.replace('/', '_').replace('.', '_')
        self.resolver = resolver

    def emit(self, current_addr):
        return self.binary_content

    def pc_after(self, current_pc):
        retval = current_pc + len(self.binary_content)
        self.resolver.current_scope.add_label(self.symbol_base, current_pc)
        self.resolver.current_scope.add_symbol(self.symbol_base + '__size', len(self.binary_content))
        return retval


class LongNode(object):
    def __init__(self, value_node):
        self.value_node = value_node

    def emit(self, current_address):
        value = self.value_node.get_value()
        return struct.pack('<HB', value & 0xFFFF, (value >> 16) & 0xFF)

    def pc_after(self, current_pc):
        return current_pc + 3


class WordNode(object):
    def __init__(self, value_node):
        self.value_node = value_node

    def emit(self, current_address):
        return struct.pack('<H', self.value_node.get_value() & 0xffff)

    def pc_after(self, current_pc):
        return current_pc + 2


class ByteNode(object):
    def __init__(self, value_node):
        self.value_node = value_node

    def emit(self, current_address):
        return struct.pack('B', self.value_node.get_value() & 0xff)

    def pc_after(self, current_pc):
        return current_pc + 1


class UnkownOpcodeError(Exception):
    pass


class NodeError(Exception):
    def __init__(self, message: str, file_info: list) -> None:
        super().__init__(message)
        self.file_info = file_info
        self.message = message

    def __str__(self):
        return '"{message}" at \n{file}:{line} {data}'.format(
            message=self.message,
            file=self.file_info[0] if self.file_info[0] else 'stdin',
            line=self.file_info[1],
            data=self.file_info[2])


class OpcodeNode(object):
    def __init__(self, opcode, size=None, addressing_mode=None, index=None, value_node=None, file_info=None):
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        self.size = size.lower() if size else None
        self.file_info = file_info

    def check_opcode(self):
        emitter = self._get_emitter()
        if not isinstance(emitter, RelativeJumpOpcode):
            emitter.emit(self.value_node, self.size)

    def _get_emitter(self):
        try:
            opcode_emitter = snes_opcode_table[self.opcode][self.addressing_mode]
        except KeyError:
            raise NodeError('Addressing mode ({}) for opcode ({}) is not defined.'.format(
                self.addressing_mode.name,
                self.opcode
            ), file_info=self.file_info)

        if isinstance(opcode_emitter, dict):
            opcode_emitter = opcode_emitter[self.index]
        return opcode_emitter

    def emit(self, resolver):
        opcode_emitter = self._get_emitter()
        try:
            return opcode_emitter.emit(self.value_node, self.size, resolver)
        except NoOpcodeForOperandSize as e:
            raise NodeError('{} does not supports size ({}).'.format(
                self.opcode,
                opcode_emitter.guess_value_size(self.value_node, self.size)
            ), self.file_info) from e
        except SymbolNotDefined as e:
            raise NodeError('{} ({}) is not defined in the current scope.'.format(
                e,
                self.value_node
            ), self.file_info)

    def pc_after(self, current_pc):
        opcode_emitter = self._get_emitter()
        return current_pc + opcode_emitter.supposed_length(self.value_node, self.size)

    def __str__(self):
        return 'OpcodeNode(%s, %s, %s, %s)' % (self.opcode, self.addressing_mode, self.index, self.value_node)


class CodePositionNode(object):
    def __init__(self, value_node, resolver):
        self.value_node = value_node
        self.resolver: Resolver = resolver

    def pc_after(self, current_pc):
        self.resolver.reloc = False
        return self.resolver.get_bus().get_address(self.value_node.get_value())

    def emit(self, current_addr):
        self.resolver.set_position(self.value_node.get_value())
        return []

    def __str__(self):
        return 'CodePositionNode(%s)' % self.value_node.get_value()


class RelocationAddressNode(object):
    def __init__(self, pc_value_node, resolver):
        self.pc_value_node = pc_value_node
        self.resolver = resolver

    def pc_after(self, current_pc):
        self.resolver.reloc = True
        return self.resolver.get_bus().get_address(self.pc_value_node.get_value())

    def emit(self, current_addr):
        self.resolver.set_position(self.pc_value_node.get_value())
        # self.resolver.set_position(self.pc_value_node.get_value(), reloc=True)
        return []

    def __str__(self):
        return 'RelocationAddressNode(%s)' % self.pc_value_node.get_value()


class IncludeIpsNode(object):
    def __init__(self, file_path, resolver, delta_expression=None):
        self.ips_file_path = file_path
        self.delta = eval_expr(delta_expression, resolver)
        self.blocks = []
        with open(self.ips_file_path, 'rb') as ips_file:
            if ips_file.read(5) != b'PATCH':
                raise RuntimeError(f'{self.ips_file_path} is missing "PATCH" header')

            while ips_file.peek(3)[:3] != b'EOF':
                block_addr = struct.unpack('>BH', ips_file.read(3))
                block_addr = (block_addr[0] << 16) | block_addr[1]
                block_size = struct.unpack('>H', ips_file.read(2))[0]
                block = ips_file.read(block_size)

                if self.delta is not None:
                    block_addr += self.delta

                self.blocks.append((block_addr, block))

    def pc_after(self, current_pc):
        return current_pc

    def emit(self, current_addr):
        return []

    def __str__(self):
        return


class ScopeNode(object):
    def __init__(self, resolver):
        self.resolver = resolver
        self.parent_scope = self.resolver.current_scope

    def pc_after(self, current_pc):
        self.resolver.use_next_scope()
        return current_pc

    def emit(self, current_addr):
        self.resolver.use_next_scope()
        return []


class PopScopeNode(object):
    def __init__(self, resolver):
        self.resolver = resolver

    def pc_after(self, current_pc):
        self.resolver.restore_scope(exports=True)
        return current_pc

    def emit(self, current_addr):
        self.resolver.restore_scope()
        return []


class TableNode(object):
    def __init__(self, path, resolver):
        self.table_path = path
        self.resolver = resolver
        resolver.current_scope.table = Table(self.table_path)

    def pc_after(self, current_pc):
        return current_pc

    def emit(self, current_addr):
        return []


class TextNode(object):
    def __init__(self, text, resolver):
        self.text = text
        self.resolver = resolver

        table = self.resolver.current_scope.get_table()

        self.binary_text = table.to_bytes(self.text)

    def pc_after(self, current_pc):
        return current_pc + len(self.binary_text)

    def emit(self, current_addr):
        return self.binary_text


class PointerNode(object):
    def __init__(self, value_node):
        self.value_node = value_node

    def pc_after(self, current_pc):
        return current_pc + 3

    def emit(self, current_addr):
        value = self.value_node.get_value()
        return struct.pack('<HB', value & 0xFFFF, (value >> 16) & 0xFF)
