import struct
from a816.expressions import eval_expr
from ..cpu.cpu_65c816 import snes_opcode_table, snes_to_rom


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


class LabelReferenceNode(object):
    def __init__(self, expression, resolver):
        self.expression = expression
        self.resolver = resolver

    def get_value(self):
        try:
            return eval_expr(self.expression, self.resolver)
        except KeyError:
            raise RuntimeError('The label %s is not defined.' % self.expression)

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


class ExpressionNode(object):
    def __init__(self, expression, resolver):
        self.expression = expression
        self.resolver = resolver

    def get_value(self):
        return eval_expr(self.expression, self.resolver)


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


class WordNode(object):
    def __init__(self, value_node):
        self.value_node = value_node

    def emit(self, current_address):
        return struct.pack('<H', self.value_node.get_value())

    def pc_after(self, current_pc):
        return current_pc + 2


class ByteNode(object):
    def __init__(self, value_node):
        self.value_node = value_node

    def emit(self, current_address):
        return struct.pack('B', self.value_node.get_value())

    def pc_after(self, current_pc):
        return current_pc + 1


class UnkownOpcodeError(Exception):
    pass


class OpcodeNode(object):
    def __init__(self, opcode, size=None, addressing_mode=None, index=None, value_node=None):
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        self.size = size.lower() if size else None

        try:
            self._get_emitter()
        except KeyError:
            raise UnkownOpcodeError('%s is unknown for addressing mode' % self.opcode)

    def _get_emitter(self):
        opcode_emitter = snes_opcode_table[self.opcode][self.addressing_mode]
        if isinstance(opcode_emitter, dict):
            opcode_emitter = opcode_emitter[self.index]
        return opcode_emitter

    def emit(self, resolver):
        opcode_emitter = self._get_emitter()
        instruction_bytes = opcode_emitter.emit(self.value_node, self.size, resolver)
        return instruction_bytes

    def pc_after(self, current_pc):
        opcode_emitter = self._get_emitter()
        return current_pc + opcode_emitter.supposed_length(self.value_node, self.size)

    def __str__(self):
        return 'OpcodeNode(%s, %s, %s, %s)' % (self.opcode, self.addressing_mode, self.index, self.value_node)


class CodePositionNode(object):
    def __init__(self, code_position, resolver):
        self.code_position = code_position
        self.resolver = resolver

    def pc_after(self, current_pc):
        return snes_to_rom(int(self.code_position, 16))

    def emit(self, current_addr):
        self.resolver.pc = snes_to_rom(int(self.code_position, 16))
        return []

    def __str__(self):
        return 'CodePositionNode(%s)' % self.code_position


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
        self.resolver.restore_scope()
        return current_pc

    def emit(self, current_addr):
        self.resolver.restore_scope()
        return []
