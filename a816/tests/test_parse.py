import unittest
from a816.parse.ast import code_gen
from a816.parse.lalrparser import LALRParser
import struct

from a816.cpu.cpu_65c816 import AddressingMode
from a816.expressions import eval_expr
from a816.parse.nodes import OpcodeNode, ValueNode
from a816.program import Program
from a816.symbols import Resolver


class ParseTest(unittest.TestCase):
    def test_parse(self):
        program = Program()
        nodes = program.parser.parse([
            'lda #$1234'
        ])

        self.assertEqual(len(nodes), 1)

        node = nodes[0]
        expected_node = OpcodeNode('lda', addressing_mode=AddressingMode.immediate, value_node=ValueNode('1234'))
        self.assertEqual(node.opcode, expected_node.opcode)
        self.assertEqual(node.value_node.get_value(), expected_node.value_node.get_value())
        self.assertEqual(node.addressing_mode, expected_node.addressing_mode)

    def test_short_jumps(self):
        input_program = [
            'my_label:',
            'lda #$0000',
            'bra my_label'
        ]

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        program.resolver.pc = 3
        short_jump_node = nodes[-1]

        machine_code = short_jump_node.emit(program.resolver)

        unpacked = struct.unpack('Bb', machine_code)

        self.assertEqual(unpacked, (128, -5))

    def test_math_expr_eval(self):
        expr = '$100+toto & 0xFFFF'

        resolver = Resolver()
        resolver.current_scope.add_symbol('toto', 0x108000)

        self.assertEqual(eval_expr(expr, resolver), 0x8100)

    def test_symbols_resolved_through_eval(self):
        expr = 'toto'
        resolver = Resolver()
        resolver.current_scope.add_symbol('toto', 0x1234)

        self.assertEqual(eval_expr(expr, resolver), 0x1234)

    def test_data_word(self):
        input_program = [
            'symbol=0x12345',
            '.dw 0x0000',
            '.dw 0x3450, 0x00, symbol & 0x00FF'
        ]

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)
        # print(nodes)
        self.assertEqual(nodes[-1].emit(program.resolver), b'E\x00')

    def test_expressions(self):
        input_program = [
            '{my_symbol = 0x4567',
            "jmp.w my_symbol",
            "{",
            "my_symbol = 0x1234",
            "lda.w label",
            "pea.w my_symbol",
            'label:',
            "}}",
        ]

        program = Program()
        program.parser = LALRParser(program.resolver)

        nodes = program.parser.parse('\n'.join(input_program))

        program.resolve_labels(nodes)

        class StubWriter(object):
            def __init__(self):
                self.data = []

            def begin(self):
                pass

            def write_block(self, block, block_address):
                self.data.append(block)

            def end(self):
                pass

        writer = StubWriter()
        program.emit(nodes, writer)

        machine_code = writer.data[0]

        # program.resolver.dump_symbol_map()
        unpacked = struct.unpack('<BHBHBH', machine_code)

        self.assertEqual(unpacked[1], 0x4567)
        self.assertEqual(unpacked[3], 0x8009)
        self.assertEqual(unpacked[5], 0x1234)

    def test_blocks(self):
        input_program = [
            "{"
            "my = 0x01",
            "{",
            "my = 0x00",
            "}",
            "{",
            "my = 0x12",
            "a:",
            "}"
            "}"
        ]

        program = Program()
        program.parser = LALRParser(program.resolver)

        nodes = program.parser.parse('\n'.join(input_program))
        program.resolve_labels(nodes)

    def test_bug(self):
#         input_program = '''
# .macro dma_transfer_to_vram_call(source, vramptr, count, mode) {
#     php
#     pha
#     phx
#     pea.w return_addr-1
#     pea.w source & 0xFFFF
#     pea.w source >> 16
#     pea.w vramptr
#     pea.w count
#     pea.w mode
#     jmp.w dma_transfer_to_vram
#     return_addr:
#     plx
#     pla
#     plp
# }
#
# *=0x108000
# dma_transfer_to_vram_call(0x112233, 0x1000, 0x1234, 0x3434)
# *=0x10A999
# dma_transfer_to_vram:
# {
#     ; on the stack:
#     ; return address
#     ; source offset
#     ; source bank
#     ; vram pointer
#     ; count
#     ; mode
#     arg_count = 5
#     stack_ptr       = arg_count * 2 - 1
#
#     source_offset   = stack_ptr
#     source_bank     = stack_ptr - 2
#     vram_pointer    = stack_ptr - 4
#     count           = stack_ptr - 6
#     dma_mode        = stack_ptr - 8
#     channel         = 4
#
#     rep #0x20
#     sep #0x10
#     ldx #0x80
#     stx 0x2115
#
#     lda.b source_offset, s
#     sta.w 0x4302 +(channel << 4)
#
#     sep #0x10
#     lda.b source_bank, s
#     sta.w 0x4304 + (channel << 4)
#     rep #0x20
#
#     lda.b vram_pointer, s
#     sta.w 0x2116
#
#     lda.b count, s
#     sta.w 0x4305 + (channel << 4)
#
#     lda.b dma_mode, s
#     sta.w 0x4300 + (channel << 4)
#
#     ldx.b #1 << channel
#     stx 0x420B
#     nop
#     nop
#     pla
#     pla
#     pla
#     pla
#     pla
#     rts
# }
# '''

        input_program = '''.include 'test_include.i'
        dma_transfer_to_vram_call(binary_file_dat, 0x1000, 0x1234, 0x3434)
        ;label:
        .include 'test_include.s'
        .incbin 'binary_file.dat'
        lda.b [0x00], y
        lda.l binary_file_dat, x
        ;label2:
        '''


        program = Program()

        from pprint import pprint
        # program.parser = LALRParser(program.resolver)
        nodes = program.parser.parse(input_program)
        pprint(nodes)
        # code_nodes = code_gen(nodes, program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()

