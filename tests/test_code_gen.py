import unittest

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.nodes import LabelNode, NodeError, OpcodeNode, RelocationAddressNode, ValueNode
from a816.program import Program


class CodeGenTest(unittest.TestCase):
    def test_immediate_code_gen(self) -> None:
        program = Program()
        _, nodes = program.parser.parse("lda #0x1234")

        self.assertEqual(len(nodes), 1)

        node = nodes[0]

        assert isinstance(node, OpcodeNode)

        self.assertEqual(node.opcode, "lda")
        assert node.value_node is not None
        self.assertEqual(node.value_node.get_value(), 0x1234)
        self.assertEqual(node.addressing_mode, AddressingMode.immediate)

    def test_brk_with_signature_byte(self) -> None:
        """`brk #imm` emits the opcode + the user-supplied signature byte."""
        program = Program()
        _, nodes = program.parser.parse("brk #0x42")
        program.resolve_labels(nodes)
        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        emitted = node.emit(program.resolver.reloc_address)
        self.assertEqual(emitted, b"\x00\x42")

    def test_brk_without_operand_still_emits_opcode(self) -> None:
        """Bare `brk` keeps its single-byte form for backward compatibility."""
        program = Program()
        _, nodes = program.parser.parse("brk")
        program.resolve_labels(nodes)
        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        emitted = node.emit(program.resolver.reloc_address)
        self.assertEqual(emitted, b"\x00")

    def test_ateq_reslove(self) -> None:
        program = Program()
        program.resolve_labels(
            [
                RelocationAddressNode(ValueNode("0x7f0000"), program.resolver),
                LabelNode("miaou", program.resolver),
            ]
        )

        self.assertEqual(program.resolver.current_scope["miaou"], 0x7F0000)

    def test_if_with_undefined_symbol_evaluates_to_false(self) -> None:
        """Test that .if with undefined symbol evaluates to false.

        This is intentional - labels are resolved in a later pass, so forward
        references like `.if END_OF_FREE_SPACE > 0x1ffff` need to work.
        """
        program_text = """
.if UNDEFINED_SYMBOL {
    .db 0x42
}
"""
        program = Program()
        # Should not raise - undefined symbols evaluate to false
        _, nodes = program.parser.parse(program_text)
        # The .db should not be generated since condition is false
        self.assertEqual(len(nodes), 0)

    def test_macro_wrong_argument_count_raises_error(self) -> None:
        """Test that calling a macro with wrong number of arguments raises a descriptive error."""
        program_text = """
.macro my_macro(arg1, arg2) {
    .db arg1
    .db arg2
}
my_macro(1)
"""
        program = Program()
        with self.assertRaises(NodeError) as context:
            program.parser.parse(program_text)
        self.assertIn("my_macro", str(context.exception))
        self.assertIn("2", str(context.exception))  # expected count
        self.assertIn("1", str(context.exception))  # actual count
