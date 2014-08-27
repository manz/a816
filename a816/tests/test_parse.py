import unittest
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
        nodes = program.parser.parse(
            'lda #0x1234'
        )

        self.assertEqual(len(nodes), 1)

        node = nodes[0]
        expected_node = OpcodeNode('lda', addressing_mode=AddressingMode.immediate, value_node=ValueNode('1234'))
        self.assertEqual(node.opcode, expected_node.opcode)
        self.assertEqual(node.value_node.get_value(), expected_node.value_node.get_value())
        self.assertEqual(node.addressing_mode, expected_node.addressing_mode)

    def test_short_jumps(self):
        input_program = '''
            my_label:
            lda #0x0000
            bra my_label
        '''

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        program.resolver.pc = 3
        short_jump_node = nodes[-1]

        machine_code = short_jump_node.emit(program.resolver)

        unpacked = struct.unpack('Bb', machine_code)

        self.assertEqual(unpacked, (128, -5))

    def test_math_expr_eval(self):
        expr = '0x100+toto & 0xFFFF'

        resolver = Resolver()
        resolver.current_scope.add_symbol('toto', 0x108000)

        self.assertEqual(eval_expr(expr, resolver), 0x8100)

    def test_symbols_resolved_through_eval(self):
        expr = 'toto'
        resolver = Resolver()
        resolver.current_scope.add_symbol('toto', 0x1234)

        self.assertEqual(eval_expr(expr, resolver), 0x1234)

    def test_data_word(self):
        input_program = '''
            symbol=0x12345
            .dw 0x0000
            .dw 0x3450, 0x00, symbol & 0x00FF
        '''

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)
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

    def test_macro(self):
        input_program = '''.include 'test_include.i'
        dma_transfer_to_vram_call(binary_file_dat, 0x1000, 0x1234, 0x3434)
        .include 'test_include.s'
        .incbin 'binary_file.dat'
        lda.b [0x00], y
        lda.l binary_file_dat, x
        '''

        program = Program()

        from pprint import pprint
        nodes = program.parser.parse(input_program)
        pprint(nodes)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()

