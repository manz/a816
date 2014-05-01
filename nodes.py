class ValueNode(object):
    def __init__(self, value):
        self.value = int(value, 16)

    def get_value(self):
        return self.value

    def __repr__(self):
        return 'ValueNode(%s)' % self.value


class SymbolNode(object):
    def __init__(self, symbol, resolver):
        self.symbol = symbol
        self.resolver = resolver

    def get_value(self):
        return self.resolver.value_for(self.symbol)

    def __repr__(self):
        return 'SymbolNode(%s)' % self.symbol
    

class ImmediateInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.value_node = value_node

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.opcode, self.value_node)


class DirectInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.value_node = value_node

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.opcode, self.value_node)


class DirectIndexedInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.index = match.group('index').lower()
        self.value_node = value_node

    def __repr__(self):
        return '%s(%s, %s, %s)' % (self.__class__.__name__, self.opcode, self.value_node, self.index)


class IndirectInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.value_node = value_node

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.opcode, self.value_node)

class IndirectIndexedInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.index = match.group('index').lower()
        self.value_node = value_node

    def __repr__(self):
        return '%s(%s, %s, %s)' % (self.__class__.__name__, self.opcode, self.value_node, self.index)


class IndirectLongInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.value_node = value_node
        self.value_size = 3

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.opcode, self.value_node)

class IndirectLongIndexedInstructionNode(object):
    def __init__(self, match, value_node):
        self.opcode = match.group('opcode').lower()
        self.index = match.group('index').lower()
        self.value_node = value_node
        self.value_size = 3

    def __repr__(self):
        return '%s(%s, %s, %s)' % (self.__class__.__name__, self.opcode, self.value_node, self.index)

