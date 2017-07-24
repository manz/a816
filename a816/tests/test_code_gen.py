import unittest

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.nodes import OpcodeNode, ValueNode, RelocationAddressNode, LabelNode
from a816.program import Program


class CodeGenTest(unittest.TestCase):
    def test_immediate_code_gen(self):
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

    def test_ateq_reslove(self):
        program = Program()
        program.resolve_labels([
            RelocationAddressNode(ValueNode('0xFFFFFF'), ValueNode('0x7f0000'), program.resolver),
            LabelNode('miaou', program.resolver)
        ])

        self.assertEqual(program.resolver.current_scope['miaou'], 0x7f0000)

