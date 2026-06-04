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

    def test_brk_without_operand_is_rejected(self) -> None:
        """Bare `brk` is no longer accepted — signature byte is required."""
        program = Program()
        error, _ = program.parser.parse("brk")
        self.assertIsNotNone(error)

    def test_cop_with_signature_byte(self) -> None:
        program = Program()
        _, nodes = program.parser.parse("cop #0x7F")
        program.resolve_labels(nodes)
        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.emit(program.resolver.reloc_address), b"\x02\x7f")

    def test_wdm_with_signature_byte(self) -> None:
        program = Program()
        _, nodes = program.parser.parse("wdm #0xAB")
        program.resolve_labels(nodes)
        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.emit(program.resolver.reloc_address), b"\x42\xab")

    def test_cmp_stack_relative_under_a16(self) -> None:
        """`cmp <n>,s` ($C3) takes a single byte stack offset regardless of M.

        Stack-relative width does not follow the accumulator, so the byte
        offset must not be rejected as an illegal data size under `.a16`.
        """
        program = Program()
        _, nodes = program.parser.parse("cmp 0x03, s")
        program.resolve_labels(nodes)
        program.resolver.a_size = 16
        node = nodes[-1]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.emit(program.resolver.reloc_address), b"\xc3\x03")

    def test_alu_stack_relative_offsets_are_single_byte(self) -> None:
        """The `,s` ALU family encodes one byte offset, not an acc-sized operand."""
        cases = {"ora": b"\x03\x03", "eor": b"\x43\x03", "adc": b"\x63\x03", "sbc": b"\xe3\x03"}
        for opcode, expected in cases.items():
            with self.subTest(opcode=opcode):
                program = Program()
                _, nodes = program.parser.parse(f"{opcode} 0x03, s")
                program.resolve_labels(nodes)
                program.resolver.a_size = 16
                node = nodes[-1]
                assert isinstance(node, OpcodeNode)
                self.assertEqual(node.emit(program.resolver.reloc_address), expected)

    def test_alu_immediate_opcodes_under_a16(self) -> None:
        """16-bit immediate ALU opcodes must each use their own opcode.

        Regression: `ora #imm` under .a16 was mis-encoded as `$A9` (LDA),
        copy-pasted from the lda row, so `ora #0x2000` silently became
        `lda #0x2000` (replace instead of OR). Pin the whole family.
        """
        cases = {
            "ora": b"\x09\x00\x20",
            "and": b"\x29\x00\x20",
            "eor": b"\x49\x00\x20",
            "adc": b"\x69\x00\x20",
            "sbc": b"\xe9\x00\x20",
            "lda": b"\xa9\x00\x20",
            "cmp": b"\xc9\x00\x20",
        }
        for opcode, expected in cases.items():
            with self.subTest(opcode=opcode):
                program = Program()
                _, nodes = program.parser.parse(f"{opcode} #0x2000")
                program.resolve_labels(nodes)
                program.resolver.a_size = 16
                node = nodes[-1]
                assert isinstance(node, OpcodeNode)
                self.assertEqual(node.emit(program.resolver.reloc_address), expected)

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
