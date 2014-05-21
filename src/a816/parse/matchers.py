import re
from a816.parse.nodes import BinaryNode, WordNode, ByteNode, ScopeNode, PopScopeNode, SymbolNode
from a816.parse.regexes import include_binary_regex, data_word_regexp, data_byte_regexp, push_context_regexp, \
    pop_context_regexp
from ..cpu.cpu_65c816 import RomType
from ..parse.nodes import LabelReferenceNode, ValueNode, LabelNode, CodePositionNode
from .regexes import label_regexp, pc_change_regexp, rom_type_regexp, define_symbol_regex


class LabelMatcher(object):
    def __init__(self, resolver):
        self.resolver = resolver
        self.regexp = re.compile(label_regexp)

    def parse(self, line):
        match = self.regexp.match(line)
        if match:
            return LabelNode(match.group('label'), self.resolver)


class ProgramCounterPositionMatcher(object):
    def __init__(self, resolver):
        self.regexp = re.compile(pc_change_regexp)
        self.resolver = resolver

    def parse(self, line):
        match = self.regexp.match(line)

        if match:
            return CodePositionNode(match.group('value'), self.resolver)


class SymbolDefineMatcher(object):
    def __init__(self, resolver):
        self.regexp = re.compile(define_symbol_regex)
        self.resolver = resolver

    def parse(self, line):
        match = self.regexp.match(line)

        if match:
            # self.resolver.current_scope.add_symbol(match.group('symbol'), int(match.group('value'), 16))
            # return []
            return [SymbolNode(match.group('symbol'),
                               match.group('expression'),
                               self.resolver)]


class StateMatcher(object):
    def __init__(self, resolver):
        self.resolver = resolver
        self.push_context_regexp = re.compile(push_context_regexp)
        self.pop_context_regexp = re.compile(pop_context_regexp)

    def parse(self, line):
        match = self.push_context_regexp.match(line)
        if match:
            self.resolver.append_scope()
            self.resolver.use_next_scope()
            return ScopeNode(self.resolver)

        match = self.pop_context_regexp.match(line)
        if match:
            self.resolver.restore_scope()
            return PopScopeNode(self.resolver)


class BinaryIncludeMatcher(object):
    def __init__(self, resolver):
        self.regexp = re.compile(include_binary_regex)
        self.resolver = resolver

    def parse(self, line):
        match = self.regexp.match(line)

        if match:
            return BinaryNode(match.group('path'), self.resolver)


class DataWordMatcher(object):
    def __init__(self, resolver):
        self.regexp = re.compile(data_word_regexp)
        self.resolver = resolver

    def parse(self, line):
        match = self.regexp.match(line)

        if match:
            values = match.group('data').split(',')
            if len(values) == 0:
                raise RuntimeError('.dw should have at least one value')

            nodes = []

            for value in values:
                 nodes.append(WordNode(LabelReferenceNode(value.strip(), self.resolver)))

            return nodes


class DataByteMatcher(object):
    def __init__(self, resolver):
        self.regexp = re.compile(data_byte_regexp)
        self.resolver = resolver

    def parse(self, line):
        match = self.regexp.match(line)

        if match:
            values = match.group('data').split(',')
            if len(values) == 0:
                raise RuntimeError('.dw should have at least one value')

            nodes = []

            for value in values:
                 nodes.append(ByteNode(LabelReferenceNode(value.strip(), self.resolver)))

            return nodes


class RomTypeMatcher(object):
    def __init__(self, resolver):
        self.regexp = re.compile(rom_type_regexp)
        self.resolver = resolver

    def parse(self, line):
        match = self.regexp.match(line)

        if match:
            self.resolver.rom_type = getattr(RomType, match.group('romtype'))


class AbstractInstructionMatcher(object):
    def __init__(self, regexp, node_class, resolver, addressing_mode):
        self._compiled_regexp = None
        self.resolver = resolver
        self.regexp = regexp + '$'
        self.node_class = node_class
        self.addressing_mode = addressing_mode

    def compiled_regexp(self):
        if self._compiled_regexp is None:
            self._compiled_regexp = re.compile(self.regexp)

        return self._compiled_regexp

    def parse(self, line):
        match = self.compiled_regexp().match(line)
        if match:
            value = None
            if 'symbol' in match.groupdict().keys() or 'expression' in match.groupdict():
                if match.group('symbol') or match.group('expression'):
                    value = LabelReferenceNode(match.group('symbol') or match.group('expression'), self.resolver)
                else:
                    value = ValueNode(match.group('value'))

            size = match.group('size')

            index = None
            if 'index' in match.groupdict().keys():
                index = match.group('index').lower()

            opcode = match.group('opcode')

            return self.node_class(opcode, size=size, value_node=value, index=index, addressing_mode=self.addressing_mode)
