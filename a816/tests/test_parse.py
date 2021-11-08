import os
import unittest
import struct
import logging
from typing import List, Tuple

from a816.parse.codegen import code_gen
from a816.parse.mzparser import MZParser as LALRParser, MZParser
from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.nodes import OpcodeNode, ValueNode, ExpressionNode, ScopeNode, PopScopeNode
from a816.program import Program
from a816.symbols import Resolver
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
        pass

    def write_block(self, block: bytes, block_address: int) -> None:
        self.data.append((block_address, block))

    def end(self) -> None:
        pass


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

        # program.resolver.dump_symbol_map()
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
        program.parser = LALRParser(program.resolver)
        ast = program.parser.parse_as_ast(input_program)
        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

    # def test_macro(self):
    #     input_program = '''
    #     .macro test_macro(a, b, c) {
    #         lda.b #a
    #         lda.b #b
    #         lda.b #c
    #     }
    #     test_macro(0, 1, 2)
    #     '''
    #
    #     expected_ast_nodes = ('block',
    #                           ('macro', 'test_macro', ('args', ('a', 'b', 'c')),
    #                            ('compound',
    #                             ('block',
    #                              ('opcode_def', AddressingMode.immediate, ['lda', 'b'], 'a'),
    #                              ('opcode_def', AddressingMode.immediate, ['lda', 'b'], 'b'),
    #                              ('opcode_def', AddressingMode.immediate, ['lda', 'b'], 'c')))),
    #                           ('macro_apply', 'test_macro', ('apply_args', ('0', '1', '2'))))
    #
    #     program = Program()
    #
    #     ast_nodes = program.parser.parse_as_ast(input_program)
    #
    #     self.assertEqual(ast_nodes, expected_ast_nodes)
    #
    #     nodes = code_gen(ast_nodes[1:], program.resolver)
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()

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

    # def test_label_reference(self) -> None:
    #     resolver = Resolver()
    #     ref = ExpressionNode("0x00", resolver, file_info=None)

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
