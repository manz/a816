import unittest
import struct
import logging

from a816.parse.ast import code_gen
from a816.parse.lalrparser import LALRParser
from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.nodes import OpcodeNode, ValueNode, ExpressionNode
from a816.program import Program
from a816.symbols import Resolver

logger = logging.getLogger('a816')

stream_formatter = logging.Formatter('%(levelname)s :: %(message)s')
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(stream_formatter)

logger.addHandler(stream_handler)


# logger.setLevel(logging.WARNING)


class StubWriter(object):
    def __init__(self):
        self.data = []

    def begin(self):
        pass

    def write_block(self, block, block_address):
        self.data.append((block_address, block))

    def end(self):
        pass


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

    def test_data_word(self):
        input_program = '''{
            symbol=0x12345
            .dw 0x0000
            .dw 0x3450, 0x00, symbol & 0x00FF
            }
        '''

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)
        emitted_nodes = []
        for node in nodes:
            node_data = node.emit(program.resolver)
            if node_data:
                emitted_nodes.append(node_data)
        self.assertEqual(emitted_nodes, [b'\x00\x00', b'\x50\x34', b'\x00\x00', b'\x45\x00'])

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
            "lda.w #0x00",
            "a:",
            "}"
            "}"
        ]

        program = Program()
        program.parser = LALRParser(program.resolver)
        ast = program.parser.parse_as_ast('\n'.join(input_program))
        nodes = program.parser.parse('\n'.join(input_program))
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
    #                              ('opcode', AddressingMode.immediate, ['lda', 'b'], 'a'),
    #                              ('opcode', AddressingMode.immediate, ['lda', 'b'], 'b'),
    #                              ('opcode', AddressingMode.immediate, ['lda', 'b'], 'c')))),
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

    def test_macro_empty_args(self):
        input_program = """
                .macro test() {
                    sep #0x30
                }

                test()
                """

        program = Program()

        ast_nodes = program.parser.parse_as_ast(input_program)

        nodes = code_gen(ast_nodes[1:], program.resolver)

    def test_label_reference(self):
        resolver = Resolver()
        ref = ExpressionNode('0x00', resolver)

    def test_push_pull(self):
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
        ast = program.parser.parse_as_ast(input_program)
        nodes = code_gen(ast[1:], program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, StubWriter())
        print(nodes[-2])

    def test_macro_definition_should_parse(self):
        input_program = '''
        .macro waitforvblank(a) {
            pla
        }
        plb
        waitforvblank(0x123456)
        '''

        program = Program()
        ast = program.parser.parse_as_ast(input_program)

    def test_nini(self):
        input_program = '''.macro wait_for_vblank_inline() {
            pha
        negative:
            lda.l 0x004212
            bmi negative
        positive:
            lda.l 0x004212
            bpl positive
            pla
        }'''
        program = Program()
        ast = program.parser.parse_as_ast(input_program)

    def test_php_pha(self):
        input_program = '''
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
        }'''
        program = Program()
        ast = program.parser.parse_as_ast(input_program)

    def test_named_scopes(self):
        input_program = '''
                .scope newgame {
                    .db 0
                    .db 0

                    empty:


                }

                .dw newgame.empty'''

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        nodes = code_gen(ast[1:], program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()
        writer = StubWriter()
        program.emit(nodes, writer)
        self.assertEqual(writer.data[0], b'\x00\x00\x02\x80')

    def test_pluseq(self):
        input_program = '''
        
        @=0x9f8100,0x7F1001
        label:
        .dl label
        '''

        program = Program()
        ast = program.parser.parse_as_ast(input_program)

        print(ast)
        nodes = code_gen(ast[1:], program.resolver)
        program.resolve_labels(nodes)
        program.resolver.dump_symbol_map()
        writer = StubWriter()
        program.emit(nodes, writer)
        first_patch = writer.data[0]
        print(hex(first_patch[0]), first_patch[1])
        # def test_stareq_expr(self):
        #     input_program = '''
        #     sym=3
        #     *=sym+4
        #     coucou:
        #     .db 0x00
        #     '''
        #     program = Program()
        #     ast = program.parser.parse_as_ast(input_program)
        #
        #     print(ast)
        #     nodes = code_gen(ast[1:], program.resolver)
        #     program.resolve_labels(nodes)
        #     program.resolver.dump_symbol_map()
        #     writer = StubWriter()
        #     program.emit(nodes, writer)

    #     def test_module(self):
    #         input_program = '''.scope State {
    #     v_idx = 0
    #
    # __init__:
    #     self = 0x00
    #     phx
    #
    #     ldx v_idx
    #     plx
    #
    #     rts
    # }
    #
    # vinx = State.__init__ + 4
    #
    # *=vinx - 2
    # JSR.w State.__init__
    # labelle:
    #
    #         '''
    #         program = Program()
    #         ast = program.parser.parse_as_ast(input_program)
    #
    #         from pprint import pprint
    #         pprint(ast)
    #
    #         nodes = code_gen(ast[1:], program.resolver)
    #         program.resolve_labels(nodes)
    #         program.resolver.dump_symbol_map()
    #         writer = StubWriter()
    #         program.emit(nodes, writer)



    # def test_if_true(self):
    #     program = Program()
    #     program.resolver.current_scope.add_symbol('TEST', 1)
    #     ast = program.parser.parse_as_ast('''
    #         #if TEST
    #             .db 1
    #         #else
    #             .db 0
    #         #endif
    #     ''')
    #
    #     print(ast)
    #     nodes = code_gen(ast[1:], program.resolver)
    #
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()
    #     writer = StubWriter()
    #     program.emit(nodes, writer)
    #     self.assertEqual(writer.data[0], b'\x01')

    # def test_if_false(self):
    #     program = Program()
    #     program.resolver.current_scope.add_symbol('TEST', 0)
    #     ast = program.parser.parse_as_ast('''
    #         #if TEST
    #             .db 1
    #         #else
    #             .db 0
    #         #endif
    #     ''')
    #
    #     print(ast)
    #     nodes = code_gen(ast[1:], program.resolver)
    #
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()
    #     writer = StubWriter()
    #     program.emit(nodes, writer)
    #     self.assertEqual(writer.data[0], b'\x00')

    # def test_if_no_else(self):
    #     program = Program()
    #     program.resolver.current_scope.add_symbol('TEST', 0)
    #     ast = program.parser.parse_as_ast('''
    #         #if TEST
    #             .db 1
    #         #endif
    #     ''')
    #
    #     print(ast)
    #     nodes = code_gen(ast[1:], program.resolver)
    #
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()
    #     writer = StubWriter()
    #     program.emit(nodes, writer)
    #     self.assertEqual(len(writer.data), 0)

    # def test_if_no_else_true(self):
    #     program = Program()
    #     program.resolver.current_scope.add_symbol('TEST', 1)
    #     ast = program.parser.parse_as_ast('''
    #         #if TEST
    #             .db 1
    #         #endif
    #     ''')
    #
    #     print(ast)
    #     nodes = code_gen(ast[1:], program.resolver)
    #
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()
    #     writer = StubWriter()
    #     program.emit(nodes, writer)
    #     self.assertEqual(writer.data[0], b'\x01')

    # def test_for(self):
    #     program = Program()
    #     ast = program.parser.parse_as_ast('''
    #         .macro vwf_char(read_base_address, write_base_address) {
    #             write_base_address_bank = write_base_address >> 16
    #             write_base_address_low = write_base_address & 0xFFFF
    #
    #             phb
    #             pha
    #             lda.b #write_base_address_bank
    #             pha
    #             plb
    #             pla
    #
    #             sep #0x20
    #             #for k 0 16
    #             {
    #                 lda read_base_address + k, x
    #                 sta 0x4016
    #                 nop
    #                 nop
    #                 lda 0x4018
    #                 ora.w write_base_address_low + k, y
    #                 sta.w write_base_address_low + k, y
    #                 lsr
    #                 sta.w write_base_address_low + k, y
    #             }
    #             lda read_base_address + 16, x
    #             clc
    #             adc 0x00
    #             sta 0x00
    #             rep #0x20
    #             plb
    #
    #         }
    #
    #         vwf_char(0xF00000, 0x7FC000)
    #
    #
    #     ''')
    #
    #     print(ast)
    #     nodes = code_gen(ast[1:], program.resolver)
    #
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()
    #     writer = StubWriter()
    #     program.emit(nodes, writer)
    #     self.assertEqual(writer.data[0], b'\xA9\x00\xA9\x01\xA9\x02\xA9\x03\xA9\x04')
    #
    # def test_for_with_expressions(self):
    #     program = Program()
    #     ast = program.parser.parse_as_ast('''{
    #         _from=3
    #         #for k _from _from + 2 {
    #             .db k
    #         }
    #     }
    #     ''')
    #
    #     print(ast)
    #     nodes = code_gen(ast[1:], program.resolver)
    #
    #     program.resolve_labels(nodes)
    #     program.resolver.dump_symbol_map()
    #     writer = StubWriter()
    #     program.emit(nodes, writer)
    #     self.assertEqual(writer.data[0], b'\x03\x04\x05')

# def test_code_gen_error(self):
#         program = Program()
#         ast = program.parser.parse_as_ast(
#             '''letter_width_table = 0xe00000
# font_addr = 0xff0000
#
# ; if item_descriptions are moved it should be modified
# ;item_descriptions = 0xD72FD1
#
# copy_char = 0xC80C3C
# lda.l #0xffff
# ; repurpose odd / even char flag to store the occupied bits
# ;position = 0x2e''')
#         try:
#             nodes = code_gen(ast[1:], program.resolver)
#             program.resolve_labels(nodes)
#             # program.resolver.dump_symbol_map()
#             writer = StubWriter()
#             program.emit(nodes, writer)
#         except NodeError as e:
#
#             logger.error('"{message}" at \n{file}:{line} {data}'.format(
#                 message=e.message,
#                 file=e.file_info[0] if e.file_info[0] else 'input',
#                 line=e.file_info[1],
#                 data=e.file_info[2]))
#
#     def test_tildaeq(self):
#         program = Program()
#         ast = program.parser.parse_as_ast('@=0x7e0000, 0xc00000')
#         self.assertEqual(ast,
#                           ('block', ('tildaeq', '0x7e0000', '0xc00000', ('fileinfo', '', 1, '@=0x7e0000, 0xc00000'))))
