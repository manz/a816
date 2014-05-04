import re
from cpu_65c816 import RomType
from nodes import LabelReferenceNode, ValueNode, LabelNode, CodePositionNode
from regexes import label_regexp, pc_change_regexp, rom_type_regexp


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
            if 'symbol' in match.groupdict().keys():
                if match.group('symbol'):
                    value = LabelReferenceNode(match.group('symbol'), self.resolver)
                else:
                    value = ValueNode(match.group('value'))

            size = match.group('size')

            index = None
            if 'index' in match.groupdict().keys():
                index = match.group('index')

            return self.node_class(match.group('opcode'), size=size, value_node=value, index=index, addressing_mode=self.addressing_mode)
