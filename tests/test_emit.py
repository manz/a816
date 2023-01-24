import struct
import unittest
from unittest import skip

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import ExpressionAstNode, Term
from a816.parse.codegen import code_gen
from a816.parse.nodes import NodeError
from a816.parse.tokens import Token, TokenType
from a816.program import Program
from tests import StubWriter


class EmitTest(unittest.TestCase):
    def test_emit_opcode_size_error(self) -> None:
        program = Program()
        nodes = program.parser.parse("lda.l #0x123456")

        self.assertEqual(len(nodes), 1)

        first_node = nodes[0]

        self.assertRaises(NodeError, lambda: first_node.emit(program.resolver.reloc_address))

    def test_short_jumps(self) -> None:
        input_program = """
            my_label:
            lda #0x0000
            bra my_label
        """

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        program.resolver.pc = 3
        short_jump_node = nodes[-1]

        machine_code = short_jump_node.emit(program.resolver.reloc_address)

        unpacked = struct.unpack("Bb", machine_code)

        self.assertEqual(unpacked, (128, -5))

    def test_long_data_node(self) -> None:
        input_program = """
        .dl 0xf01ac5
        """

        program = Program()
        result = program.parser.parse_as_ast(input_program)
        self.assertEqual(result.ast, [("dl", ["0xf01ac5"])])

        writer = StubWriter()

        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, writer)

        self.assertEqual(writer.data[0], b"\xc5\x1a\xf0")

    def test_ateq_using_map(self) -> None:
        writer = StubWriter()

        program = Program()
        input_program = """
        .map identifier=1 bank_range=0x00, 0x6f addr_range=0x8000, 0xffff mask=0x8000 mirror_bank_range=0x80, 0xcf
        .map identifier=2 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=0

        *=0x208000
        @=0x7e4000
        label:
        .pointer label
        """
        program.assemble_string_with_emitter(input_program, "test_at_eq", writer)

        self.assertEqual(writer.data[0], b"\x00\x40\x7e")
        self.assertEqual(writer.data_addresses[0], 0x100000)

    def test_ateq_bank_boundaries(self) -> None:
        writer = StubWriter()

        program = Program()

        input_program = """
        *=0x008000
        @=0x7effff
        label:
        .pointer label
        label2:
        """
        program.assemble_string_with_emitter(input_program, "test_macro_application", writer)

        self.assertEqual(writer.data[0], b"\xff\xff\x7e")
        self.assertEqual(writer.data_addresses[0], 0x000000)
        self.assertEqual(program.resolver.current_scope["label2"], 0x7F0002)

    def test_stareq(self) -> None:
        writer = StubWriter()

        program = Program()
        input_program = """
        *=0x038000
        label:
        .pointer label
        """
        program.assemble_string_with_emitter(input_program, "test_macro_application", writer)

        self.assertEqual(writer.data_addresses[0], program.get_physical_address(0x038000))
        self.assertEqual(writer.data[0], b"\x00\x80\x03")

    def test_stareq_bank_boundaries(self) -> None:
        writer = StubWriter()

        program = Program()
        input_program = """
           *=0x03FFFF
           label:
           .pointer label
           label2:
           """
        program.assemble_string_with_emitter(input_program, "test_macro_application", writer)

        self.assertEqual(writer.data[0], b"\xFF\xFF\x03")
        self.assertEqual(writer.data_addresses[0], program.get_physical_address(0x03FFFF))
        self.assertEqual(program.resolver.current_scope["label2"], 0x048002)

    def test_symbols_are_globals_in_current_scope(self) -> None:
        writer = StubWriter()

        program = Program()
        program.resolver.bus
        input_program = """
        *=0x008000
        .macro test(pointer) {
            .pointer pointer
        }
        test(newgame.label)
        .db 0
       .scope newgame {
            label:
        }
        """
        program.assemble_string_with_emitter(input_program, "test_macro_application", writer)
        expression = ExpressionAstNode([Term(Token(TokenType.IDENTIFIER, "newgame.label"))])
        self.assertEqual(writer.data_addresses[0], program.get_physical_address(0x008000))
        self.assertEqual(eval_expression(expression, program.resolver), 0x008004)
        self.assertEqual(writer.data[0], b"\x04\x80\x00\x00")
        self.assertEqual(writer.data_addresses[0], 0x000000)

    def test_eor_dp_sr(self) -> None:
        input_program = """
        eor (0x12,x)
        """

        program = Program()
        result = program.parser.parse_as_ast(input_program)
        self.assertEqual([("opcode", AddressingMode.dp_or_sr_indirect_indexed, "eor", "0x12", "x")], result.ast)

        writer = StubWriter()

        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, writer)

        self.assertEqual(writer.data[0], b"\x41\x12")

    def test_ascii(self) -> None:
        input_program = """
           .ascii 'Final Fantasy VI    '
           """

        program = Program()
        result = program.parser.parse_as_ast(input_program)
        self.assertEqual(result.ast, [("ascii", "Final Fantasy VI    ")])

        writer = StubWriter()

        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, writer)

        self.assertEqual(writer.data[0], b"Final Fantasy VI    ")

    @skip("Not implemented yet")
    def test_tr(self) -> None:
        input_program = """
           .ascii t'Final Fantasy VI    '
           """

        program = Program()
        result = program.parser.parse_as_ast(input_program)
        self.assertEqual(result.ast, [("ascii", "Final Fantasy VI    ")])

        writer = StubWriter()

        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, writer)

        self.assertEqual(writer.data[0], b"Final Fantasy VI    ")
