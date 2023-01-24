import struct
from typing import Any, List, Tuple
from unittest.case import TestCase

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import AssignAstNode
from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser, ParserResult
from a816.program import Program
from a816.symbols import Resolver
from tests import StubWriter


class TestParse(TestCase):
    maxDiff = None

    @staticmethod
    def _get_result_for(program_text: str) -> ParserResult:
        return MZParser.parse_as_ast(program_text, "memory.s")

    def _get_ast_for(self, program_text: str) -> List[Tuple[Any, ...]]:
        return self._get_result_for(program_text).ast

    def test_label(self) -> None:
        result = self._get_result_for("my_cute_label:")
        self.assertEqual(result.ast, [("label", "my_cute_label")])

    def test_immediate_instruction(self) -> None:
        result = self._get_result_for("lda #0x00")
        self.assertEqual(result.ast, [("opcode", AddressingMode.immediate, "lda", "0x00", None)])

    def test_direct_instruction_with_size(self) -> None:
        result_b = self._get_result_for("lda.b 0x00")
        result_w = self._get_result_for("lda.w 0x0000")
        result_l = self._get_result_for("lda.l 0x000000")

        self.assertEqual(
            result_b.ast,
            [("opcode", AddressingMode.direct, ("lda", "b"), "0x00", None)],
        )

        self.assertEqual(
            result_w.ast,
            [("opcode", AddressingMode.direct, ("lda", "w"), "0x0000", None)],
        )

        self.assertEqual(
            result_l.ast,
            [("opcode", AddressingMode.direct, ("lda", "l"), "0x000000", None)],
        )

    def test_direct_instruction(self) -> None:
        result = self._get_result_for("lda 0x00")
        self.assertEqual(result.ast, [("opcode", AddressingMode.direct, "lda", "0x00", None)])

    def test_direct_indexed_instruction(self) -> None:
        result = self._get_result_for("lda 0x00, y\n")
        self.assertEqual(result.ast, [("opcode", AddressingMode.direct_indexed, "lda", "0x00", "y")])

    def test_indirect_instruction(self) -> None:
        result = self._get_result_for("lda (0x00)")
        self.assertEqual(result.ast, [("opcode", AddressingMode.indirect, "lda", "0x00", None)])

    def test_indirect_indexed_instruction(self) -> None:
        result = self._get_result_for("lda (0x00), s\n")
        self.assertEqual(
            result.ast,
            [("opcode", AddressingMode.indirect_indexed, "lda", "0x00", "s")],
        )

    def test_indirect_long_instruction(self) -> None:
        result = self._get_result_for("lda [0x00]")
        self.assertEqual(result.ast, [("opcode", AddressingMode.indirect_long, "lda", "0x00", None)])

    def test_indirect_long_indexed_instruction(self) -> None:
        result = self._get_result_for("lda [0x00], x\n")
        self.assertEqual(
            result.ast,
            [("opcode", AddressingMode.indirect_indexed_long, "lda", "0x00", "x")],
        )

    def test_none_instruction(self) -> None:
        result = self._get_result_for("nop\n")
        self.assertEqual(result.ast, [("opcode", AddressingMode.none, "nop", None, None)])

    def test_symbol_define(self) -> None:
        result = self._get_result_for("toto = 0x00 + 0x00")
        self.assertEqual([("assign", "toto", "0x00 + 0x00")], result.ast)

    def test_macro(self) -> None:
        result = self._get_result_for(".macro test_macro(arg) {\n lda #arg\n }")
        self.assertEqual(
            result.ast,
            [
                (
                    "macro",
                    "test_macro",
                    ("args", ["arg"]),
                    (
                        "block",
                        [("opcode", AddressingMode.immediate, "lda", "arg", None)],
                    ),
                )
            ],
        )

    def test_macro_apply(self) -> None:
        result = self._get_result_for("shift_char(base, dest)")
        self.assertEqual(
            [("macro_apply", "shift_char", ("apply_args", ["base", "dest"]))],
            result.ast,
        )

    def test_named_scope(self) -> None:
        result = self._get_result_for(".scope toto {\n .db 0\n }")
        self.assertEqual(
            [
                (
                    "scope",
                    "toto",
                    ("block", [("db", ["0"])]),
                )
            ],
            result.ast,
        )

    def test_incbin(self) -> None:
        result = self._get_result_for(".incbin 'binary_file.bin'")
        self.assertEqual([("incbin", "binary_file.bin")], result.ast)

    def test_table(self) -> None:
        result = self._get_result_for(".table 'dialog.tbl'")
        self.assertEqual([("table", "dialog.tbl")], result.ast)

    def test_text(self) -> None:
        result = self._get_result_for(".text 'PUSH START'")
        self.assertEqual([("text", "PUSH START")], result.ast)

    def test_star_eq(self) -> None:
        result = self._get_result_for("*=0xc00000")
        self.assertEqual([("star_eq", "0xc00000")], result.ast)

    def test_at_eq(self) -> None:
        result = self._get_result_for("@=0x7e0000")
        self.assertEqual([("at_eq", "0x7e0000")], result.ast)

    def test_dp_or_sr_indirect_indexed(self) -> None:
        result = self._get_result_for("lda (0x00,x)\n")
        self.assertEqual(
            result.ast,
            [("opcode", AddressingMode.dp_or_sr_indirect_indexed, "lda", "0x00", "x")],
        )

    def test_eor_addressing_modes(self) -> None:
        program = """
        EOR (0x01,x)
        EOR 0x01, s
        EOR 0x01
        EOR [0x01]
        EOR #0x01
        EOR (0x01), y
        EOR (0x01,s),y
        EOR 0x01, x
        EOR [0x02], y
        EOR 0x02, y
        EOR 0x02, x
        EOR 0x010203, x
        """
        result = self._get_result_for(program)
        expected = [
            ("opcode", AddressingMode.dp_or_sr_indirect_indexed, "EOR", "0x01", "x"),
            ("opcode", AddressingMode.direct_indexed, "EOR", "0x01", "s"),
            ("opcode", AddressingMode.direct, "EOR", "0x01", None),
            ("opcode", AddressingMode.indirect_long, "EOR", "0x01", None),
            ("opcode", AddressingMode.immediate, "EOR", "0x01", None),
            ("opcode", AddressingMode.indirect_indexed, "EOR", "0x01", "y"),
            (
                "opcode",
                AddressingMode.stack_indexed_indirect_indexed,
                "EOR",
                "0x01",
                "y",
            ),
            ("opcode", AddressingMode.direct_indexed, "EOR", "0x01", "x"),
            ("opcode", AddressingMode.indirect_indexed_long, "EOR", "0x02", "y"),
            ("opcode", AddressingMode.direct_indexed, "EOR", "0x02", "y"),
            ("opcode", AddressingMode.direct_indexed, "EOR", "0x02", "x"),
            ("opcode", AddressingMode.direct_indexed, "EOR", "0x010203", "x"),
        ]
        self.assertEqual(result.ast, expected)

    def test_string_quote_escape(self) -> None:
        result = self._get_result_for(".text 'I\\'m hungry'")
        self.assertEqual([("text", "I\\'m hungry")], result.ast)

    def test_scan_error(self) -> None:
        result = self._get_result_for("a")
        self.assertEqual(result.error, "\nmemory.s:0:1 TokenType.EOF\na\n ")

    def test_recursive_macros(self) -> None:
        program = """
.macro recursive(length) {
    .if length  {
        .db length
        recursive(length - 1)
    } .else {
        .db 0
    }
}
        recursive(4)
        """

        result = self._get_result_for(program)

        self.assertEqual(
            [
                (
                    "macro",
                    "recursive",
                    ("args", ["length"]),
                    (
                        "block",
                        [
                            (
                                "if",
                                "length",
                                (
                                    "compound",
                                    [
                                        ("db", ["length"]),
                                        (
                                            "macro_apply",
                                            "recursive",
                                            ("apply_args", ["length - 1"]),
                                        ),
                                    ],
                                ),
                                ("compound", [("db", ["0"])]),
                            )
                        ],
                    ),
                ),
                ("macro_apply", "recursive", ("apply_args", ["4"])),
            ],
            result.ast,
        )
        _ = code_gen(result.nodes, Resolver())

    def test_dw(self) -> None:
        program = """
.dw 0x00
mac(0)
"""
        result = self._get_result_for(program)
        self.assertEqual(
            result.ast,
            [("dw", ["0x00"]), ("macro_apply", "mac", ("apply_args", ["0"]))],
        )

    def test_if(self) -> None:
        program = """
DEBUG := 1
.if DEBUG {
    .db 0x00
}
        """

        result = self._get_result_for(program)
        self.assertEqual(
            result.ast,
            [
                ("assign", "DEBUG", "1"),
                ("if", "DEBUG", ("compound", [("db", ["0x00"])]), None),
            ],
        )

    def test_if_else(self) -> None:
        program = """
    DEBUG := 1
    .if DEBUG {
    .db 0x00
    } .else {
    .db 0x85
    }
        """

        result = self._get_result_for(program)
        self.assertEqual(
            result.ast,
            [
                ("assign", "DEBUG", "1"),
                (
                    "if",
                    "DEBUG",
                    ("compound", [("db", ["0x00"])]),
                    ("compound", [("db", ["0x85"])]),
                ),
            ],
        )

    def test_for(self) -> None:
        program = """
        .macro generate_power_of_twos_table(min, max) {
            .for k := min, max  {
                .dw 1 << k
            }
        }
        generate_power_of_twos_table(0, 8)
        """

        result = self._get_result_for(program)
        #      self.assertEqual(result.ast, [
        #          ('for', 'k', '0', '5', ('compound', [('db', ['k'])]))
        #      ])
        p = Program()

        nodes = code_gen(result.nodes, p.resolver)
        #   self.assertEqual(nodes, [])
        writer = StubWriter()
        p.resolve_labels(nodes)
        p.emit(nodes, writer)

        self.assertEqual(struct.unpack("<8H", writer.data[0]), (1, 2, 4, 8, 16, 32, 64, 128))

    def test_eq_ne(self) -> None:
        program = """
        a := 0
        .if a != 0 {
            .db a
        }
        """

        result = self._get_result_for(program)

        self.assertEqual(
            result.ast,
            [
                ("assign", "a", "0"),
                ("if", "a != 0", ("compound", [("db", ["a"])]), None),
            ],
        )

    def test_unary_operator(self) -> None:
        program = """
        a := -1
        """

        result = self._get_result_for(program)
        root_node = result.nodes[0]
        assert isinstance(root_node, AssignAstNode)
        assign_value = eval_expression(root_node.value, Resolver())
        self.assertEqual(-1, assign_value)

    def test_code_variable(self) -> None:
        program = """
        .macro ram_patch(address, code) {
  .dw start & 0xffff
  .db start >> 16
  .dw end - start

  @=address
  start:
  {{ code }}
  end:
}

.scope credits_text {
    PT0001=0x1985
}

ram_patch(0x7E5CFB, {
    .dw credits_text.PT0001
})"""

        p = Program()
        writer = StubWriter()
        p.assemble_string_with_emitter(program, "memory.s", writer)
