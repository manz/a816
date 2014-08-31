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
        input_program = '''
        .macro test_macro(a, b, c) {
            lda.b #a
            lda.b #b
            lda.b #c
        }
        test_macro(0, 1, 2)
        '''

        expected_ast_nodes = ('block',
                              ('macro', 'test_macro', ('args', ('a', 'b', 'c')),
                               ('compound',
                                ('block',
                                 ('opcode', AddressingMode.immediate, ['lda', 'b'], 'a'),
                                 ('opcode', AddressingMode.immediate, ['lda', 'b'], 'b'),
                                 ('opcode', AddressingMode.immediate, ['lda', 'b'], 'c')))),
                              ('macro_apply', 'test_macro', ('apply_args', ('0', '1', '2'))))

        program = Program()

        ast_nodes = program.parser.parse_as_ast(input_program)

        self.assertEqual(ast_nodes, expected_ast_nodes)

        nodes = code_gen(ast_nodes[1:], program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()

    def test_macro_empty_args(self):
        input_program = """
                .macro test() {
                    sep #0x30
                }

                test()
                """

        program = Program()

        ast_nodes = program.parser.parse_as_ast(input_program)

        print(ast_nodes)
        nodes = code_gen(ast_nodes[1:], program.resolver)
        print(nodes)
