#!/usr/bin/env python3.4
import argparse
import struct

from cpu_65c816 import AddressingMode
from matchers import AbstractInstructionMatcher, LabelMatcher, ProgramCounterPositionMatcher, RomTypeMatcher
from nodes import OpcodeNode, CodePositionNode
from regexes import immediate_regexp, direct_regexp, direct_indexed_regexp, indirect_long_regexp, \
    indirect_indexed_long_regexp, \
    indirect_regexp, indirect_indexed_regexp, none_regexp
from symbols import Resolver


class Program(object):
    def __init__(self):
        self.resolver = Resolver()

        self.matchers = [
            RomTypeMatcher(self.resolver),
            LabelMatcher(self.resolver),
            ProgramCounterPositionMatcher(self.resolver),
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
        previous_pc = self.resolver.pc

        for node in program_nodes:
            previous_pc = node.pc_after(previous_pc)

    def parse(self, program):
        parsed_list = []
        for line in program:
            line = line.strip()
            for matcher in self.matchers:
                node = matcher.parse(line)
                if node:
                    parsed_list.append(node)
                    continue
        return parsed_list

    # def emit(self, program):
    #     with open('/tmp/toto.o', 'wb') as f:
    #         for node in program:
    #             node_bytes = node.emit(self.resolver)
    #             if node_bytes:
    #                 print(node_bytes)
    #                 f.write(node_bytes)
    #                 self.resolver.pc += len(node_bytes)

    def emit_ips(self, program, file):
        ips = IPSWriter(file)

        current_block = b''
        current_block_addr = self.resolver.pc
        ips.begin()
        for node in program:
            node_bytes = node.emit(self.resolver)
            if node_bytes:
                current_block += node_bytes
                self.resolver.pc += len(node_bytes)

            if isinstance(node, CodePositionNode):
                if len(current_block) > 0:
                    ips.write_block(current_block, current_block_addr)
                current_block_addr = self.resolver.pc
                current_block = b''

        if len(current_block) > 0:
            ips.write_block(current_block, current_block_addr)

        ips.end()

    def assemble_as_patch(self, asm_file, ips_file):
        with open(asm_file) as f:
            input_program = f.readlines()
            nodes = self.parse(input_program)

            # for node in nodes:
            #     print(node)

        self.resolve_labels(nodes)
        # print(self.resolver.symbol_map)

        with open(ips_file, 'wb') as f:
            self.emit_ips(nodes, f)


class IPSWriter(object):
    def __init__(self, file):
        self.file = file

    def begin(self):
        self.file.write(b'PATCH')

    def write_block_header(self, block, block_address):
        self.file.write(struct.pack('>BH', block_address >> 16, block_address & 0xFFFF))
        self.file.write(struct.pack('>H', len(block)))

    def write_block(self, block, block_address):
        self.write_block_header(block, block_address)
        self.file.write(block)

    def end(self):
        self.file.write(b'EOF')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='a816 Arguments parser', epilog='')
    parser.add_argument('--verbose', action='store_true', help='Displays all log levels.')
    parser.add_argument('-o', '--output', dest='output_file', default='a.out', help='Output file')
    parser.add_argument('input_file', help='The asm file to assemble.')
    args = parser.parse_args()

    program = Program()
    program.assemble_as_patch(args.input_file, args.output_file)
