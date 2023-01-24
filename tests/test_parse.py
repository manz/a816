import logging
import struct
import unittest
from typing import List, Tuple, cast

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.ast.nodes import (
    ExpressionAstNode,
    ExprNode,
    IncludeIpsAstNode,
    Term,
    UnaryOp,
)
from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser
from a816.parse.nodes import OpcodeNode
from a816.parse.tokens import Token, TokenType
from a816.program import Program
from a816.writers import Writer

logger = logging.getLogger("a816")

stream_formatter = logging.Formatter("%(levelname)s :: %(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(stream_formatter)

logger.addHandler(stream_handler)


# logger.setLevel(logging.WARNING)


class StubWriter(Writer):
    def __init__(self) -> None:
        self.data: List[Tuple[int, bytes]] = []

    def begin(self) -> None:
        """StubWriter only implements write_block."""

    def write_block(self, block: bytes, block_address: int) -> None:
        self.data.append((block_address, block))

    def write_block_header(self, block: bytes, block_address: int) -> None:
        return None

    def end(self) -> None:
        """StubWriter only implements write_block."""


class ParseTest(unittest.TestCase):
    def test_parse(self) -> None:
        program = Program()
        nodes = program.parser.parse("lda #0x1234")

        self.assertEqual(len(nodes), 1)

        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.opcode, "lda")
        assert node.value_node is not None
        self.assertEqual(node.value_node.get_value(), 0x1234)
        self.assertEqual(node.addressing_mode, AddressingMode.immediate)

    def test_parse_comments(self) -> None:
        program = Program()
        nodes = program.parser.parse("; comment\nlda #0x1234\n; another comment")

        self.assertEqual(len(nodes), 1)

        node = nodes[0]
        assert isinstance(node, OpcodeNode)
        self.assertEqual(node.opcode, "lda")

    def test_unterminated_string(self) -> None:
        input_program = "'coucou"
        program = Program()

        result = program.parser.parse_as_ast(input_program, "memory.s")

        self.assertEqual("memory.s:0:0 : Unterminated String\n'coucou\n^", result.error)

    def test_invalid_size_specifier(self) -> None:
        input_program = "lda.Q #0x00"
        program = Program()

        result = program.parser.parse_as_ast(input_program, "memory.s")
        self.assertEqual("memory.s:0:4 : Invalid Size Specifier\nlda.Q #0x00\n    ^", result.error)

    def test_invalid_index(self) -> None:
        input_program = "lda 0x00, O"
        program = Program()

        result = program.parser.parse_as_ast(input_program, "memory.s")
        self.assertEqual("memory.s:0:10 : Invalid index\nlda 0x00, O\n          ^", result.error)

    def test_data_word(self) -> None:
        input_program = """{
            symbol=0x12345
            .dw 0x0000
            .dw 0x3450, 0x00, symbol & 0x00FF
            }
        """

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)
        emitted_nodes = []
        for node in nodes:
            node_data = node.emit(program.resolver.reloc_address)
            if node_data:
                emitted_nodes.append(node_data)
        self.assertEqual(emitted_nodes, [b"\x00\x00", b"\x50\x34", b"\x00\x00", b"\x45\x00"])

    def test_expressions(self) -> None:
        input_program = """
            {
                my_symbol = 0x4567
                jmp.w my_symbol
            {
                my_symbol = 0x1234
                lda.w label
                pea.w my_symbol
                label:
            }
        }
        """

        program = Program()
        nodes = program.parser.parse(input_program)

        program.resolve_labels(nodes)

        writer = StubWriter()
        program.emit(nodes, writer)

        machine_code = writer.data[0][1]

        unpacked = struct.unpack("<BHBHBH", machine_code)

        self.assertEqual(unpacked[1], 0x4567)
        self.assertEqual(unpacked[3], 0x8009)
        self.assertEqual(unpacked[5], 0x1234)

    def test_blocks(self) -> None:
        input_program = """
        {
            my = 0x01
            {
            my = 0x00
            }
            {
            my = 0x12
            lda.w #0x00
            a:
            }
            }
        """

        program = Program()
        program.parser = MZParser(program.resolver)
        ast = program.parser.parse_as_ast(input_program)
        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

    def test_macro(self) -> None:
        input_program = """
        .macro test_macro(a, b, c) {
            lda.b #a
            lda.b #b
            lda.b #c
        }
        test_macro(0, 1, 2)
        """

        expected_ast_nodes = [
            (
                "macro",
                "test_macro",
                ("args", ["a", "b", "c"]),
                (
                    "block",
                    [
                        ("opcode", AddressingMode.immediate, ("lda", "b"), "a", None),
                        ("opcode", AddressingMode.immediate, ("lda", "b"), "b", None),
                        ("opcode", AddressingMode.immediate, ("lda", "b"), "c", None),
                    ],
                ),
            ),
            ("macro_apply", "test_macro", ("apply_args", ["0", "1", "2"])),
        ]

        program = Program()

        result = program.parser.parse_as_ast(input_program)
        self.assertEqual(expected_ast_nodes, result.ast)

        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()

    def test_macro_empty_args(self) -> None:
        input_program = """
                .macro test() {
                    sep #0x30
                }

                test()
                """

        program = Program()

        ast_nodes = program.parser.parse_as_ast(input_program)
        self.assertEqual(
            ast_nodes.ast,
            [
                ("macro", "test", ("args", []), ("block", [("opcode", AddressingMode.immediate, "sep", "0x30", None)])),
                ("macro_apply", "test", ("apply_args", [])),
            ],
        )

        code_gen(ast_nodes.nodes, program.resolver)

    def test_push_pull(self) -> None:
        input_program = """
        php
        pha
        display_text_in_menus:                  ; CODE XREF: new_game_screen_related+39p
                                                ; sub_197D3+4Ep ...
            phb
            phd
            phx
            ldx.w     #0x100
            phx
            pld
        ; D=100
            phk
            plb
        ; ds=1000 B=1

        loc_1830B:                              ; CODE XREF: display_text_in_menus+1Fj
            rep     #0x20 ; ' '
        ;.A16
            lda.w     0x0000 ,y
            clc
            adc     0x29
            tax
            sep     #0x20 ; ' '
        ;.A8
            iny
            iny

        loc_18318:                              ; CODE XREF: sub_182CD+15j
                                                ; .01:82F9j ...
        ;	lda     0,Y
            beq     loc_18332
            iny
            cmp     #1
            beq     loc_1830B
            jsr     0x8E32
            sta     0x7E0000,X
            xba
            sta     0x7E0040,X
            inx
            inx
            bra     loc_18318
        ; ---------------------------------------------------------------------------

        loc_18332:                              ; CODE XREF: display_text_in_menus+1Aj
            plx
            pld
            plb
            rts
        """

        program = Program()
        result = program.parser.parse_as_ast(input_program)
        nodes = code_gen(result.nodes, program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, StubWriter())
        print(nodes[-2])

    def test_macro_definition_should_parse(self) -> None:
        input_program = """
        .macro waitforvblank(a) {
            pla
        }
        plb
        waitforvblank(0x123456)
        """

        program = Program()
        ast = program.parser.parse_as_ast(input_program)

    def test_nini(self) -> None:
        input_program = """.macro wait_for_vblank_inline() {
            pha
        negative:
            lda.l 0x004212
            bmi negative
        positive:
            lda.l 0x004212
            bpl positive
            pla
        }"""
        program = Program()
        ast = program.parser.parse_as_ast(input_program)

    def test_php_pha(self) -> None:
        input_program = """
        .macro dma_transfer_to_vram_call(source, vramptr, count, mode)
        {
            php
            pha
            phx
            pea.w return_addr-1
            pea.w source & 0xFFFF
            pea.w  0x00FF & (source >> 16)
            pea.w vramptr
            pea.w count
            pea.w mode
            jmp.l dma_transfer_to_vram
        return_addr:
            plx
            pla
            plp
            TAX            ; using math multiplication
            LDA.L vwf_shift_table,X
        }"""
        program = Program()
        ast = program.parser.parse_as_ast(input_program)

    def test_named_scopes(self) -> None:
        input_program = """
                .scope newgame {
                    .db 0
                    .db 0

                    empty:


                }

                .dw newgame.empty"""

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        nodes = code_gen(ast.nodes, program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()
        writer = StubWriter()
        program.emit(nodes, writer)
        self.assertEqual(writer.data[0][1], b"\x00\x00\x02\x80")

    def test_include_ips(self) -> None:
        input_program = """.include_ips 'whee.ips', -0x200"""
        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        nodes = ast.nodes

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertTrue(isinstance(node, IncludeIpsAstNode))
        ips_node = cast(IncludeIpsAstNode, node)
        self.assertEqual("whee.ips", ips_node.file_path)
        self.assertEqual(
            [UnaryOp(token=Token(TokenType.OPERATOR, "-")), Term(token=Token(TokenType.NUMBER, "0x200"))],
            ips_node.expression.tokens,
        )

    def test_parse_struct(self) -> None:
        input_program = """
        .struct a_struct {
            byte id
            word offset
            long pointer
        }"""

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        self.assertEqual([("struct", "a_struct", {"id": "byte", "offset": "word", "pointer": "long"})], ast.ast)
