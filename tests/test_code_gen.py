import unittest

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.ast.nodes import ExpressionAstNode
from a816.parse.codegen import code_gen
from a816.parse.nodes import LabelNode, OpcodeNode, RelocationAddressNode, ValueNode
from a816.program import Program


class CodeGenTest(unittest.TestCase):
    def test_immediate_code_gen(self) -> None:
        program = Program()
        nodes = program.parser.parse("lda #0x1234")

        self.assertEqual(len(nodes), 1)

        node = nodes[0]

        assert isinstance(node, OpcodeNode)

        self.assertEqual(node.opcode, "lda")
        assert node.value_node is not None
        self.assertEqual(node.value_node.get_value(), 0x1234)
        self.assertEqual(node.addressing_mode, AddressingMode.immediate)

    def test_ateq_reslove(self) -> None:
        program = Program()
        program.resolve_labels(
            [RelocationAddressNode(ValueNode("0x7f0000"), program.resolver), LabelNode("miaou", program.resolver)]
        )

        self.assertEqual(program.resolver.current_scope["miaou"], 0x7F0000)
