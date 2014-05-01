import re
from nodes import SymbolNode, ValueNode, ImmediateInstructionNode, DirectInstructionNode, DirectIndexedInstructionNode, \
    IndirectInstructionNode, IndirectIndexedInstructionNode
from regexes import immediate_regexp, direct_regexp, direct_indexed_regexp, indirect_regexp, indirect_indexed_regexp


class AbstractInstructionMatcher(object):
    def __init__(self, regexp, resolver):
        self.resolver = resolver
        self._compiled_regexp = None
        self.regexp = regexp
        self.node_class = None

    def compiled_regexp(self):
        if self._compiled_regexp is None:
            self._compiled_regexp = re.compile(self.regexp)

        return self._compiled_regexp

    def parse(self, line):
        match = self.compiled_regexp().match(line)
        if match:
            if match.group('symbol'):
                value = SymbolNode(match.group('symbol'), self.resolver)
            else:
                value = ValueNode(match.group('value'))

            return self.node_class(match, value)


class ImmediateMatcher(AbstractInstructionMatcher):
    def __init__(self, resolver):
        super().__init__(immediate_regexp + '$', resolver)
        self.node_class = ImmediateInstructionNode


class DirectMatcher(AbstractInstructionMatcher):
    def __init__(self, resolver):
        super().__init__(direct_regexp + '$', resolver)
        self.node_class = DirectInstructionNode


class DirectIndexedMatcher(AbstractInstructionMatcher):
    def __init__(self, resolver):
        super().__init__(direct_indexed_regexp + '$', resolver)
        self.node_class = DirectIndexedInstructionNode


class IndirectMatcher(AbstractInstructionMatcher):
    def __init__(self, resolver):
        super().__init__(indirect_regexp + '$', resolver)
        self.node_class = IndirectInstructionNode


class IndirectIndexedMatcher(AbstractInstructionMatcher):
    def __init__(self, resolver):
        super().__init__(indirect_indexed_regexp + '$', resolver)
        self.node_class = IndirectIndexedInstructionNode