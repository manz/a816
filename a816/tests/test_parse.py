import unittest
import struct

from a816.cpu.cpu_65c816 import AddressingMode
from a816.expressions import eval_expr
from a816.parse.nodes import OpcodeNode, ValueNode
from a816.program import Program
from a816.symbols import Resolver


class ParseTest(unittest.TestCase):
    def test_parse(self):
        program = Program()
        nodes = program.parse([
            'lda #$1234'
        ])

        print(nodes)
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

        nodes = program.parse(input_program)
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
            '.dw $3450, 0x00, symbol & 0x00FF'
        ]

        program = Program()

        nodes = program.parse(input_program)
        print(nodes)
        self.assertEqual(nodes[-1].emit(program.resolver), b'E\x00')

    def test_expressions(self):
        input_program = [
            'my_symbol = 0x4567',
            "jmp.w my_symbol",
            "{",
            "my_symbol = 0x1234",
            "pea.w label",
            'label:',
            "}",
        ]

        program = Program()

        nodes = program.parse(input_program)
        print('Resolve labels')
        for scope in program.resolver.scopes:
            print(scope.symbols)

        program.resolve_labels(nodes)
        machine_output = []
        for node in nodes:
            machine_output.append(node.emit(program.resolver))

        machine_code = machine_output[-2]

        print(machine_code)
        unpacked = struct.unpack('<Bh', machine_code)
        self.assertEqual(unpacked[1], 0x1234)

    def test_blocks(self):
        input_program = [
            "my = 0x01",
            "{",
            "my = 0x00",
            "}",
            "{",
            "my = 0x12",
            "a:",
            "}"
        ]

        program = Program()

        nodes = program.parse(input_program)
        program.resolve_labels(nodes)

        for scope in program.resolver.scopes:
            print(scope.symbols)