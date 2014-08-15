import logging
import re

from a816.cpu.cpu_65c816 import AddressingMode
from a816.exceptions import SymbolNotDefined
from a816.parse.matchers import RomTypeMatcher, LabelMatcher, ProgramCounterPositionMatcher, AbstractInstructionMatcher, \
    SymbolDefineMatcher, BinaryIncludeMatcher, DataWordMatcher, DataByteMatcher, StateMatcher, TableMatcher, TextMatcher, \
    PointerMatcher
from a816.parse.nodes import OpcodeNode, CodePositionNode, UnkownOpcodeError
from a816.parse.regexes import none_regexp, immediate_regexp, direct_regexp, direct_indexed_regexp, indirect_regexp, \
    indirect_indexed_regexp, indirect_long_regexp, indirect_indexed_long_regexp, comment_regexp
from a816.symbols import Resolver
from a816.writers import IPSWriter


class Program(object):
    def __init__(self):
        self.resolver = Resolver()
        self.logger = logging.getLogger('x816')

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

    def resolve_labels(self, program_nodes):
        self.resolver.last_used_scope = 0

        previous_pc = self.resolver.pc

        for node in program_nodes:
            previous_pc = node.pc_after(previous_pc)

        self.resolver.pc = 0x000000
        self.resolver.last_used_scope = 0
        self.resolver.current_scope = self.resolver.scopes[0]

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

    def emit(self, program, writer):
        current_block = b''
        current_block_addr = self.resolver.pc
        writer.begin()

        for node in program:
            node_bytes = node.emit(self.resolver)

            if node_bytes:
                current_block += node_bytes
                self.resolver.pc += len(node_bytes)

            if isinstance(node, CodePositionNode):
                if len(current_block) > 0:
                    writer.write_block(current_block, current_block_addr)
                current_block_addr = self.resolver.pc
                current_block = b''

        if len(current_block) > 0:
            writer.write_block(current_block, current_block_addr)

        writer.end()

    def emit_ips(self, program, file):
        ips = IPSWriter(file)
        self.emit(program, ips)

    def assemble_as_patch(self, asm_file, ips_file):
        try:
            with open(asm_file, encoding='utf-8') as f:
                input_program = f.readlines()
                nodes = self.parse(input_program)
            self.logger.info('Resolving labels')
            self.resolve_labels(nodes)
            self.resolver.dump_symbol_map()

            with open(ips_file, 'wb') as f:
                self.emit_ips(nodes, f)

        except RuntimeError as e:
            self.logger.error(e)
            return -1

        self.logger.info('Success !')
        return 0