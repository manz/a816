from cpu_65c816 import snes_opcode_table, snes_to_rom


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

    def __repr__(self):
        return 'ValueNode(%s)' % self.value


class LabelReferenceNode(object):
    def __init__(self, symbol, resolver):
        self.symbol = symbol
        self.resolver = resolver

    def get_value(self):
        return self.resolver.value_for(self.symbol) or 0

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

    def __repr__(self):
        return 'LabelReferenceNode(%s)' % self.symbol


class LabelNode(object):
    def __init__(self, symbol_name, resolver):
        self.symbol_name = symbol_name
        self.resolver = resolver

    def emit(self, current_addr):
        return []
        # self.resolver.add_symbol(self.symbol_name, current_addr)

    def pc_after(self, current_pc):
        self.resolver.add_symbol(self.symbol_name, current_pc)
        return current_pc

    def __repr__(self):
        return 'LabelNode(%s)' % self.symbol_name


class OpcodeNode(object):
    def __init__(self, opcode, size=None, addressing_mode=None, index=None, value_node=None):
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        self.size = size.lower() if size else None

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

    def __repr__(self):
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

    def __repr__(self):
        return 'CodePositionNode(%s)' % self.code_position