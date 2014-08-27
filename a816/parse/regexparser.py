from a816.cpu.cpu_65c816 import AddressingMode
from a816.exceptions import SymbolNotDefined
from a816.parse.matchers import *
from a816.parse.nodes import UnkownOpcodeError, OpcodeNode
from a816.parse.regexes import *

import re


class Parser(object):
    def __init__(self, resolver):
        self.resolver = resolver
        self.logger = logger
        self.logger = logging.getLogger('x816')

    def parse(self, program):
        raise NotImplementedError('You have to implement the parse method.')


class RegexParser(Parser):
    def __init__(self, resolver):
        super().__init__(resolver)

        self.matchers = [
            RomTypeMatcher(self.resolver),
            ProgramCounterPositionMatcher(self.resolver),
            SymbolDefineMatcher(self.resolver),
            LabelMatcher(self.resolver),
            BinaryIncludeMatcher(self.resolver),
            DataWordMatcher(self.resolver),
            DataByteMatcher(self.resolver),
            TableMatcher(self.resolver),
            TextMatcher(self.resolver),
            PointerMatcher(self.resolver),
            StateMatcher(self.resolver),
            AbstractInstructionMatcher(none_regexp, OpcodeNode, self.resolver, AddressingMode.none),
            AbstractInstructionMatcher(immediate_regexp, OpcodeNode, self.resolver, AddressingMode.immediate),
            AbstractInstructionMatcher(direct_regexp, OpcodeNode, self.resolver, AddressingMode.direct),
            AbstractInstructionMatcher(direct_indexed_regexp, OpcodeNode, self.resolver, AddressingMode.direct_indexed),
            AbstractInstructionMatcher(indirect_regexp, OpcodeNode, self.resolver, AddressingMode.indirect),
            AbstractInstructionMatcher(indirect_indexed_regexp, OpcodeNode, self.resolver,
                                       AddressingMode.indirect_indexed),
            AbstractInstructionMatcher(indirect_long_regexp, OpcodeNode, self.resolver, AddressingMode.indirect_long),
            AbstractInstructionMatcher(indirect_indexed_long_regexp, OpcodeNode, self.resolver,
                                       AddressingMode.indirect_indexed_long)
        ]

    def parse(self, program):
        parsed_list = []
        line_number = 0
        for line in program:
            line = line.strip()
            line = re.sub(comment_regexp, '', line)
            node = None

            if line:
                for matcher in self.matchers:

                    try:
                        node = matcher.parse(line)
                    except UnkownOpcodeError as e:
                        self.logger.error('While parsing "%s" at %d' % (line, line_number))
                        self.logger.error(e)
                    except SymbolNotDefined as e:
                        self.logger.error(e)

                    if node is not None:
                        if isinstance(node, list):
                            parsed_list = parsed_list + node
                        elif isinstance(node, bool) and node:
                            break
                        else:
                            parsed_list.append(node)
                        break

                if node is None:
                    self.logger.warn('Ignored a non matching line at %d "%s"' % (line_number, line))

            line_number += 1
        return parsed_list