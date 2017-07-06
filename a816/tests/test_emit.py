import unittest

import struct

from a816.program import Program
from a816.parse.nodes import NodeError
from cpu.cpu_65c816 import snes_to_rom
from parse.ast import code_gen
from tests import StubWriter


class EmitTest(unittest.TestCase):
    def test_emit_opcode_size_error(self):
        program = Program()
        nodes = program.parser.parse(
            'lda.l #0x123456'
        )

        self.assertEqual(len(nodes), 1)

        first_node = nodes[0]

        self.assertRaises(NodeError, lambda: first_node.emit(program.resolver))

    def test_short_jumps(self):
        input_program = '''
            my_label:
            lda #0x0000
            bra my_label
        '''

        program = Program()

        nodes = program.parser.parse(input_program)
        program.resolve_labels(nodes)

        program.resolver.pc = 3
        short_jump_node = nodes[-1]

        machine_code = short_jump_node.emit(program.resolver)

        unpacked = struct.unpack('Bb', machine_code)

        self.assertEqual(unpacked, (128, -5))

    def test_long_data_node(self):
        input_program = """
        .dl 0xf01ac5
        """

        program = Program()
        ast = program.parser.parse_as_ast(input_program)
        self.assertEqual(ast, ('block', ('dl', ('0xf01ac5',))))

        writer = StubWriter()

        nodes = code_gen(ast[1:], program.resolver)
        program.resolve_labels(nodes)

        program.emit(nodes, writer)

        self.assertEqual(writer.data[0], b'\xc5\x1a\xf0')

    def test_ateq(self):
        writer = StubWriter()

        program = Program()
        input_program = '''
        @=0x200000, 0x7e4000
        label:
        .pointer label
        '''
        program.assemble_string_with_emitter(input_program, 'test_macro_application', writer)

        self.assertEqual(writer.data[0], b'\x00\x40\x7e')
        self.assertEqual(writer.data_addresses[0], 0x200000)

    # FIXME: ateq should not support RAM reloc this by design is more for bank mapping
    # FIXME: should properly support 32k banks
    # def test_ateq_bank_boundaries(self):
    #     writer = StubWriter()
    #
    #     program = Program()
    #
    #     input_program = '''
    #     @=0x200000, 0x03ffff
    #     label:
    #     .pointer label
    #     label2:
    #     '''
    #     program.assemble_string_with_emitter(input_program, 'test_macro_application', writer)
    #
    #     self.assertEqual(writer.data[0], b'\x00\x40\x7e')
    #     self.assertEqual(writer.data_addresses[0], 0x200000)
    #     self.assertEqual(program.resolver.current_scope['label2'], 0x048002)

    def test_stareq(self):
        writer = StubWriter()

        program = Program()
        input_program = '''
        *=0x038000
        label:
        .pointer label
        '''
        program.assemble_string_with_emitter(input_program, 'test_macro_application', writer)

        self.assertEqual(writer.data[0], b'\x00\x80\x03')
        self.assertEqual(writer.data_addresses[0], snes_to_rom(0x038000))

    def test_stareq_bank_boundaries(self):
        writer = StubWriter()

        program = Program()
        input_program = '''
           *=0x03FFFF
           label:
           .pointer label
           label2:
           '''
        program.assemble_string_with_emitter(input_program, 'test_macro_application', writer)

        self.assertEqual(writer.data[0], b'\xFF\xFF\x03')
        self.assertEqual(writer.data_addresses[0], snes_to_rom(0x03FFFF))
        self.assertEqual(program.resolver.current_scope['label2'], 0x048002)

    def test_symbols_are_globals_in_current_scope(self):
        writer = StubWriter()

        program = Program()
        input_program = '''
         .macro test(pointer) {
            .pointer pointer
            }
            test(newgame.label)

           .scope newgame {
            label:
           }
           
           '''
        program.assemble_string_with_emitter(input_program, 'test_macro_application', writer)

        self.assertEqual(writer.data[0], b'\xFF\xFF\x03')
        self.assertEqual(writer.data_addresses[0], snes_to_rom(0x03FFFF))
        self.assertEqual(program.resolver.current_scope['label2'], 0x048002)
